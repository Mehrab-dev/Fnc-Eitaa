"""Bounded account media transfers using the pinned Eitaa web protocol."""
import asyncio
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
import hashlib
import inspect
import io
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from typing import Any, BinaryIO

from .account import AccountHTTPTransport
from .account_files import DownloadedFile, MediaItem, UploadedFile, parse_media
from .account_messages import correlate_sent, message_id
from .account_operations import operation_random_ids, _updates
from .account_peers import peer_key
from .enums import MediaKind
from .errors import (AccountRPCError, AccountStateError, SessionError, SessionRevokedError,
                     UnsupportedOperationError, UploadCancelledError, UploadInterruptedError)
from .protocol import TLCodecError

UPLOAD_ENDPOINT = 'https://alzheimer.eitaa.com/eitaa/'
DOWNLOAD_ENDPOINT = 'https://mohsen.eitaa.com/eitaa/'
PART_SIZE = 512 * 1024
BIG_FILE_SIZE = 10 * 1024 * 1024  # Eitaa web uses >=, including the exact boundary.
MAX_FILE_SIZE = 4000 * PART_SIZE


def _caption(value):
    if not isinstance(value, str):
        raise ValueError('caption must be a string')
    try:
        if len(value) > 1024 or len(value.encode('utf-16-le')) // 2 > 1024:
            raise ValueError('caption must contain at most 1024 UTF-16 units')
    except UnicodeEncodeError:
        raise ValueError('caption contains invalid Unicode') from None


def _filename(value):
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or any(c in value for c in '/\\\0') or any(ord(c) < 32 for c in value)):
        raise ValueError('filename must be a plain basename')
    try:
        if len(value.encode('utf-8')) > 255:
            raise ValueError('filename exceeds 255 UTF-8 bytes')
    except UnicodeEncodeError:
        raise ValueError('filename contains invalid Unicode') from None
    return value


def _signature(source):
    value = os.fstat(source.stream.fileno())
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


@dataclass
class _LocalFile:
    stream: BinaryIO
    size: int
    name: str
    header: bytes
    stamp: tuple | None


def _correlate(value, peer, tokens, captions, reply_to, peers):
    results = tuple(correlate_sent(value, peer, token, caption, reply_to, peers)
                    for token, caption in zip(tokens, captions))
    if len({item.id for item in results}) != len(tokens):
        raise TLCodecError('Media response has duplicate destination mappings')
    if isinstance(value, dict) and value.get('_') in ('updates', 'updatesCombined', 'updateShort'):
        for update in _updates(value):
            message = update.get('message')
            if (update.get('_') in ('updateNewMessage', 'updateNewChannelMessage')
                    and isinstance(message, dict) and message.get('id') in {item.id for item in results}
                    and peer_key(message.get('peer_id')) == peer.key
                    and message.get('media', {}).get('_') not in ('messageMediaPhoto', 'messageMediaDocument')):
                raise TLCodecError('Media acknowledgement contains no photo/document')
    groups = {item.grouped_id for item in results if item.grouped_id is not None}
    if len(groups) > 1:
        raise TLCodecError('Album response contains different media group IDs')
    return tuple(replace(item, has_media=True) for item in results)


