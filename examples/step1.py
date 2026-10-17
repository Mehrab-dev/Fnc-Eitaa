"""Run from the repository root: python -m examples.step1 --help."""
import argparse
import asyncio
import os

from fnceitaa import Client, FncEitaaError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Fnc-Eitaa: public reading and token sending')
    commands = parser.add_subparsers(dest='command', required=True)
    info = commands.add_parser('info', help='Read a public profile/channel; no token needed')
    info.add_argument('peer', help='Public username, e.g. eitaa')
    message = commands.add_parser('message', help='Read one public message; no token needed')
    message.add_argument('peer')
    message.add_argument('message_id', type=int)
    send = commands.add_parser('send', help='Send text using EITAAYAR_TOKEN')
    send.add_argument('peer')
    send.add_argument('text')
    send.add_argument('--reply-to', type=int)
    file = commands.add_parser('file', help='Upload a local file using EITAAYAR_TOKEN')
    file.add_argument('peer')
    file.add_argument('path')
    file.add_argument('--caption')
    file.add_argument('--reply-to', type=int)
    return parser


async def run(args: argparse.Namespace) -> None:
    token = None
    if args.command in ('send', 'file'):
        token = os.environ.get('EITAAYAR_TOKEN', '').strip()
        if not token:
            raise ValueError('Set EITAAYAR_TOKEN in your environment before sending')
    async with Client(token=token) as client:
        if args.command == 'info':
            info = await client.get_chat_info(args.peer)
            for key, value in info.items():
                print(f'{key}: {value}')
        elif args.command == 'message':
            message = await client.get_message(args.peer, args.message_id)
            if message is None:
                print('Message not found on the public page')
            else:
                print(f'ID: {message.id}')
                print(message.text or '')
                if message.media:
                    print(f'Media: {message.media.file_path_or_id}')
        else:
            # Numeric destinations are chat IDs; other destinations are usernames.
            peer = int(args.peer) if args.peer.lstrip('-').isdigit() else args.peer
            if args.command == 'send':
                message = await client.send_message(peer, args.text, args.reply_to)
            else:
                message = await client.send_file(peer, args.path, args.caption, args.reply_to)
            print(f'Server confirmed message ID: {message.id}')


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (FncEitaaError, ValueError, OSError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
