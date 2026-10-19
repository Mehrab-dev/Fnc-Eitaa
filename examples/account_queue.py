"""Persistent account queue. Offline by default; enqueue and transmit are separate."""
import argparse
import asyncio
import json

from fnceitaa import AccountClient, FncEitaaError


def show_job(job, *, preview=False):
    print(f'job_id={job.id}; operation={job.operation}; state={job.state}; attempts={job.attempts}')
    if job.receipt is not None:
        print('Receipt:', json.dumps(job.receipt))
    if job.error:
        print('Last error type:', job.error)
    if job.state == 'failed':
        print('Retry not before (Unix seconds):', job.not_before)
    if preview:
        data = job.request
        for key in ('peer', 'from_peer'):
            if key in data:
                print(f'{key}: {data[key]["kind"]}:{data[key]["id"]}')
        for key in ('text', 'message_id', 'message_ids', 'reply_to_msg_id', 'revoke', 'silent', 'no_webpage'):
            if key in data:
                print(f'{key}:', json.dumps(data[key], ensure_ascii=False))
        if 'file' in data:
            print('File:', json.dumps(data['file']['path'], ensure_ascii=False))
            print('Media options:', json.dumps(data['options'], ensure_ascii=False))
        for item in data.get('items', []):
            print('Album file:', json.dumps(item['file']['path'], ensure_ascii=False))
            print('Media options:', json.dumps(item['options'], ensure_ascii=False))


async def run(args):
    async with AccountClient(args.session) as client:
        if args.load_dialogs:
            await client.get_dialogs(limit=1000)
        with client.queue(args.database) as queue:
            if args.command == 'enqueue':
                peer = await client.resolve_peer(args.peer)
                text = input('Message text (saved locally in SQLite): ')
                print(f'Destination: {peer.kind.value}:{peer.id}')
                print('Text:', json.dumps(text, ensure_ascii=False))
                print('Idempotency key:', json.dumps(args.key))
                if input('Save this send in the local outbox for later dispatch? [yes/NO]: ').strip().lower() != 'yes':
                    print('Cancelled. Nothing enqueued.')
                    return
                job = await queue.enqueue_message(peer, text, key=args.key)
                show_job(job)
                print('Saved in SQLite. No message sent. Reopen status to verify persistence.')
            elif args.command == 'status':
                print('Queue counts:', json.dumps(queue.store.stats(), sort_keys=True))
                for job in queue.store.list_jobs(limit=args.limit):
                    show_job(job)
                print('Local queue read. No queued operation dispatched.')
            elif args.command == 'send':
                job = queue.store.get_job(args.job_id)
                show_job(job, preview=True)
                if job.state != 'pending':
                    print('Only a pending job can be dispatched. No request sent.')
                    return
                if input('Dispatch exactly this queued operation once? [yes/NO]: ').strip().lower() != 'yes':
                    print('Cancelled. Job remains pending.')
                    return
                result = await queue.dispatch_one(job.id)
                if result is None:
                    print('Job is claimed, not pending, or its wait has not elapsed. No dispatch started here.')
                else:
                    show_job(result)
            elif args.command == 'recover':
                print('Expired claims recovered:', json.dumps(queue.store.recover_expired()))
                print('Uncertain outbox jobs were not requeued or transmitted.')
            elif args.command == 'retry':
                show_job(queue.store.retry_failed(args.job_id))
                print('Explicitly requeued a failed job. No message sent.')
            elif args.command == 'inbox':
                for item in queue.store.list_inbox(limit=args.limit):
                    print(f'event_id={item.event.event_id}; kind={item.event.kind}; state={item.state}; attempts={item.attempts}')
                    if args.show_text and item.event.message is not None:
                        print('Text:', json.dumps(item.event.message.text, ensure_ascii=False))
            elif args.command == 'ack':
                item = queue.store.claim_inbox(args.event_id)
                if item is None:
                    print('No pending inbox event with this ID.')
                    return
                print(f'event_id={item.event.event_id}; kind={item.event.kind}')
                if args.show_text and item.event.message is not None:
                    print('Text:', json.dumps(item.event.message.text, ensure_ascii=False))
                success = input('Mark this local inbox event processed? [yes/NO]: ').strip().lower() == 'yes'
                queue.store.finish_inbox(item, success=success)
                print('Inbox event marked done.' if success else 'Inbox event returned to pending.')
            else:
                if args.import_checkpoint:
                    await queue.import_checkpoint(args.import_checkpoint)
                await queue.updates.initialize()
                for peer in args.watch:
                    await queue.updates.watch_channel(peer)
                print('SQLite inbox ready. Fresh checkpoints start now; saved SQLite checkpoints resume.')
                print('Events and checkpoint commit together. Press Ctrl+C to stop.')
                replay = args.replay_pts
                while True:
                    batch = await queue.poll_once(replay_pts=replay)
                    replay = 0
                    print(f'Captured batch: events={len(batch.events)}; has_more={batch.has_more}')
                    for event in batch.events:
                        print(f'{event.kind}; event_id={event.event_id}; stored=True')
                        if args.show_text and event.message is not None:
                            print('Text:', json.dumps(event.message.text, ensure_ascii=False))
                    if args.once:
                        return
                    if not batch.has_more:
                        await asyncio.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('enqueue', 'status', 'send', 'poll', 'inbox', 'ack', 'recover', 'retry'), default='status')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--session', default='account.session')
    parser.add_argument('--database', help='Default: SESSION.queue.sqlite3')
    parser.add_argument('--peer', default='self')
    parser.add_argument('--load-dialogs', action='store_true')
    parser.add_argument('--key', help='Stable application key for deduplicating enqueue')
    parser.add_argument('--job-id')
    parser.add_argument('--event-id')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--watch', action='append', default=[])
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--replay-pts', type=int, default=0)
    parser.add_argument('--import-checkpoint')
    parser.add_argument('--show-text', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000 or not 1 <= args.interval <= 60 or not 0 <= args.replay_pts <= 1000:
        parser.error('Use --limit 1..10000, --interval 1..60 and --replay-pts 0..1000')
    if not args.live:
        print('Offline mode. No session, database, prompt or network request opened.')
        print('First test: python -m examples.account_queue enqueue --live --peer self --key phase10-test-1')
        return
    if args.command in ('send', 'retry') and not args.job_id:
        parser.error('send/retry requires --job-id')
    if args.command == 'ack' and not args.event_id:
        parser.error('ack requires --event-id')
    try:
        asyncio.run(run(args))
    except (FncEitaaError, ValueError, OSError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, 'Interrupted. Check queue status; possibly transmitted jobs are never retried automatically.\n')


if __name__ == '__main__':
    main()
