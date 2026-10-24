"""Account-bound durable work. Enqueue never transmits a chat mutation."""
import asyncio
from dataclasses import asdict
import hashlib
from pathlib import Path
import re

from .account_files import MediaItem
from .account_operations import operation_random_ids, validate_ids, validate_text
from .account_messages import message_id
from .account_peers import AccountPeer
from .account_updates import AccountUpdates, UpdateCheckpointStore
from .enums import MediaKind, PeerKind
from .errors import (AccountRPCError, AuthenticationError, QueueError, SessionRevokedError,
                     UploadCancelledError, UploadInterruptedError)
from .queue_store import SQLiteCheckpointStore, SQLiteQueueStore


def queue_owner(client):
    client._require_authorized()
    session = client._current_session()
    return [session.user_id, session.endpoint, session.api_id,
            hashlib.sha256(session.device_id.encode()).hexdigest()]


def _peer_data(peer):
    return {'id': peer.id, 'kind': peer.kind.value, 'access_hash': peer.access_hash,
            'is_self': peer.is_self, 'is_min': peer.is_min, 'is_megagroup': peer.is_megagroup}


def _peer(value):
    return AccountPeer(**{**value, 'kind': PeerKind(value['kind'])})


class AccountQueue:
    """A durable queue bound to one authorized account/device/application.

    Use as a synchronous context manager inside the AccountClient async context.
    Workers send one claimed job at a time. They never retry uncertain writes.
    """
    def __init__(self, client, path=None):
        self.client = client
        self._owner = queue_owner(client)
        path = Path(path or (str(client._store.path) + '.queue.sqlite3')).absolute()
        if path.resolve() in (client._store.path.resolve(), Path(str(client._store.path)+'.updates').resolve()):
            raise ValueError('Queue must not overwrite session or JSON update checkpoint')
        self.store = SQLiteQueueStore(path, self._owner)
        self.updates = AccountUpdates(client, store=SQLiteCheckpointStore(self.store))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.store.close()

    def _check(self):
        if queue_owner(self.client) != self._owner:
            raise QueueError('Queue belongs to a different account/device/application')

    async def _resolve(self, peer):
        self._check()
        resolved = await self.client.resolve_peer(peer)
        self._check()
        return resolved

    async def enqueue_message(self, chat_id, text, *, key=None, reply_to_msg_id=None, silent=False, no_webpage=False):
        validate_text(text)
        if reply_to_msg_id is not None and not message_id(reply_to_msg_id):
            raise ValueError('Invalid reply_to_msg_id')
        if type(silent) is not bool or type(no_webpage) is not bool:
            raise ValueError('silent and no_webpage must be bool')
        resolved = await self._resolve(chat_id)
        return self.store.enqueue('send_message', dict(peer=_peer_data(resolved), text=text,
            reply_to_msg_id=reply_to_msg_id, silent=silent, no_webpage=no_webpage,
            random_id=operation_random_ids(1, None)[0]), key=key)

    async def enqueue_edit(self, chat_id, message_id, text, *, key=None, no_webpage=False):
        validate_ids([message_id])
        validate_text(text)
        if type(no_webpage) is not bool:
            raise ValueError('no_webpage must be bool')
        resolved = await self._resolve(chat_id)
        return self.store.enqueue('edit_message', dict(peer=_peer_data(resolved), message_id=message_id,
                                                      text=text, no_webpage=no_webpage), key=key)

    async def enqueue_delete(self, chat_id, message_ids, *, key=None, revoke=False):
        identifiers = validate_ids(message_ids)
        if type(revoke) is not bool:
            raise ValueError('revoke must be bool')
        resolved = await self._resolve(chat_id)
        if resolved.kind == PeerKind.CHANNEL and not revoke:
            raise ValueError('Channel deletion requires explicit revoke=True')
        return self.store.enqueue('delete_messages', dict(peer=_peer_data(resolved), message_ids=identifiers, revoke=revoke), key=key)

    async def enqueue_forward(self, chat_id, from_chat_id, message_ids, *, key=None, copy=False, silent=False):
        identifiers = validate_ids(message_ids)
        if type(copy) is not bool or type(silent) is not bool or (copy and len(identifiers) != 1):
            raise ValueError('Copy supports one message; copy and silent must be bool')
        source, destination = await self._resolve(from_chat_id), await self._resolve(chat_id)
        return self.store.enqueue('copy_message' if copy else 'forward_messages', dict(
            peer=_peer_data(destination), from_peer=_peer_data(source), message_ids=identifiers,
            silent=silent, random_ids=operation_random_ids(len(identifiers), None)), key=key)

    def _file_snapshot(self, file, *, filename=None, kind=MediaKind.DOCUMENT, mime_type=None, duration=None, width=None, height=None):
        if not isinstance(file, (str, Path)):
            raise ValueError('Queued media requires a persistent local path')
        path = Path(file).absolute()
        media = self.client.media()
        with media._open(path, filename) as source:
            media._media_options(source, kind, mime_type, duration, width, height)
            digest = hashlib.sha256()
            while chunk := source.stream.read(512 * 1024):
                digest.update(chunk)
            from .account_media import _signature
            if source.stamp != _signature(source):
                raise ValueError('Media file changed while queued')
            return dict(path=str(path), size=source.size, sha256=digest.hexdigest())

    async def enqueue_file(self, chat_id, file, *, key=None, kind=MediaKind.DOCUMENT, filename=None, caption='',
                           mime_type=None, duration=None, width=None, height=None, silent=False, reply_to_msg_id=None):
        self._check()
        self.client.media()._send_options(caption, silent, reply_to_msg_id)
        options = dict(kind=MediaKind(kind).value, filename=filename, caption=caption, mime_type=mime_type,
                       duration=duration, width=width, height=height, silent=silent, reply_to_msg_id=reply_to_msg_id)
        file_info = await asyncio.to_thread(self._file_snapshot, file, filename=filename,
            kind=kind, mime_type=mime_type, duration=duration, width=width, height=height)
        resolved = await self._resolve(chat_id)
        return self.store.enqueue('send_file', dict(peer=_peer_data(resolved), file=file_info,
            options=options, random_id=operation_random_ids(1, None)[0]), key=key)

    async def enqueue_album(self, chat_id, items, caption=None, *, key=None, silent=False, reply_to_msg_id=None):
        self._check()
        self.client.media()._send_options('', silent, reply_to_msg_id)
        items = self.client.media()._album_items(items, caption)
        packed, kinds = [], set()
        for item in items:
            kind = MediaKind(item.kind)
            if kind == MediaKind.VOICE:
                raise ValueError('Voice is unsupported in albums')
            self.client.media()._send_options(item.caption, silent, reply_to_msg_id)
            options = {key: value for key, value in asdict(item).items() if key != 'file'}
            snapshot = await asyncio.to_thread(self._file_snapshot, item.file, filename=item.filename,
                kind=kind, mime_type=item.mime_type, duration=item.duration, width=item.width, height=item.height)
            packed.append(dict(file=snapshot, options=options))
            kinds.add(kind)
        if MediaKind.DOCUMENT in kinds and len(kinds) != 1:
            raise ValueError('Document albums cannot mix with photo/video')
        resolved = await self._resolve(chat_id)
        return self.store.enqueue('send_album', dict(peer=_peer_data(resolved), items=packed, silent=silent,
            reply_to_msg_id=reply_to_msg_id, random_ids=operation_random_ids(len(items), None)), key=key)

    def _validate_files(self, job):
        request = job.request
        rows = [(request['file'], request['options'])] if job.operation == 'send_file' else [
            (item['file'], item['options']) for item in request.get('items', [])]
        for saved, options in rows:
            actual = self._file_snapshot(saved['path'], **{k: v for k, v in options.items()
                if k in ('filename', 'kind', 'mime_type', 'duration', 'width', 'height')})
            if actual != saved:
                raise ValueError('Queued media source changed; no upload or message sent')

    async def _dispatch(self, job):
        data = dict(job.request)
        peer = _peer(data.pop('peer'))
        operation = job.operation
        if operation == 'send_message':
            return await self.client.send_message(peer, **data)
        if operation == 'edit_message':
            return await self.client.edit_message(peer, **data)
        if operation == 'delete_messages':
            return await self.client.delete_messages(peer, **data)
        if operation in ('forward_messages', 'copy_message'):
            source = _peer(data.pop('from_peer'))
            if operation == 'copy_message':
                return await self.client.copy_message(peer, source, data['message_ids'][0],
                                                      random_id=data['random_ids'][0], silent=data['silent'])
            return await self.client.forward_messages(peer, source, **data)
        if operation == 'send_file':
            return await self.client.send_file(peer, data['file']['path'], random_id=data['random_id'], **data['options'])
        if operation == 'send_album':
            items = [MediaItem(item['file']['path'], **item['options']) for item in data.pop('items')]
            return await self.client.send_album(peer, items, **data)
        raise QueueError('Unsupported queued operation')

    @staticmethod
    def _receipt(result):
        if isinstance(result, tuple):
            return {'message_ids': [item.id for item in result]}
        if hasattr(result, 'message_ids'):
            return {'message_ids': list(result.message_ids)}
        return {'message_ids': [result.id]}

    async def dispatch_one(self, identifier=None, *, lease_seconds=300):
        self._check()
        job = self.store.claim_job(identifier, lease_seconds=lease_seconds)
        if job is None:
            return None
        try:
            await asyncio.to_thread(self._validate_files, job)
            self._check()
        except (ValueError, OSError, QueueError, AuthenticationError) as exc:
            return self.store.finish_job(job, 'failed', error=type(exc).__name__)
        try:
            result = await self._dispatch(job)
        except UploadCancelledError:
            self.store.finish_job(job, 'failed', error='UploadCancelledError')
            raise
        except asyncio.CancelledError:
            self.store.finish_job(job, 'uncertain', error='cancelled')
            raise
        except (AccountRPCError, SessionRevokedError, UploadInterruptedError) as exc:
            delay = exc.retry_after if isinstance(exc, AccountRPCError) else None
            error = type(exc).__name__
            if isinstance(exc, AccountRPCError) and re.fullmatch(r'[A-Z0-9_]{1,128}', exc.rpc_type):
                error += ':' + exc.rpc_type
            # Explicit RPC rejection and interrupted upload cannot confirm a send.
            return self.store.finish_job(job, 'failed', error=error, retry_after=delay or 0)
        except Exception as exc:
            # Conservative for network/decode/local errors after starting dispatch.
            return self.store.finish_job(job, 'uncertain', error=type(exc).__name__)
        # A crash or database error here leaves in_flight, later recoverable ONLY
        # as uncertain. A lost commit must never turn into an automatic resend.
        return self.store.finish_job(job, 'confirmed', receipt=self._receipt(result))

    async def poll_once(self, *, replay_pts=0):
        self._check()
        batch = await self.updates.poll(replay_pts=replay_pts)
        self._check()
        await self.updates.ack(batch)
        return batch

    async def import_checkpoint(self, path):
        """Explicit one-time import; the original JSON checkpoint is untouched."""
        self._check()
        if self.updates._checkpoint is not None or self.updates.store.load() is not None:
            raise QueueError('Import requires an unused SQLite checkpoint')
        value = UpdateCheckpointStore(path).load()
        if value is None:
            raise QueueError('No JSON checkpoint exists at that path')
        self.updates._validate(value, self.updates._owner())
        self.updates.store.save(value)