class AccountMedia:
    """One client/session, sequential chunks, no automatic upload/send retries.

    Endpoints are role-specific entries from the pinned public worker. Override
    only with an explicit HTTPS Eitaa /eitaa/ endpoint. No browser state is read.
    """

    def __init__(self, client, *, upload_endpoint=UPLOAD_ENDPOINT, download_endpoint=DOWNLOAD_ENDPOINT):
        for endpoint in (upload_endpoint, download_endpoint):
            AccountHTTPTransport(device_id='validation', endpoint=endpoint)
        self.client = client
        self.upload_endpoint, self.download_endpoint = upload_endpoint, download_endpoint

    def _owner(self):
        self.client._require_authorized()
        session = self.client._current_session()
        return (session.user_id, hashlib.sha256(session.device_id.encode()).hexdigest(),
                session.endpoint, session.api_id)

    def _check_owner(self, owner):
        if self._owner() != owner:
            raise SessionError('Media transfer belongs to a different account session')

    def _transport(self, role):
        session = self.client._current_session()
        endpoint = self.upload_endpoint if role == 'upload' else self.download_endpoint
        return AccountHTTPTransport(device_id=session.device_id, token=session.token,
                                    endpoint=endpoint, codec=self.client._rpc_transport().codec)

    def _revoked(self, exc):
        if exc.rpc_type in ('INVALID_LOGIN', 'AUTH_KEY_UNREGISTERED', 'SESSION_REVOKED', 'SESSION_EXPIRED'):
            self.client._authorized = False
            raise SessionRevokedError('Saved account session was rejected; file retained') from None
        raise exc

    @contextmanager
    def _open(self, file, filename=None):
        if isinstance(file, UploadedFile):
            if filename is not None:
                raise ValueError('An UploadedFile already has its filename')
            self._check_owner(file._owner)
            _filename(file.name)
            if (type(file.size) is not int or not 0 < file.size <= MAX_FILE_SIZE
                    or type(file.parts) is not int or file.parts != (file.size + PART_SIZE - 1) // PART_SIZE
                    or type(file.big) is not bool or file.big != (file.size >= BIG_FILE_SIZE)):
                raise ValueError('Invalid uploaded file metadata')
            yield file
            return
        if isinstance(file, bytes):
            stream = io.BytesIO(file)
            source = _LocalFile(stream, len(file), _filename(filename or 'file.bin'), file[:4096], None)
        elif isinstance(file, (str, Path)):
            path = Path(file)
            if path.is_symlink():
                raise ValueError('Upload source must not be a symbolic link')
            stream = path.open('rb')
            try:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Upload source must be a regular file')
                source = _LocalFile(stream, info.st_size, _filename(filename or path.name), stream.read(4096), None)
                stream.seek(0)
                source.stamp = _signature(source)
            except BaseException:
                stream.close()
                raise
        else:
            raise ValueError('file must be local path, bytes or UploadedFile')
        try:
            if not 0 < source.size <= MAX_FILE_SIZE:
                raise ValueError(f'File must contain 1..{MAX_FILE_SIZE} bytes (SDK limit)')
            yield source
        finally:
            stream.close()

    @staticmethod
    def _media_options(source, kind, mime_type, duration, width, height):
        try:
            kind = MediaKind(kind)
        except (ValueError, TypeError):
            raise ValueError('Unsupported media kind') from None
        if kind not in (MediaKind.PHOTO, MediaKind.DOCUMENT, MediaKind.VIDEO, MediaKind.VOICE):
            raise ValueError('Use photo, document, video or voice')
        if kind == MediaKind.PHOTO:
            if not (source.header.startswith(b'\xff\xd8\xff') or source.header.startswith(b'\x89PNG\r\n\x1a\n')):
                raise ValueError('Photo must be JPEG or PNG; no conversion is performed')
            return kind, None, []
        mime = mime_type or mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
        if not isinstance(mime, str) or len(mime) > 255 or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+', mime):
            raise ValueError('Invalid media MIME type')
        attributes = [{'_': 'documentAttributeFilename', 'file_name': source.name}]
        if kind in (MediaKind.VIDEO, MediaKind.VOICE):
            if type(duration) is not int or not 0 <= duration < 2**31:
                raise ValueError('Voice/video duration must be a nonnegative integer in seconds')
        if kind == MediaKind.VOICE:
            if not source.header.startswith(b'OggS') or b'OpusHead' not in source.header:
                raise ValueError('Voice requires Ogg/Opus; renaming a file does not convert it')
            mime = 'audio/ogg'
            attributes.append({'_': 'documentAttributeAudio', 'voice': True, 'duration': duration})
        if kind == MediaKind.VIDEO:
            if any(type(value) is not int or not 0 < value < 2**31 for value in (width, height)):
                raise ValueError('Video width and height must be positive integers')
            if not mime.startswith('video/'):
                raise ValueError('Video requires a video MIME type')
            attributes.append({'_': 'documentAttributeVideo', 'duration': duration, 'w': width, 'h': height})
        return kind, mime, attributes

    async def _part(self, owner, file_id, index, method, params):
        async with self.client._lock:
            self._check_owner(owner)
            transport = self._transport('upload')
            transport.encode_request(method, params)
            try:
                result = (await transport.invoke(method, params)).result
                if result is not True:
                    raise TLCodecError('Upload part did not receive boolTrue')
            except asyncio.CancelledError:
                raise UploadCancelledError(file_id, index) from None
            except AccountRPCError as exc:
                self._revoked(exc)
            except Exception:
                raise UploadInterruptedError(file_id, index) from None

    async def _stage(self, source, peer, owner, progress=None):
        self._check_owner(owner)
        if isinstance(source, UploadedFile):
            return source
        file_id = secrets.randbits(63) or 1
        parts = (source.size + PART_SIZE - 1) // PART_SIZE
        big = source.size >= BIG_FILE_SIZE
        output_peer = {'_': {'user': 'peerUser', 'chat': 'peerChat', 'channel': 'peerChannel'}[peer.kind.value],
                       peer.kind.value + '_id': peer.id}
        if big:
            # The web client sends a one-byte quota announcement before a big upload.
            await self._part(owner, file_id, -1, 'upload.saveFilePart', dict(
                file_id=secrets.randbits(63) or 1, file_part=0, bytes=b'\0', peer=output_peer, totalFileSize=source.size))
        digest = hashlib.md5()
        for index in range(parts):
            data = source.stream.read(PART_SIZE)
            if len(data) != min(PART_SIZE, source.size - index * PART_SIZE):
                raise ValueError('Upload source changed or was truncated; no message sent')
            digest.update(data)
            params: dict[str, Any] = dict(file_id=file_id, file_part=index, bytes=data)
            if big:
                params['file_total_parts'] = parts
            if index == 0:
                params.update(peer=output_peer, totalFileSize=source.size)
            await self._part(owner, file_id, index, 'upload.saveBigFilePart' if big else 'upload.saveFilePart', params)
            if progress is not None:
                result = progress(min((index + 1) * PART_SIZE, source.size), source.size)
                if inspect.isawaitable(result):
                    await result
        if source.stamp is not None and _signature(source) != source.stamp:
            raise ValueError('Upload source changed; no message sent')
        return UploadedFile(file_id, parts, source.name, source.size, big, digest.hexdigest(), owner, source.header)

    async def upload_file(self, file, *, peer='self', filename=None, progress=None):
        owner = self._owner()
        with self._open(file, filename) as source:
            resolved = await self.client.resolve_peer(peer)
            return await self._stage(source, resolved, owner, progress)

    @staticmethod
    def _input(uploaded, options):
        kind, mime, attributes = options
        if kind == MediaKind.PHOTO:
            return {'_': 'inputMediaUploadedPhoto', 'file': uploaded.to_input_file()}
        return dict(_='inputMediaUploadedDocument', file=uploaded.to_input_file(), mime_type=mime,
                    attributes=attributes, force_file=kind == MediaKind.DOCUMENT)

    @staticmethod
    def _send_options(caption, silent, reply_to):
        _caption(caption)
        if type(silent) is not bool:
            raise ValueError('silent must be bool')
        if reply_to is not None and not message_id(reply_to):
            raise ValueError('reply_to_msg_id must be a positive signed 32-bit integer')

    async def _send(self, owner, peer, method, params, tokens, captions, reply_to, *, role):
        async with self.client._lock:
            self._check_owner(owner)
            transport = self._transport('upload') if role == 'upload' else self.client._rpc_transport()
            return await self.client._message_operation('send_media' if len(tokens) == 1 else 'send_album',
                method, params, (), tokens,
                lambda value: _correlate(value, peer, tokens, captions, reply_to, self.client._peers),
                transport=transport)

    async def send_file(self, peer, file, *, kind=MediaKind.DOCUMENT, filename=None, caption='',
                        mime_type=None, duration=None, width=None, height=None, silent=False,
                        reply_to_msg_id=None, random_id=None, progress=None):
        self._send_options(caption, silent, reply_to_msg_id)
        tokens = operation_random_ids(1, None if random_id is None else [random_id])
        owner = self._owner()
        with self._open(file, filename) as source:
            options = self._media_options(source, kind, mime_type, duration, width, height)
            resolved = await self.client.resolve_peer(peer)
            uploaded = await self._stage(source, resolved, owner, progress)
            params = dict(peer=resolved.to_input_peer(), media=self._input(uploaded, options),
                          message=caption, random_id=tokens[0], silent=silent, entities=[])
            if reply_to_msg_id is not None:
                params['reply_to_msg_id'] = reply_to_msg_id
            result = await self._send(owner, resolved, 'messages.sendMedia', params, tokens,
                                      (caption,), reply_to_msg_id, role='upload')
            return result[0]

    async def send_photo(self, peer, file, **kwargs):
        return await self.send_file(peer, file, kind=MediaKind.PHOTO, **kwargs)

    async def send_video(self, peer, file, *, duration, width, height, **kwargs):
        return await self.send_file(peer, file, kind=MediaKind.VIDEO, duration=duration, width=width, height=height, **kwargs)

    async def send_voice(self, peer, file, *, duration, **kwargs):
        return await self.send_file(peer, file, kind=MediaKind.VOICE, duration=duration, **kwargs)

    async def send_album(self, peer, items, *, silent=False, reply_to_msg_id=None, random_ids=None):
        if not isinstance(items, (list, tuple)) or not 2 <= len(items) <= 10 or any(not isinstance(item, MediaItem) for item in items):
            raise ValueError('Album requires 2..10 MediaItem objects')
        self._send_options('', silent, reply_to_msg_id)
        tokens = operation_random_ids(len(items), random_ids)
        owner = self._owner()
        with ExitStack() as stack:
            sources, options = [], []
            for item in items:
                _caption(item.caption)
                source = stack.enter_context(self._open(item.file, item.filename))
                option = self._media_options(source, item.kind, item.mime_type, item.duration, item.width, item.height)
                if option[0] == MediaKind.VOICE:
                    raise ValueError('Voice is not supported in albums')
                sources.append(source)
                options.append(option)
            kinds = {option[0] for option in options}
            if MediaKind.DOCUMENT in kinds and len(kinds) != 1:
                raise ValueError('Document albums cannot mix with photos/videos')
            resolved = await self.client.resolve_peer(peer)
            multi = []
            for source, option, token, item in zip(sources, options, tokens, items):
                uploaded = await self._stage(source, resolved, owner)
                async with self.client._lock:
                    self._check_owner(owner)
                    transport = self._transport('upload')
                    # Materialization stages media only; it never sends a chat message.
                    try:
                        result = (await transport.invoke('messages.uploadMedia', dict(
                            peer=resolved.to_input_peer(), media=self._input(uploaded, option)))).result
                    except AccountRPCError as exc:
                        self._revoked(exc)
                info = parse_media(result)
                if info is None or (info.kind == MediaKind.PHOTO) != (option[0] == MediaKind.PHOTO):
                    raise TLCodecError('Album staging returned unrelated media')
                multi.append(dict(_='inputSingleMedia', media=info.to_input_media(), random_id=token,
                                  message=item.caption, entities=[]))
            params = dict(peer=resolved.to_input_peer(), multi_media=multi, silent=silent)
            if reply_to_msg_id is not None:
                params['reply_to_msg_id'] = reply_to_msg_id
            return await self._send(owner, resolved, 'messages.sendMultiMedia', params, tokens,
                                    tuple(item.caption for item in items), reply_to_msg_id, role='client')

    async def _download_part(self, owner, location, offset):
        async with self.client._lock:
            self._check_owner(owner)
            for attempt in range(2):
                try:
                    return (await self._transport('download').invoke('upload.getFile', dict(
                        location=location, offset=offset, limit=PART_SIZE, cdn_supported=False))).result
                except AccountStateError:
                    if attempt:
                        raise
                    await self.client._refresh_token()
                except AccountRPCError as exc:
                    self._revoked(exc)

    async def download_media(self, peer, message_id, destination, *, overwrite=False, max_size=MAX_FILE_SIZE):
        if type(overwrite) is not bool or type(max_size) is not int or not 0 <= max_size <= MAX_FILE_SIZE:
            raise ValueError('Invalid download overwrite/size limit')
        owner = self._owner()
        resolved = await self.client.resolve_peer(peer)
        message = await self.client.get_message(resolved, message_id)
        if message is None or message.media is None:
            raise UnsupportedOperationError('Message has no supported downloadable photo/document')
        info = message.media
        if info.size > max_size:
            raise ValueError('Media exceeds the requested download size limit')
        target = Path(destination).absolute()
        if target.is_symlink() or (target.exists() and (not overwrite or not target.is_file())):
            raise FileExistsError('Download destination already exists or is not a regular file')
        if not target.parent.is_dir():
            raise ValueError('Download destination parent must already exist')
        fd, temporary = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.part', dir=target.parent)
        try:
            digest, offset, refreshed = hashlib.sha256(), 0, False
            with os.fdopen(fd, 'wb') as stream:
                while offset < info.size:
                    self._check_owner(owner)
                    if info.cached_bytes is not None:
                        data = info.cached_bytes[offset:offset + PART_SIZE]
                    else:
                        try:
                            value = await self._download_part(owner, info.to_location(), offset)
                        except AccountRPCError as exc:
                            if refreshed or not (exc.rpc_type.startswith('FILE_REFERENCE_') and exc.rpc_type.endswith('EXPIRED')):
                                raise
                            current = await self.client.get_message(resolved, message_id)
                            replacement = current.media if current is not None else None
                            if replacement is None or (replacement.id, replacement.kind, replacement.size, replacement.thumb_size) != (info.id, info.kind, info.size, info.thumb_size):
                                raise TLCodecError('Media changed during file-reference refresh') from None
                            info, refreshed = replacement, True
                            continue
                        if not isinstance(value, dict) or value.get('_') != 'upload.file':
                            raise UnsupportedOperationError('CDN redirects and non-file download responses are unsupported')
                        data = value.get('bytes')
                    if not isinstance(data, bytes) or len(data) != min(PART_SIZE, info.size - offset):
                        raise TLCodecError('Download chunk is truncated or exceeds the expected size')
                    stream.write(data)
                    digest.update(data)
                    offset += len(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_owner(owner)
            if target.is_symlink():
                raise FileExistsError('Download destination became a symbolic link')
            if overwrite:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)  # Atomic publication without replacing an existing file.
            return DownloadedFile(target, offset, digest.hexdigest())
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
