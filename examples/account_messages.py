"""Account text/history example. Offline by default; sending needs local consent."""
import argparse
import asyncio
import json
import secrets

from fnceitaa import (AccountClient, AccountRPCError, AuthenticationError, FncEitaaError,
                      SendMessageCancelledError, SendMessageUncertainError)


def show_message(message, *, show_text=False):
    print(f'id={message.id}; peer={message.peer.kind.value}:{message.peer.id}; '
          f'date={message.date}; outgoing={message.outgoing}; '
          f'reply_to={message.reply_to_msg_id}; service={message.is_service}; media={message.has_media}')
    if show_text:
        print('Text:', json.dumps(message.text, ensure_ascii=False))
    if message.media is not None:
        print(f'Media: kind={message.media.kind.value}; size={message.media.size}; grouped_id={message.grouped_id}')


async def run(args):
    async with AccountClient(args.session) as client:
        if not client.is_authorized:
            raise AuthenticationError('No saved session. First run python -m examples.account_login --live locally.')
        if args.load_dialogs:
            await client.get_dialogs(limit=1000)
        peer = await client.resolve_peer(args.peer)
        if args.command == 'history':
            count = 0
            async for message in client.iter_history(peer, limit=args.limit, page_size=args.page_size):
                show_message(message, show_text=args.show_text)
                count += 1
            print(f'Read {count} messages. No messages sent or marked read.')
        elif args.command == 'read':
            message = await client.get_message(peer, args.message_id)
            if message is None:
                print('Message unavailable in this conversation.')
            else:
                show_message(message, show_text=args.show_text)
        else:
            text = input('Message text (one line; entered locally): ')
            identifier = secrets.randbits(63) or 1
            print(f'Destination: {peer.kind.value}:{peer.id}')
            print('Title:', json.dumps(peer.title, ensure_ascii=False))
            print('Text:', json.dumps(text, ensure_ascii=False))
            print(f'Reply to: {args.reply_to}; random_id={identifier}')
            if input('Send exactly this one message to this destination? [yes/NO]: ').strip().lower() != 'yes':
                print('Cancelled. No message sent.')
                return
            sent = await client.send_message(peer, text, reply_to_msg_id=args.reply_to,
                                             no_webpage=True, random_id=identifier)
            print(f'Send confirmed; message_id={sent.id}; random_id={sent.random_id}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('history', 'read', 'send'), default='history')
    parser.add_argument('--live', action='store_true', help='Enable requests using the existing account session')
    parser.add_argument('--session', default='account.eitaa')
    parser.add_argument('--peer', default='self', help='Username, self or an account peer ID')
    parser.add_argument('--load-dialogs', action='store_true', help='Cache up to 1000 dialogs before resolving a numeric peer')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--message-id', type=int, help='Message ID for the read command')
    parser.add_argument('--reply-to', type=int, help='Reply to this ID in the same destination')
    parser.add_argument('--show-text', action='store_true', help='Display message text locally')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000 or not 1 <= args.page_size <= 100:
        parser.error('Use --limit 1..10000 and --page-size 1..100')
    if args.command == 'read' and (args.message_id is None or not 0 < args.message_id < 2**31):
        parser.error('read requires --message-id with a positive signed 32-bit ID')
    if args.reply_to is not None and (args.command != 'send' or not 0 < args.reply_to < 2**31):
        parser.error('--reply-to requires send and a positive signed 32-bit ID')
    if not args.live:
        print('Offline mode. No session opened and no network request sent.')
        print('Read history: python -m examples.account_messages history --live --peer "@eitaa"')
        print('Send to yourself with confirmation: python -m examples.account_messages send --live --peer self')
        return
    try:
        asyncio.run(run(args))
    except SendMessageCancelledError as exc:
        parser.exit(1, f'{exc}\nCheck the destination before retrying; preserve this random_id.\n')
    except SendMessageUncertainError as exc:
        parser.exit(1, f'{exc}\nCheck the destination before retrying; preserve this random_id.\n')
    except AccountRPCError as exc:
        if exc.retry_after is not None:
            print(f'Eitaa requested a wait of {exc.retry_after} seconds. No automatic retry sent.')
        parser.exit(1, f'AccountRPCError: {exc}\n')
    except (FncEitaaError, ValueError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, 'Operation interrupted. If a send was in progress, its outcome may be uncertain.\n')


if __name__ == '__main__':
    main()
