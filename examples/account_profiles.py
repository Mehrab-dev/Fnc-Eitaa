"""Read full account profiles and permitted members; offline by default, no OTP."""
import argparse
import asyncio
import json

from fnceitaa import AccountClient, AccountUserFull, AccountRPCError, FncEitaaError


def show_member(member, show_names=False):
    print(f'{member.peer.kind.value}:{member.peer.id}; role={member.role}; is_member={member.is_member}')
    if show_names:
        print('Name:', json.dumps(member.peer.title, ensure_ascii=False))


async def run(args):
    async with AccountClient(args.session) as client:
        if args.load_dialogs:
            await client.get_dialogs(limit=1000)
        if args.command == 'members':
            count = 0
            async for member in client.iter_members(args.peer, limit=args.limit, page_size=args.page_size,
                                                    filter=args.filter, query=args.query):
                show_member(member, args.show_names)
                count += 1
            print(f'Read {count} visible participants. No membership changed.')
            return
        if args.command == 'member':
            member = await client.get_member(args.peer, args.participant)
            if member is None:
                print('Not a participant in the accessible membership result.')
            else:
                show_member(member, args.show_names)
            return
        info = await client.get_chat_info(args.peer)
        print(f'Peer: {info.peer.kind.value}:{info.peer.id}; megagroup={info.peer.is_megagroup}')
        if args.show_names:
            print('Name:', json.dumps(info.peer.title, ensure_ascii=False))
            print('Username:', json.dumps(info.peer.username, ensure_ascii=False))
        if args.show_about:
            print('About:', json.dumps(info.about, ensure_ascii=False))
        if isinstance(info, AccountUserFull):
            print(f'Full user: common_chats={info.common_chats_count}; blocked={info.blocked}; '
                  f'phone_calls_available={info.phone_calls_available}; video_calls_available={info.video_calls_available}')
        else:
            print(f'Full chat: member_count={info.member_count}; admin_count={info.admin_count}; '
                  f'online_count={info.online_count}; can_view_participants={info.can_view_participants}')
            print(f'pinned_message_id={info.pinned_message_id}; linked_chat_id={info.linked_chat_id}; '
                  f'slowmode_seconds={info.slowmode_seconds}')
        print('Read-only request completed. No OTP requested or account settings changed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('info', 'members', 'member'), default='info')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--session', default='account.session')
    parser.add_argument('--peer', default='self', help='Quote "@username" in PowerShell')
    parser.add_argument('--participant', default='self', help='For member: self, username, cached typed ID')
    parser.add_argument('--load-dialogs', action='store_true')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--filter', choices=('recent', 'admins', 'bots', 'search', 'banned', 'kicked'), default='recent')
    parser.add_argument('--query', default='')
    parser.add_argument('--show-names', action='store_true')
    parser.add_argument('--show-about', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000 or not 1 <= args.page_size <= 200:
        parser.error('Use --limit 1..10000 and --page-size 1..200')
    if not args.live:
        print('Offline mode. No session opened, prompt shown or network request sent.')
        print('First test: python -m examples.account_profiles info --live --peer self')
        return
    try:
        asyncio.run(run(args))
    except AccountRPCError as exc:
        if exc.retry_after is not None:
            print(f'Eitaa requested a wait of {exc.retry_after} seconds. No automatic retry sent.')
        parser.exit(1, f'AccountRPCError: {exc}\n')
    except (FncEitaaError, ValueError, OSError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, 'Read interrupted.\n')


if __name__ == '__main__':
    main()
