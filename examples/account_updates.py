"""Receive account new/edit/delete events. Read-only; never requests an OTP."""
import argparse
import asyncio
import json
import logging

from fnceitaa import AccountClient, Router, FncEitaaError


async def run(args):
    async with AccountClient(args.session) as client:
        updates = client.updates(args.checkpoint)
        await updates.initialize()
        if not args.common_only:
            await updates.watch_dialogs()
        for peer in args.channel:
            await updates.watch_channel(peer)
        print('Updates ready. Fresh checkpoints start now; saved checkpoints resume.')
        print('Listening for new/edit/delete. Press Ctrl+C to stop.', flush=True)
        router = Router()

        async def show(event):
            peer = f'{event.peer.kind.value}:{event.peer.id}' if event.peer else 'unknown'
            print(f'{event.kind}; peer={peer}; ids={event.message_ids}; event_id={event.event_id}', flush=True)
            if args.show_text and event.message is not None:
                print('Text:', json.dumps(event.message.text, ensure_ascii=False), flush=True)

        for kind in ('new', 'edit', 'delete'):
            router.on(kind)(show)
        if args.once:
            batch = await updates.poll(replay_pts=args.replay_pts)
            for event in batch.events:
                await router.emit(event.kind, event)
            if args.diagnose:
                print(f'Diagnostic batch; events={len(batch.events)}; has_more={batch.has_more}; checkpoint not advanced')
            else:
                await updates.ack(batch)
                print(f'Batch acknowledged; events={len(batch.events)}; has_more={batch.has_more}')
        else:
            await updates.run_polling(router, interval=args.interval, replay_pts=args.replay_pts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--session', default='account.session')
    parser.add_argument('--checkpoint')
    parser.add_argument('--once', action='store_true', help='Read and acknowledge one batch only')
    parser.add_argument('--common-only', action='store_true', help='Skip channel discovery; common chats still work')
    parser.add_argument('--channel', action='append', default=[], help='Watch a channel username; quote "@username" in PowerShell')
    parser.add_argument('--show-text', action='store_true')
    parser.add_argument('--diagnose', action='store_true', help='Print structural diagnostics; with --once do not acknowledge the batch')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--replay-pts', type=int, default=0,
                        help='Explicitly overlap the first common difference by 0..1000 positions for recovery')
    args = parser.parse_args()
    if not 1 <= args.interval <= 3600:
        parser.error('--interval must be between 1 and 3600 seconds')
    if not 0 <= args.replay_pts <= 1000:
        parser.error('--replay-pts must be between 0 and 1000')
    if not args.live:
        print('Offline mode. No session opened and no network request sent.')
        print('Listen: python -m examples.account_updates --live --show-text')
        return
    if args.diagnose:
        logging.basicConfig(level=logging.WARNING, format='%(message)s')
        logging.getLogger('fnceitaa.account_updates').setLevel(logging.INFO)
        logging.getLogger('fnceitaa.account').setLevel(logging.INFO)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('Stopped. Last acknowledged checkpoint is preserved.')
    except (FncEitaaError, ValueError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
