"""Read account dialogs/identities using an existing session; never request OTP."""
import argparse
import asyncio
import json

from fnceitaa import AccountClient, AccountRPCError, AuthenticationError, FncEitaaError


async def inspect_account(args):
    async with AccountClient(args.session) as client:
        if not client.is_authorized:
            raise AuthenticationError('No saved session. First run python -m examples.account_login --live locally.')
        if args.peer:
            peer = await client.resolve_peer(args.peer)
            print(f'Resolved: {peer.kind.value}:{peer.id}; addressable=True; megagroup={peer.is_megagroup}')
            if args.show_titles:
                print('Title:', json.dumps(peer.title, ensure_ascii=False))
            return
        folder = {'all': None, 'main': 0, 'archive': 1}[args.folder]
        count = 0
        async for dialog in client.iter_dialogs(limit=args.limit, page_size=args.page_size, folder_id=folder):
            peer = dialog.peer
            print(f'{peer.kind.value}:{peer.id}; unread={dialog.unread_count}; '
                  f'pinned={dialog.pinned}; folder={dialog.folder_id}; megagroup={peer.is_megagroup}')
            if args.show_titles:
                print('Title:', json.dumps(peer.title, ensure_ascii=False))
            count += 1
        print(f'Read {count} dialogs. Session connected; no OTP requested.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Enable read-only requests with the saved session')
    parser.add_argument('--session', default='account.eitaa')
    parser.add_argument('--limit', type=int, default=20, help='Maximum number of dialogs (default: 20)')
    parser.add_argument('--page-size', type=int, default=100, help='Requested page size (1..100)')
    parser.add_argument('--folder', choices=('all', 'main', 'archive'), default='all')
    parser.add_argument('--peer', help='Resolve a username, self, or explicit chat:ID')
    parser.add_argument('--show-titles', action='store_true', help='Also display names/titles in your local terminal')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000 or not 1 <= args.page_size <= 100:
        parser.error('Use --limit 1..10000 and --page-size 1..100')
    if not args.live:
        print('Offline mode. No session opened and no network request sent.')
        print('Read dialogs: python -m examples.account_dialogs --live')
        print('Resolve a public username: python -m examples.account_dialogs --live --peer "@eitaa"')
        return
    try:
        asyncio.run(inspect_account(args))
    except AccountRPCError as exc:
        if exc.retry_after is not None:
            print(f'Eitaa requested a wait of {exc.retry_after} seconds. No automatic retry sent.')
        parser.exit(1, f'AccountRPCError: {exc}\n')
    except (FncEitaaError, ValueError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except KeyboardInterrupt:
        parser.exit(1, 'Read interrupted.\n')


if __name__ == '__main__':
    main()
