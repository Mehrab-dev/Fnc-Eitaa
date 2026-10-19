"""Edit/delete/forward/copy account messages. Offline by default; confirm each write."""
import argparse
import asyncio
import json
import secrets

from fnceitaa import (AccountClient, AccountRPCError, AuthenticationError, FncEitaaError,
                      MessageOperationCancelledError, MessageOperationUncertainError,
                      PeerKind)
from fnceitaa.account_operations import validate_ids, validate_text
from .account_messages import show_message


async def run(args):
    async with AccountClient(args.session) as client:
        if not client.is_authorized:
            raise AuthenticationError('No saved session. Run examples.account_login --live locally first.')
        if args.load_dialogs:
            await client.get_dialogs(limit=1000)
        destination = await client.resolve_peer(args.peer)
        source = await client.resolve_peer(args.from_peer) if args.command in ('forward', 'copy') else destination
        if args.command == 'delete' and destination.kind == PeerKind.CHANNEL and not args.revoke:
            raise ValueError('Channel deletion is shared; add --revoke explicitly')
        originals = []
        for mid in args.message_id:
            message = await client.get_message(source, mid)
            if message is None:
                raise ValueError('A selected message is unavailable in the source conversation')
            originals.append(message)
        text = None
        if args.command == 'edit':
            text = input('New text (one line; entered locally): ')
            validate_text(text)
        tokens = [secrets.randbits(63) or 1 for _ in args.message_id] if args.command in ('forward', 'copy') else []
        print('Operation:', args.command)
        print(f'Source: {source.kind.value}:{source.id}')
        print(f'Destination: {destination.kind.value}:{destination.id}')
        for message in originals:
            show_message(message, show_text=args.show_text)
        if text is not None:
            print('New text:', json.dumps(text, ensure_ascii=False))
        if args.command == 'delete':
            print('Deletion scope:', 'shared / revoke requested' if args.revoke else 'local deletion requested')
        if tokens:
            print('random_ids:', tokens)
            print('Copy without author requested:', args.command == 'copy')
        if input('Perform exactly this operation on these messages? [yes/NO]: ').strip().lower() != 'yes':
            print('Cancelled. No mutation sent.')
            return
        if args.command == 'edit':
            if text is None:
                raise ValueError('New text is missing')
            result = await client.edit_message(destination, args.message_id[0], text, no_webpage=True)
            print(f'Edit confirmed; message_id={result.id}')
        elif args.command == 'delete':
            result = await client.delete_messages(destination, args.message_id, revoke=args.revoke)
            print(f'Delete RPC acknowledged; message_ids={result.message_ids}; revoke={result.revoke}')
            print('Read history separately to verify the resulting state.')
        elif args.command == 'forward':
            results = await client.forward_messages(destination, source, args.message_id, random_ids=tokens)
            for result in results:
                print(f'Forward confirmed; source_id={result.source_message_id}; '
                      f'message_id={result.id}; random_id={result.random_id}')
        else:
            result = await client.copy_message(destination, source, args.message_id[0], random_id=tokens[0])
            print(f'Copy RPC confirmed; source_id={result.source_message_id}; '
                  f'message_id={result.id}; random_id={result.random_id}')
            print('Check the destination to verify author attribution.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('edit', 'delete', 'forward', 'copy'), default='edit')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--session', default='account.session')
    parser.add_argument('--peer', default='self', help='Destination; quote "@username" in PowerShell')
    parser.add_argument('--from-peer', default='self', help='Source for forward/copy')
    parser.add_argument('--message-id', type=int, nargs='+', help='One ID, or up to 100 IDs for delete/forward')
    parser.add_argument('--load-dialogs', action='store_true')
    parser.add_argument('--revoke', action='store_true', help='Request shared deletion; required for channels')
    parser.add_argument('--show-text', action='store_true')
    args = parser.parse_args()
    if args.revoke and args.command != 'delete':
        parser.error('--revoke is only valid with delete')
    if args.message_id is not None:
        try:
            validate_ids(args.message_id)
        except ValueError as exc:
            parser.error(str(exc))
        if args.command in ('edit', 'copy') and len(args.message_id) != 1:
            parser.error('edit/copy requires exactly one --message-id')
    elif args.live:
        parser.error('--live requires --message-id')
    if not args.live:
        print('Offline mode. No session opened, prompt shown or request sent.')
        print('Example: python -m examples.account_actions edit --live --peer self --message-id YOUR_MESSAGE_ID')
        return
    try:
        asyncio.run(run(args))
    except (MessageOperationCancelledError, MessageOperationUncertainError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\nCheck source/destination before any manual retry.\n')
    except AccountRPCError as exc:
        if exc.retry_after is not None:
            print(f'Eitaa requested a wait of {exc.retry_after} seconds. No automatic retry sent.')
        parser.exit(1, f'AccountRPCError: {exc}\n')
    except (FncEitaaError, ValueError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, 'Interrupted. An operation in progress may have an uncertain outcome.\n')


if __name__ == '__main__':
    main()
