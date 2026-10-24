"""Account difference polling, explicit acknowledgement and durable cursors.

Delivery is at least once. A handler must use event_id for idempotent effects.
No message bodies or credentials are written to the checkpoint.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import stat
import tempfile
import time

from .account_messages import AccountMessage, message_id, parse_message
from .account_peers import AccountPeer, peer_key
from .enums import PeerKind
from .errors import AccountRPCError, AccountTransportError, PeerResolveError, SessionError, UpdateGapError
from .protocol import TLCodecError

logger = logging.getLogger(__name__)


def _position(value):
    if type(value) is not int or not 0 <= value < 2**31:
        raise TLCodecError('Invalid update position')
    return value


def _state(value):
    if not isinstance(value, dict) or value.get('_') != 'updates.state':
        raise TLCodecError('Expected updates.state')
    return {key: _position(value.get(key)) for key in ('pts', 'qts', 'date', 'seq')}


def _fingerprint(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      default=lambda item: {'bytes': item.hex()} if isinstance(item, bytes) else str(item))
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass(frozen=True)
class AccountEvent:
    kind: str
    event_id: str
    peer: AccountPeer | None = None
    message: AccountMessage | None = field(default=None, repr=False)
    message_ids: tuple[int, ...] = ()
    raw_type: str = ''
    raw: dict | None = field(default=None, repr=False)


@dataclass(frozen=True)
class UpdateBatch:
    events: tuple[AccountEvent, ...]
    has_more: bool
    _checkpoint: dict = field(repr=False)


class UpdateCheckpointStore:
    """Atomic JSON, one writer/process per file; Windows inherits parent ACL."""
    MAX_BYTES = 2 * 1024 * 1024

    def __init__(self, path):
        self.path = Path(path).absolute()
        self._loaded = False
        self._digest = None

    def _read(self):
        try:
            if self.path.is_symlink():
                raise SessionError('Update checkpoint must not be a symbolic link')
            with self.path.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise SessionError('Update checkpoint must be a regular file')
                data = stream.read(self.MAX_BYTES + 1)
            if len(data) > self.MAX_BYTES:
                raise SessionError('Update checkpoint exceeds size limit')
            return data
        except FileNotFoundError:
            return None
        except OSError:
            raise SessionError('Cannot read update checkpoint') from None

    def load(self):
        data = self._read()
        try:
            value = json.loads(data) if data is not None else None
            if data is not None and not isinstance(value, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise SessionError('Malformed update checkpoint') from None
        self._loaded = True
        self._digest = hashlib.sha256(data).digest() if data is not None else None
        return value

    def save(self, value):
        if not self._loaded:
            raise SessionError('Load update checkpoint before saving')
        current = self._read()
        if (hashlib.sha256(current).digest() if current is not None else None) != self._digest:
            raise SessionError('Update checkpoint changed outside this consumer')
        data = (json.dumps(value, sort_keys=True) + '\n').encode()
        if len(data) > self.MAX_BYTES:
            raise SessionError('Update checkpoint exceeds size limit')
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.' + self.path.name + '.', dir=self.path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._digest = hashlib.sha256(data).digest()
        except OSError:
            raise SessionError('Cannot persist update checkpoint') from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


class AccountUpdates:
    """Poll common updates and explicitly watched channels, including megagroups.

    initialize() starts a fresh consumer at the current state. Existing cursors
    catch up through difference slices. watch_channel() starts a NEW channel at
    its current state; call it before asking someone to produce test events.
    The server can discard old differences: UpdateGapError never skips that gap.
    """

    def __init__(self, client, checkpoint_path=None, *, store=None):
        self.client = client
        if store is not None and checkpoint_path is not None:
            raise ValueError('Use checkpoint_path or a checkpoint store, not both')
        self.store = store if store is not None else UpdateCheckpointStore(checkpoint_path or (str(client._store.path) + '.updates'))
        if self.store.path.resolve() == client._store.path.resolve():
            raise ValueError('Checkpoint and session must use different files')
        self._checkpoint = None
        self._pending = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._running = False
        self._due = {}
        self._pending_due = {}
        self._next_checkpoint = None

    def _owner(self):
        self.client._require_authorized()
        session = self.client._current_session()
        return [session.user_id, session.endpoint, session.api_id]

    def _validate(self, value, owner):
        try:
            if (not isinstance(value, dict) or set(value) != {
                    'version', 'owner', 'state', 'channels', 'seen', 'known'}
                    or type(value['version']) is not int or value['version'] != 1
                    or value['owner'] != owner):
                raise ValueError
            if not isinstance(value['state'], dict) or set(value['state']) != {'pts', 'qts', 'date', 'seq'}:
                raise ValueError
            for number in value['state'].values():
                _position(number)
            if not isinstance(value['channels'], dict) or len(value['channels']) > 1000:
                raise ValueError
            for identifier, channel in value['channels'].items():
                if str(int(identifier)) != identifier or int(identifier) <= 0 or set(channel) != {'pts', 'access_hash'}:
                    raise ValueError
                _position(channel['pts'])
                AccountPeer(int(identifier), PeerKind.CHANNEL, channel['access_hash']).to_input_peer()
            if (not isinstance(value['seen'], list) or len(value['seen']) > 4096
                    or any(not isinstance(item, str) or len(item) != 64
                           or any(c not in '0123456789abcdef' for c in item) for item in value['seen'])):
                raise ValueError
            if not isinstance(value['known'], dict) or len(value['known']) > 4096:
                raise ValueError
            for identifier, key in value['known'].items():
                if str(int(identifier)) != identifier or not message_id(int(identifier)) or len(key) != 2:
                    raise ValueError
                if PeerKind(key[0]) == PeerKind.CHANNEL:
                    raise ValueError
                AccountPeer(key[1], PeerKind(key[0]))
        except (KeyError, TypeError, ValueError, TLCodecError, PeerResolveError):
            raise SessionError('Invalid update checkpoint or different account/application') from None

    async def _rpc(self, method, params):
        async with self.client._lock:
            owner = self._owner()
            if self._checkpoint is not None and self._checkpoint['owner'] != owner:
                raise SessionError('Update consumer belongs to a different account')
            reply = await self.client._read_rpc(method, params)
            result = reply.result
            if self._owner() != owner:
                raise SessionError('Account changed during update request')
            if not isinstance(result, dict):
                raise TLCodecError('Missing update result')
            # Structural diagnostics only: never log RPC params, entities or bodies.
            logger.info('RPC %s: result=%s; messages=%d; other_updates=%d; attached_updates=%d',
                        method, result.get('_', 'unknown'), len(result.get('new_messages', [])),
                        len(result.get('other_updates', [])), len(reply.updates))
            self.client._cache_entities(result.get('users', []) + result.get('chats', []))
            return result

    async def initialize(self):
        async with self._lock:
            await self._initialize()

    async def _initialize(self):
        owner = self._owner()
        if self._checkpoint is not None:
            if self._checkpoint['owner'] != owner:
                raise SessionError('Update consumer belongs to a different account')
            return
        value = self.store.load()
        logger.info('Checkpoint: %s', 'fresh baseline' if value is None else 'existing file loaded')
        if value is None:
            state = _state(await self._rpc('updates.getState', {}))
            value = dict(version=1, owner=owner, state=state, channels={}, seen=[], known={})
            self.store.save(value)
        self._validate(value, owner)
        logger.info('Checkpoint metadata: seen=%d; known=%d; channels=%d',
                    len(value['seen']), len(value['known']), len(value['channels']))
        self._checkpoint = value

    async def watch_channel(self, chat_id):
        """Persist a current baseline once; an existing cursor is never reset."""
        resolved = await self.client.resolve_peer(chat_id)
        if resolved.kind != PeerKind.CHANNEL:
            raise ValueError('watch_channel requires a channel or megagroup')
        resolved.to_input_peer()
        async with self._lock:
            await self._initialize()
            if self._pending is not None:
                raise SessionError('Acknowledge the pending batch before watching a channel')
            identifier = str(resolved.id)
            if identifier in self._checkpoint['channels']:
                return
            if len(self._checkpoint['channels']) >= 1000:
                raise SessionError('Too many watched channels')
            result = await self._rpc('messages.getHistory', dict(peer=resolved.to_input_peer(),
                offset_id=0, offset_date=0, add_offset=0, limit=1, max_id=0, min_id=0, hash=0))
            if result.get('_') != 'messages.channelMessages':
                raise TLCodecError('Channel history did not provide a pts baseline')
            value = deepcopy(self._checkpoint)
            value['channels'][identifier] = dict(pts=_position(result.get('pts')), access_hash=resolved.access_hash)
            self.store.save(value)
            self._checkpoint = value

    async def watch_dialogs(self):
        """Discover current channel dialogs once; call again to discover new ones."""
        async for dialog in self.client.iter_dialogs(limit=None):
            if dialog.peer.kind == PeerKind.CHANNEL:
                await self.watch_channel(dialog.peer)

    def _events(self, result, proposed, channel_id=None):
        events = []
        seen = OrderedDict.fromkeys(proposed['seen'])
        known = OrderedDict(proposed['known'])
        peers = self.client._peers

        def emit(kind, identity, peer=None, message=None, ids=(), raw=None):
            event_id = _fingerprint(identity)
            if event_id not in seen:
                events.append(AccountEvent(kind, event_id, peer, message, ids,
                                           raw.get('_', '') if raw else '', raw))
                seen[event_id] = None
                if len(seen) > 4096:
                    seen.popitem(last=False)

        updates = list(result.get('other_updates', []))
        # Eitaa also returns edited full messages in new_messages. Classify
        # those as edits only after this consumer has delivered the original
        # new-message identity; a first delivery remains new even if edited.
        difference_updates = []
        for item in result.get('new_messages', []):
            kind = 'updateNewChannelMessage' if channel_id else 'updateNewMessage'
            if isinstance(item, dict) and item.get('_') in ('message', 'messageService'):
                key = peer_key(item.get('peer_id'))
                was_delivered = _fingerprint(['new', key[0].value, key[1], item.get('id')]) in seen
                if was_delivered and 'edit_date' in item:
                    _position(item['edit_date'])
                    kind = 'updateEditChannelMessage' if key[0] == PeerKind.CHANNEL else 'updateEditMessage'
            difference_updates.append({'_': kind, 'message': item})
        updates = difference_updates + updates
        for update in updates:
            if not isinstance(update, dict) or not isinstance(update.get('_'), str):
                raise TLCodecError('Invalid difference update')
            name = update['_']
            if name in ('updateChannelTooLong', 'updatePtsChanged'):
                if name == 'updatePtsChanged':
                    raise UpdateGapError('common', 'Server requested a state reset')
                # The channel's own difference cursor, not this hint's pts, is authoritative.
                if str(update.get('channel_id')) not in proposed['channels']:
                    emit('raw', ['raw', update], raw=update)
                continue
            if name in ('updateNewMessage', 'updateNewChannelMessage', 'updateEditMessage', 'updateEditChannelMessage'):
                raw_message = update.get('message')
                if isinstance(raw_message, dict) and raw_message.get('_') == 'messageEmpty':
                    continue
                key = peer_key(raw_message.get('peer_id') if isinstance(raw_message, dict) else None)
                if channel_id is not None and key != (PeerKind.CHANNEL, channel_id):
                    raise TLCodecError('Channel difference contains a different peer')
                peer = peers.get(key) or AccountPeer(key[1], key[0])
                message = parse_message(raw_message, peer, peers)
                if message is None:
                    continue
                if key[0] != PeerKind.CHANNEL:
                    known[str(message.id)] = [key[0].value, key[1]]
                    known.move_to_end(str(message.id))
                    if len(known) > 4096:
                        known.popitem(last=False)
                kind = 'edit' if 'Edit' in name else 'new'
                identity = [kind, key[0].value, key[1], message.id]
                if kind == 'edit':
                    # The same edit may appear as an explicit update and as a
                    # full difference message. Transport pts must not cause a
                    # duplicate event for identical message content.
                    identity.append(raw_message)
                emit(kind, identity, peer, message, (message.id,), update)
            elif name in ('updateDeleteMessages', 'updateDeleteChannelMessages'):
                channel = update.get('channel_id') if name == 'updateDeleteChannelMessages' else None
                if channel is not None:
                    if type(channel) is not int or not 0 < channel < 2**63:
                        raise TLCodecError('Invalid deleted-message channel')
                if channel_id is not None and channel != channel_id:
                    raise TLCodecError('Channel deletion belongs to a different peer')
                ids = update.get('messages')
                if not isinstance(ids, list) or any(not message_id(mid) for mid in ids):
                    raise TLCodecError('Invalid deleted-message IDs')
                for mid in ids:
                    key = (PeerKind.CHANNEL, channel) if channel is not None else known.get(str(mid))
                    peer = None
                    if key is not None:
                        key = (PeerKind(key[0]), key[1])
                        peer = peers.get(key) or AccountPeer(key[1], key[0])
                    emit('delete', ['delete', channel, update.get('pts'), mid], peer, ids=(mid,), raw=update)
            else:
                emit('raw', ['raw', update], raw=update)
        for encrypted in result.get('new_encrypted_messages', []):
            emit('raw', ['encrypted', encrypted], raw=encrypted)
        proposed['seen'], proposed['known'] = list(seen), dict(known)
        return events

    async def poll(self, *, replay_pts=0):
        """Fetch one page per scope; return the same batch until ack succeeds.

        replay_pts explicitly overlaps the common cursor for recovery from an
        empty Eitaa difference. It never changes the committed cursor. Older
        events outside the bounded dedup cache can be delivered again.
        """
        if type(replay_pts) is not int or not 0 <= replay_pts <= 1000:
            raise ValueError('replay_pts must be an integer between 0 and 1000')
        async with self._lock:
            await self._initialize()
            if self._pending is not None:
                return self._pending
            value = deepcopy(self._checkpoint)
            due = dict(self._due)
            old = value['state']
            request_pts = max(0, old['pts'] - replay_pts)
            logger.info('Common difference request: replay_pts=%d', replay_pts)
            result = await self._rpc('updates.getDifference',
                dict(pts=request_pts, date=old['date'], qts=-1))
            name = result.get('_')
            more = name == 'updates.differenceSlice'
            if name == 'updates.differenceTooLong':
                raise UpdateGapError('common', 'Server no longer retains this difference')
            if name == 'updates.differenceEmpty':
                new = dict(old, date=_position(result.get('date')), seq=_position(result.get('seq')))
            elif name in ('updates.difference', 'updates.differenceSlice'):
                new = _state(result.get('intermediate_state' if more else 'state'))
            else:
                raise TLCodecError('Unexpected account difference constructor')
            if any(new[k] < old[k] for k in ('pts', 'qts', 'date', 'seq')) or (more and new == old):
                raise UpdateGapError('common', 'Non-progressing or regressing difference state')
            value['state'] = new
            events = self._events(result, value)
            logger.info('Common difference: state_changed=%s; delivered_events=%d', new != old, len(events))
            for update in result.get('other_updates', []):
                if update.get('_') == 'updateChannelTooLong':
                    due.pop(str(update.get('channel_id')), None)
            for identifier, channel in value['channels'].items():
                if due.get(identifier, 0) > time.monotonic():
                    continue
                result = await self._rpc('updates.getChannelDifference', dict(
                    channel={'_': 'inputChannel', 'channel_id': int(identifier), 'access_hash': channel['access_hash']},
                    filter={'_': 'channelMessagesFilterEmpty'}, pts=channel['pts'], limit=1000))
                name = result.get('_')
                if name == 'updates.channelDifferenceTooLong':
                    raise UpdateGapError('channel', 'Server no longer retains this channel difference')
                if name not in ('updates.channelDifference', 'updates.channelDifferenceEmpty'):
                    raise TLCodecError('Unexpected channel difference constructor')
                pts = _position(result.get('pts'))
                final = bool(result.get('pFlags', {}).get('final'))
                if pts < channel['pts'] or (not final and pts == channel['pts']):
                    raise UpdateGapError('channel', 'Non-progressing or regressing channel state')
                channel['pts'] = pts
                timeout = _position(result.get('timeout', 1))
                due[identifier] = time.monotonic() + max(1, timeout) if final else 0
                more = more or not final
                events.extend(self._events(result, value, int(identifier)))
            self._next_checkpoint = value
            self._pending_due = due
            self._pending = UpdateBatch(tuple(events), more, deepcopy(value))
            return self._pending

    async def ack(self, batch):
        """Commit only after every event's effects completed successfully."""
        async with self._lock:
            self._owner()
            if batch is not self._pending or batch is None:
                raise ValueError('Only this consumer\'s pending batch can be acknowledged')
            self._validate(self._next_checkpoint, self._owner())
            commit_batch = getattr(self.store, 'commit_batch', None)
            if commit_batch is not None:
                commit_batch(self._next_checkpoint, batch.events)
            else:
                self.store.save(self._next_checkpoint)
            self._checkpoint = self._next_checkpoint
            self._due = self._pending_due
            self._next_checkpoint = None
            self._pending = None

    def stop(self):
        self._stop.set()

    async def run_polling(self, router, *, interval=5, replay_pts=0):
        """Retry only read transport failures/FLOOD_WAIT; handler errors escape."""
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 3600:
            raise ValueError('Polling interval must be between 1 and 3600 seconds')
        if type(replay_pts) is not int or not 0 <= replay_pts <= 1000:
            raise ValueError('replay_pts must be an integer between 0 and 1000')
        if self._running:
            raise RuntimeError('This consumer is already running')
        self._running = True
        self._stop.clear()
        backoff = interval
        try:
            while not self._stop.is_set():
                try:
                    batch = await self.poll(replay_pts=replay_pts)
                except AccountTransportError:
                    delay = backoff
                    backoff = min(60, backoff * 2)
                except AccountRPCError as exc:
                    if exc.retry_after is None:
                        raise
                    delay = max(interval, exc.retry_after)
                else:
                    for event in batch.events:
                        await router.emit(event.kind, event)
                    await self.ack(batch)
                    # Retry the overlap until acknowledgement, then resume the
                    # authoritative returned state without a permanent rewind.
                    replay_pts = 0
                    backoff = interval
                    if batch.has_more:
                        continue
                    delay = interval
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
