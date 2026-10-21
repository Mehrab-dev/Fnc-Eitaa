"""Account media example. Offline by default; uploads/sends/downloads need local confirmation."""
import argparse
import asyncio
import json
from pathlib import Path
import secrets

from fnceitaa import (AccountClient, FncEitaaError, MediaItem, MediaKind,
                      MessageOperationCancelledError, UploadCancelledError)


async def run(args):
    async with AccountClient(args.session) as client:
        if args.load_dialogs:
            await client.get_dialogs(limit=1000)
        peer = await client.resolve_peer(args.peer)
        print(f'Destination: {peer.kind.value}:{peer.id}')
        if args.command == 'download':
            message = await client.get_message(peer, args.message_id)
            if message is None or message.media is None:
                raise ValueError('No supported downloadable media on this message')
            print(f'Message: {message.id}; kind={message.media.kind.value}; size={message.media.size}')
            print('Output:', json.dumps(str(Path(args.output).absolute()), ensure_ascii=False))
            print('Overwrite existing destination:', args.overwrite)
            if input('Download this media to this path? [yes/NO]: ').strip().lower() != 'yes':
                print('Cancelled. No file downloaded.')
                return
            result = await client.download_media(peer, args.message_id, args.output, overwrite=args.overwrite)
            print(f'Download complete; bytes={result.size}; sha256={result.sha256}')
            return
        media = client.media()
        kind = {'send': MediaKind.DOCUMENT, 'photo': MediaKind.PHOTO, 'video': MediaKind.VIDEO,
                'voice': MediaKind.VOICE, 'album': MediaKind(args.kind)}[args.command]
        media._send_options(args.caption, False, args.reply_to)
        for path in args.file:
            with media._open(Path(path)) as source:
                media._media_options(source, kind, args.mime_type, args.duration, args.width, args.height)
                print('File:', json.dumps(source.name, ensure_ascii=False), f'; bytes={source.size}; kind={kind.value}')
        tokens = [secrets.randbits(63) or 1 for _ in args.file]
        print('Caption:', json.dumps(args.caption, ensure_ascii=False))
        print('Reply to:', args.reply_to)
        print('random_ids:', tokens)
        if input('Upload and send exactly these files to this destination? [yes/NO]: ').strip().lower() != 'yes':
            print('Cancelled. No file uploaded or message sent.')
            return
        if args.command == 'album':
            items = [MediaItem(Path(path), kind=kind, caption=args.caption if index == 0 else '',
                               mime_type=args.mime_type, duration=args.duration, width=args.width, height=args.height)
                     for index, path in enumerate(args.file)]
            results = await client.send_album(peer, items, random_ids=tokens, reply_to_msg_id=args.reply_to)
        else:
            result = await client.send_file(peer, Path(args.file[0]), kind=kind, caption=args.caption,
                mime_type=args.mime_type, duration=args.duration, width=args.width, height=args.height,
                random_id=tokens[0], reply_to_msg_id=args.reply_to)
            results = (result,)
        for message in results:
            print(f'Media send confirmed; message_id={message.id}; random_id={message.random_id}; grouped_id={message.grouped_id}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('send', 'photo', 'video', 'voice', 'album', 'download'), default='send')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--session', default='account.eitaa')
    parser.add_argument('--peer', default='self', help='Quote "@username" in PowerShell')
    parser.add_argument('--load-dialogs', action='store_true')
    parser.add_argument('--file', nargs='+')
    parser.add_argument('--caption', default='')
    parser.add_argument('--reply-to', type=int)
    parser.add_argument('--kind', choices=('photo', 'video', 'document'), default='photo', help='Album item kind')
    parser.add_argument('--mime-type')
    parser.add_argument('--duration', type=int, help='Actual voice/video duration in seconds')
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    parser.add_argument('--message-id', type=int)
    parser.add_argument('--output')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if not args.live:
        print('Offline mode. No session opened, prompt shown or request sent.')
        print('First test: python -m examples.account_media send --live --peer self --file examples/media_sample.txt')
        return
    if args.command == 'download':
        if args.message_id is None or not 0 < args.message_id < 2**31 or not args.output:
            parser.error('download requires positive --message-id and --output')
    elif not args.file or (args.command == 'album' and not 2 <= len(args.file) <= 10) or (args.command != 'album' and len(args.file) != 1):
        parser.error('Use one --file, or 2..10 files with album')
    try:
        asyncio.run(run(args))
    except (MessageOperationCancelledError, UploadCancelledError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (FncEitaaError, ValueError, OSError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, 'Interrupted. If a message-send RPC had started, check the destination before retrying.\n')


if __name__ == '__main__':
    main()
