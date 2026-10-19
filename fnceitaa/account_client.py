"""Experimental phone login and persistent Eitaa user-account sessions."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field, replace
import hashlib
import re
import secrets
import time
import unicodedata
from typing import Any

from .account import AccountHTTPTransport, DEFAULT_ENDPOINT
from .account_peers import (AccountDialog, AccountPeer, DialogCursor, DialogPage,
                            entity_peer, peer_key, positive_id)
from .enums import PeerKind
from .account_messages import AccountMessage, HistoryPage, correlate_sent, message_id, parse_message
from .account_operations import (DeletedMessages, ForwardedMessage, correlate_delete,
                                 correlate_edit, correlate_forward, operation_random_ids,
                                 validate_ids, validate_text)
from .errors import (AccountRPCError, AccountStateError, AuthenticationError,
                     PasswordRequiredError, PeerResolveError, SessionError, SessionRevokedError,
                     SendMessageCancelledError, SendMessageUncertainError,
                     MessageOperationCancelledError, MessageOperationUncertainError,
                     UnsupportedOperationError)
from .password import legacy_password_hash, srp_password_proof
from .protocol import TLCodecError
from .session import AccountSession, SessionStore

# Public application identity from the pinned main-domain web bundle, not a
# private account credential. Callers may supply their own application identity.
WEB_API_ID = 2496
WEB_API_HASH = '8da85b0d5bfe62527e5b244c209159c3'
REVOKED = {'INVALID_LOGIN', 'AUTH_KEY_UNREGISTERED', 'SESSION_REVOKED', 'SESSION_EXPIRED'}


def _digits(value):
    return ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in value)


def normalize_phone(phone):
    if not isinstance(phone, str):
        raise ValueError('Phone must be an Iranian mobile number (09...) or an international number (+country code)')
    value = re.sub(r'[ ()\-]', '', _digits(phone.strip()))
    if re.fullmatch(r'09[0-9]{9}', value):
        value = '+98' + value[1:]
    elif value.startswith('00'):
        value = '+' + value[2:]
    if not re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
        raise ValueError('Phone must be an 11-digit Iranian mobile number (09...) or include + and country code')
    return value


def _base36(value):
    result = ''
    while value:
        value, digit = divmod(value, 36)
        result = '0123456789abcdefghijklmnopqrstuvwxyz'[digit] + result
    return result or '0'


def create_device_id(phone, *, timestamp_ms=None):
    phone = normalize_phone(phone)
    stamp = _base36(time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms)[-8:].rjust(8, '0')
    # Single-account index 1, matching gf(phone, 1) in the public web source.
    return hashlib.sha256(phone[1:].encode()).hexdigest()[:6] + stamp + '1__web'


@dataclass(frozen=True)
class CodeRequest:
    delivery: str
    length: int | None
    timeout: int | None
    synthetic_ack: bool = False
    phone_number: str = field(default='', repr=False)
    phone_code_hash: str = field(default='', repr=False)


class AccountClient:
    """A user-account client, distinct from the Eitaayar/public-page Client.

    connect() verifies an existing session with users.getUsers(inputUserSelf).
    start() and async with reuse a valid session or run terminal login when
    auto_login=True. connect() is the noninteractive low-level connection API.
    Use one client/process per session file.
    """

    def __init__(self, session_path='account.session', *, api_id=WEB_API_ID,
                 api_hash=WEB_API_HASH, endpoint=DEFAULT_ENDPOINT, auto_login=True):
        if type(auto_login) is not bool:
            raise ValueError('auto_login must be a bool')
        if type(api_id) is not int or not 0 < api_id < 2**31:
            raise ValueError('api_id must be a positive signed 32-bit integer')
        if not isinstance(api_hash, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
            raise ValueError('api_hash must contain 32 hexadecimal characters')
        # Validate endpoint now, even before opening a session or sending anything.
        AccountHTTPTransport(device_id='validation', endpoint=endpoint)
        self._store = SessionStore(session_path)
        self._api_id, self._api_hash = api_id, api_hash
        self._endpoint = endpoint
        self._session: AccountSession | None = None
        self._transport: AccountHTTPTransport | None = None
        self._challenge: CodeRequest | None = None
        self._password_code: str | None = None
        self._connected = False
        self._authorized = False
        self._uncertain = False
        self._auto_login = auto_login
        self._start_lock = asyncio.Lock()
        self._expected_login_user: int | None = None
        self._lock = asyncio.Lock()
        self._peers: OrderedDict[tuple[PeerKind, int], AccountPeer] = OrderedDict()

    def updates(self, checkpoint_path=None):
        """Create an account event consumer with a separate persistent cursor."""
        from .account_updates import AccountUpdates
        return AccountUpdates(self, checkpoint_path)

    def media(self, **kwargs):
        """Create a media helper; optional upload/download endpoint overrides."""
        from .account_media import AccountMedia
        return AccountMedia(self, **kwargs)

    def profiles(self):
        """Read full account profiles and permitted membership information."""
        from .account_profiles import AccountProfiles
        return AccountProfiles(self)

    def queue(self, path=None):
        """Open an account-bound durable inbox/outbox; close it before the client."""
        from .account_queue import AccountQueue
        return AccountQueue(self, path)

    async def get_chat_info(self, peer):
        return await self.profiles().get_chat_info(peer)

    async def get_full_user(self, peer='self'):
        return await self.profiles().get_full_user(peer)

    async def get_full_group(self, peer):
        return await self.profiles().get_full_group(peer)

    async def get_full_channel(self, peer):
        return await self.profiles().get_full_channel(peer)

    async def get_member_count(self, peer):
        return await self.profiles().get_member_count(peer)

    async def get_members_page(self, peer, **kwargs):
        return await self.profiles().get_members_page(peer, **kwargs)

    def iter_members(self, peer, **kwargs):
        return self.profiles().iter_members(peer, **kwargs)

    async def get_member(self, peer, participant='self'):
        return await self.profiles().get_member(peer, participant)

    async def upload_file(self, file, **kwargs):
        return await self.media().upload_file(file, **kwargs)

    async def send_file(self, peer, file, **kwargs):
        """Send a document by default; accepts a local path, bytes or UploadedFile."""
        return await self.media().send_file(peer, file, **kwargs)

    async def send_photo(self, peer, file, **kwargs):
        return await self.media().send_photo(peer, file, **kwargs)

    async def send_video(self, peer, file, *, duration, width, height, **kwargs):
        return await self.media().send_video(peer, file, duration=duration, width=width, height=height, **kwargs)

    async def send_voice(self, peer, file, *, duration, **kwargs):
        return await self.media().send_voice(peer, file, duration=duration, **kwargs)

    async def send_album(self, peer, items, **kwargs):
        return await self.media().send_album(peer, items, **kwargs)

    async def download_media(self, peer, message_id, destination, **kwargs):
        return await self.media().download_media(peer, message_id, destination, **kwargs)

    def __repr__(self):
        return f'AccountClient(authorized={self.is_authorized})'

    @property
    def is_authorized(self):
        return self._connected and self._authorized and not self._uncertain

    async def __aenter__(self):
        try:
            return await self.start()
        except BaseException:
            await self.disconnect()
            raise

    async def __aexit__(self, *_):
        await self.disconnect()

    def _ready(self):
        if not self._connected:
            raise AuthenticationError('Call connect() before using the account client')
        if self._uncertain:
            raise AuthenticationError('Previous authentication outcome is uncertain; create a new client')

    def _rpc_transport(self) -> AccountHTTPTransport:
        if self._transport is None:
            raise AuthenticationError('No account transport is active')
        return self._transport

    def _current_session(self) -> AccountSession:
        if self._session is None:
            raise AuthenticationError('No account session is active')
        return self._session

    async def connect(self):
        """Load/check a session without terminal prompts or OTP requests."""
        async with self._lock:
            if self._uncertain:
                raise AuthenticationError('Previous authentication outcome is uncertain; create a new client')
            if self._connected:
                self._ready()
                return self
            session = self._store.load()
            if session is None:
                self._connected = True
                return self
            if session.api_id != self._api_id or session.endpoint != self._endpoint:
                raise SessionError('Session application identity or endpoint does not match this client')
            self._transport = AccountHTTPTransport(device_id=session.device_id, token=session.token,
                                                    endpoint=session.endpoint)
            self._session = session
            self._connected = True
            try:
                await self._get_me()
            except SessionRevokedError:
                self._expected_login_user = session.user_id
                self._connected = self._authorized = False
                self._session = self._transport = None
                raise
            except BaseException:
                self._connected = self._authorized = False
                self._session = self._transport = None
                raise
            self._authorized = True
            return self

    async def start(self):
        """Connect, then log in interactively if needed and auto_login=True.

        Explicitly rejected saved sessions may be replaced after a successful
        login to the same account. Transport, decoding and local-file errors
        propagate without prompting or replaying an operation.
        """
        async with self._start_lock:
            try:
                await self.connect()
            except SessionRevokedError:
                if not self._auto_login:
                    raise
                # connect() retained the old file and its digest. Keep it until
                # a matching login succeeds, so cancellation never deletes it.
                if self._expected_login_user is None:
                    raise AuthenticationError('No rejected session identity is available') from None
                async with self._lock:
                    self._session = self._transport = None
                    self._challenge = self._password_code = None
                    self._peers.clear()
                    self._connected = True
                print('Saved session rejected by Eitaa. Starting interactive login.')
            if not self.is_authorized and self._auto_login:
                self._ready()
                if self._session is not None:
                    raise AuthenticationError('Session is no longer authorized; reconnect before starting login')
                from .account_login import interactive_login
                await interactive_login(self)
            return self

    async def disconnect(self):
        """Forget credentials in this object; keep the saved session and remote login."""
        async with self._lock:
            self._connected = self._authorized = False
            self._challenge = self._password_code = None
            self._session = self._transport = None
            self._peers.clear()

    async def _auth_rpc(self, method, params=None):
        try:
            return await self._rpc_transport().invoke(method, params)
        except asyncio.CancelledError:
            self._uncertain = True
            self._challenge = self._password_code = None
            raise
        except Exception as exc:
            if isinstance(exc, AccountRPCError) and exc.rpc_type in (
                    'PHONE_CODE_EXPIRED', 'PHONE_CODE_HASH_EMPTY', 'PHONE_CODE_HASH_INVALID'):
                self._challenge = self._password_code = None
            # Explicit server failures are certain; transport/decoding failures
            # after a possible write must not silently reuse the login attempt.
            if not isinstance(exc, (AccountRPCError, UnsupportedOperationError, ValueError)):
                self._uncertain = True
                self._challenge = self._password_code = None
            raise

    async def request_code(self, phone) -> CodeRequest:
        """Send one OTP request. This has a live external effect; never auto-retried."""
        phone = normalize_phone(phone)
        async with self._lock:
            self._ready()
            if self._session is not None:
                raise AuthenticationError('Use a different session file to log into another account')
            if self._challenge is not None:
                raise AuthenticationError('An OTP challenge is already pending; use resend_code explicitly')
            self._transport = AccountHTTPTransport(device_id=create_device_id(phone), endpoint=self._endpoint)
            self._password_code = None
            reply = await self._auth_rpc('auth.sendCode', {
                'phone_number': phone, 'api_id': self._api_id, 'api_hash': self._api_hash,
                'settings': {'_': 'codeSettings'},
            })
            return self._accept_code(reply, phone, allow_pong=True)

    def _accept_code(self, reply, phone, *, allow_pong=False):
        value = reply.result
        synthetic = reply.control == 'pong' and allow_pong
        if synthetic:
            # The official client uses its HTTP request's LOCAL message id here;
            # pong.msg_id/ping_id are not substituted as a server code hash.
            value = {'_': 'auth.sentCode', 'type': {'_': 'auth.sentCodeTypeSms', 'length': 6},
                     'phone_code_hash': str(reply.request_id)}
        if not isinstance(value, dict) or value.get('_') != 'auth.sentCode':
            self._uncertain = True
            raise AuthenticationError('Unexpected OTP response; code delivery is unconfirmed')
        kind = value.get('type', {})
        code_hash, length, timeout = value.get('phone_code_hash'), kind.get('length'), value.get('timeout')
        if (not isinstance(code_hash, str) or not 0 < len(code_hash) <= 1024
                or not isinstance(kind.get('_'), str)
                or (length is not None and (type(length) is not int or not 1 <= length <= 32))
                or (timeout is not None and (type(timeout) is not int or not 0 <= timeout <= 86400))):
            self._uncertain = True
            raise AuthenticationError('Invalid OTP response fields')
        challenge = CodeRequest(kind['_'].removeprefix('auth.sentCodeType'), length, timeout,
                                synthetic, phone, code_hash)
        self._challenge = challenge
        return challenge

    async def resend_code(self):
        """Explicit resend only; server cooldown/flood errors are propagated."""
        async with self._lock:
            self._ready()
            if self._challenge is None or self._password_code is not None or self._session is not None:
                raise AuthenticationError('No pending OTP challenge to resend')
            challenge = self._challenge
            reply = await self._auth_rpc('auth.resendCode', {
                'phone_number': challenge.phone_number, 'phone_code_hash': challenge.phone_code_hash,
            })
            return self._accept_code(reply, challenge.phone_number)

    async def sign_in(self, code):
        if not isinstance(code, str) or not re.fullmatch(r'[0-9]{1,32}', code := _digits(code.strip())):
            raise ValueError('OTP must be a string of digits')
        async with self._lock:
            self._ready()
            challenge = self._challenge
            if challenge is None or self._password_code is not None:
                raise AuthenticationError('Request a code before sign_in')
            try:
                reply = await self._auth_rpc('auth.signIn', {
                    'phone_number': challenge.phone_number, 'phone_code_hash': challenge.phone_code_hash,
                    'phone_code': code,
                })
            except AccountRPCError as exc:
                if exc.rpc_type == 'SESSION_PASSWORD_NEEDED':
                    self._password_code = code
                    raise PasswordRequiredError('Call sign_in_password to finish login') from None
                if exc.rpc_type in ('PHONE_CODE_EXPIRED', 'PHONE_CODE_HASH_EMPTY', 'PHONE_CODE_HASH_INVALID'):
                    self._challenge = None
                raise
            return await self._finish_login(reply.result)

    async def sign_in_password(self, password):
        async with self._lock:
            self._ready()
            if self._password_code is None or self._challenge is None:
                raise AuthenticationError('No password challenge; submit the OTP first')
            # Fresh state for every attempt: never reuse an expired SRP challenge.
            state = (await self._auth_rpc('account.getPassword')).result
            if not isinstance(state, dict):
                raise AuthenticationError('Invalid password state')
            if state.get('_') == 'account.password2':
                digest = legacy_password_hash(password, state.get('current_salt'))
                reply = await self._auth_rpc('auth.checkPassword2', {
                    'password_hash': digest, 'phone_number': self._challenge.phone_number,
                    'phone_code': self._password_code,
                })
            elif state.get('_') == 'account.password':
                proof = await asyncio.to_thread(srp_password_proof, password, state)
                reply = await self._auth_rpc('auth.checkPassword', {'password': proof})
            else:
                raise UnsupportedOperationError('Unsupported login password state')
            return await self._finish_login(reply.result)

    async def _finish_login(self, value):
        if isinstance(value, dict) and value.get('_') == 'auth.authorizationSignUpRequired':
            self._challenge = self._password_code = None
            raise UnsupportedOperationError('Account registration requires a separate explicit flow')
        if not isinstance(value, dict) or value.get('_') != 'auth.authorization':
            self._uncertain = True
            raise AuthenticationError('Server did not return an account authorization')
        user = value.get('user')
        if (not isinstance(user, dict) or user.get('_') != 'user'
                or type(user.get('id')) is not int or user['id'] <= 0):
            self._uncertain = True
            raise AuthenticationError('Authorization has no valid account identity')
        if self._expected_login_user is not None and user['id'] != self._expected_login_user:
            self._uncertain = True
            self._challenge = self._password_code = None
            raise SessionError('Login belongs to a different account; use a different session file')
        try:
            session = AccountSession(value.get('token'), self._rpc_transport()._device_id,
                                     user['id'], self._endpoint, self._api_id)
            self._store.save(session)
            self._rpc_transport().set_token(session.token)
        except (SessionError, ValueError):
            self._uncertain = True
            self._challenge = self._password_code = None
            raise
        self._session = session
        self._challenge = self._password_code = None
        # Verify the token with a real identity read before reporting success.
        await self._get_me()
        self._authorized = True
        self._expected_login_user = None
        return user

    async def _refresh_token(self):
        try:
            reply = await self._rpc_transport().invoke('eitaaRefreshToken', {'app_info': {
                '_': 'eitaaAppInfo', 'build_version': self._api_id,
                'device_model': 'Fnc-Eitaa Python', 'system_version': 'Python',
                'app_version': '0.1.0', 'lang_code': 'en', 'sign': '',
            }})
        except BaseException:
            self._authorized = False
            self._uncertain = True
            raise
        value = reply.result
        if reply.control != 'eitaa_updates_token' or not isinstance(value, dict):
            self._authorized = False
            self._uncertain = True
            raise AuthenticationError('Token renewal did not return a replacement token')
        try:
            session = replace(self._current_session(), token=value.get('token'))
            self._store.save(session)
            self._rpc_transport().set_token(session.token)
        except (SessionError, ValueError):
            self._authorized = False
            self._uncertain = True
            raise
        self._session = session

    async def _get_me(self):
        try:
            try:
                reply = await self._rpc_transport().invoke('users.getUsers', {'id': [{'_': 'inputUserSelf'}]})
            except AccountStateError:
                await self._refresh_token()
                reply = await self._rpc_transport().invoke('users.getUsers', {'id': [{'_': 'inputUserSelf'}]})
        except AccountRPCError as exc:
            if exc.rpc_type in REVOKED:
                self._authorized = False
                raise SessionRevokedError('Saved account session was rejected; file retained') from None
            raise
        except AccountStateError:
            self._authorized = False
            raise
        value = reply.result
        if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict)
                or value[0].get('_') != 'user' or type(value[0].get('id')) is not int
                or value[0]['id'] != self._current_session().user_id):
            self._authorized = False
            raise TLCodecError('Account identity response does not match the saved session')
        self._cache_entities([value[0]])
        return value[0]

    async def get_me(self):
        async with self._lock:
            self._ready()
            if self._session is None or not self._authorized:
                raise AuthenticationError('Account is not authorized')
            return await self._get_me()

    def _require_authorized(self):
        self._ready()
        if not self.is_authorized:
            raise AuthenticationError('Account is not authorized; log in locally first')

    async def _read_rpc(self, method, params):
        """Only read methods use one explicit token renewal on expiry signals."""
        try:
            try:
                return await self._rpc_transport().invoke(method, params)
            except AccountStateError:
                await self._refresh_token()
                return await self._rpc_transport().invoke(method, params)
        except AccountRPCError as exc:
            if exc.rpc_type in REVOKED:
                self._authorized = False
                raise SessionRevokedError('Saved account session was rejected; file retained') from None
            raise
        except AccountStateError:
            self._authorized = False
            raise

    def _cache_entities(self, entities):
        for entity in entities:
            peer = entity_peer(entity)
            if peer is None:
                continue
            previous = self._peers.get(peer.key)
            if previous is not None and not previous.is_min and peer.is_min:
                continue
            self._peers[peer.key] = peer
            self._peers.move_to_end(peer.key)
            if len(self._peers) > 10000:
                self._peers.popitem(last=False)

    def _cached_peer(self, key):
        peer = self._peers.get(key)
        if peer is None:
            raise PeerResolveError('Unknown peer ID; resolve its username or load dialogs first')
        peer.to_input_peer()
        return peer

    async def resolve_peer(self, peer: str | int | AccountPeer) -> AccountPeer:
        """Resolve self, username, cached ID, or typed user:/chat:/channel:ID.

        Bare IDs are never guessed as a particular peer type. Access hashes
        belong to this account; use AccountPeer values with their owning account.
        """
        async with self._lock:
            self._require_authorized()
            if isinstance(peer, AccountPeer):
                if peer.is_self and peer.id != self._current_session().user_id:
                    raise PeerResolveError('Self peer belongs to a different account')
                peer.to_input_peer()
                return peer
            if isinstance(peer, str):
                peer = peer.strip()
                if peer.lower() in ('me', 'self'):
                    return AccountPeer(self._current_session().user_id, is_self=True)
                typed = re.fullmatch(r'(user|chat|channel):([1-9][0-9]{0,18})', peer)
                if typed:
                    kind, identifier = PeerKind(typed[1]), int(typed[2])
                    if not positive_id(identifier):
                        raise ValueError('Peer ID is outside its wire range')
                    if kind == PeerKind.CHAT:
                        return self._peers.get((kind, identifier), AccountPeer(identifier, kind))
                    if kind == PeerKind.USER and identifier == self._current_session().user_id:
                        return AccountPeer(identifier, is_self=True)
                    return self._cached_peer((kind, identifier))
                username = peer.removeprefix('@')
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', username):
                    raise ValueError('Use a username, self, a cached positive ID, or kind:ID')
                value = (await self._read_rpc('contacts.resolveUsername', {'username': username.lower()})).result
                if not isinstance(value, dict) or value.get('_') != 'contacts.resolvedPeer':
                    raise TLCodecError('Unexpected username resolution response')
                key = peer_key(value['peer'])
                entities = value['users'] + value['chats']
                if not any((candidate := entity_peer(entity)) is not None and candidate.key == key
                           for entity in entities):
                    raise TLCodecError('Username response omitted the resolved identity')
                self._cache_entities(entities)
                return self._cached_peer(key)
            if not positive_id(peer):
                raise ValueError('Peer ID must be a positive signed 64-bit integer')
            candidates = [value for (_, identifier), value in self._peers.items() if identifier == peer]
            if peer == self._current_session().user_id and not any(p.kind == PeerKind.USER for p in candidates):
                candidates.append(AccountPeer(peer, is_self=True))
            if len(candidates) != 1:
                raise PeerResolveError('Unknown or ambiguous peer ID; use username or kind:ID')
            candidates[0].to_input_peer()
            return candidates[0]

    async def get_dialogs_page(self, *, limit: int = 100, cursor: DialogCursor | None = None,
                               folder_id: int | None = None) -> DialogPage:
        """Read one page. folder_id: None=all, 0=main, 1=archive."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Dialog page limit must be between 1 and 100')
        if folder_id is not None and (type(folder_id) is not int or folder_id not in (0, 1)):
            raise ValueError('folder_id must be None, 0 or 1')
        if cursor is not None and not isinstance(cursor, DialogCursor):
            raise ValueError('cursor must be a DialogCursor')
        if cursor is not None and folder_id != cursor.folder_id:
            raise ValueError('Use the same folder_id for every cursor page')
        params = {'offset_date': cursor.offset_date if cursor else 0,
                  'offset_id': cursor.offset_id if cursor else 0,
                  'offset_peer': cursor.peer.to_input_peer() if cursor and cursor.peer else {'_': 'inputPeerEmpty'},
                  'limit': limit, 'hash': 0}
        if folder_id is not None:
            params['folder_id'] = folder_id
        if cursor is not None:
            params['exclude_pinned'] = True
        async with self._lock:
            self._require_authorized()
            if cursor and cursor.peer and cursor.peer.is_self and cursor.peer.id != self._current_session().user_id:
                raise PeerResolveError('Dialog cursor self peer belongs to a different account')
            value = (await self._read_rpc('messages.getDialogs', params)).result
            if not isinstance(value, dict) or value.get('_') not in ('messages.dialogs', 'messages.dialogsSlice'):
                raise TLCodecError('Unexpected dialogs response for uncached request')
            self._cache_entities(value['users'] + value['chats'])
            dialogs = []
            for item in value['dialogs']:
                if item.get('_') == 'dialogFolder':
                    continue  # Folder summaries are not conversations.
                if item.get('_') != 'dialog':
                    raise TLCodecError('Unsupported dialog constructor')
                key = peer_key(item['peer'])
                peer = self._peers.get(key) or AccountPeer(key[1], key[0])
                dialogs.append(AccountDialog(peer, item['top_message'], item['unread_count'],
                                             bool(item.get('pFlags', {}).get('pinned')),
                                             item.get('folder_id', folder_id or 0)))
            count = value.get('count', len(dialogs))
            if type(count) is not int or count < 0:
                raise TLCodecError('Invalid dialog count')
            next_cursor = None
            if value['_'] == 'messages.dialogsSlice' and value['dialogs']:
                unpinned = [item for item in dialogs if not item.pinned]
                if not unpinned:
                    # A small first page can contain only pinned conversations
                    # or a folder summary. Start the unpinned stream explicitly.
                    next_cursor = DialogCursor(0, 0, None, folder_id)
                    if cursor is not None:
                        raise TLCodecError('Dialog pagination returned no unpinned anchor')
                    return DialogPage(tuple(dialogs), count, next_cursor)
                last = unpinned[-1]
                dates = [message['date'] for message in value['messages']
                         if message.get('_') in ('message', 'messageService')
                         and message.get('id') == last.top_message_id
                         and peer_key(message.get('peer_id')) == last.peer.key]
                if len(dates) != 1:
                    raise TLCodecError('Dialog pagination needs its matching top message date')
                try:
                    next_cursor = DialogCursor(dates[0], last.top_message_id, last.peer, folder_id)
                except (ValueError, PeerResolveError):
                    raise TLCodecError('Dialog pagination anchor is not addressable') from None
                if next_cursor == cursor:
                    raise TLCodecError('Dialog pagination did not advance')
            return DialogPage(tuple(dialogs), count, next_cursor)

    async def iter_dialogs(self, *, limit: int | None = None, page_size: int = 100,
                           folder_id: int | None = None):
        """Yield unique dialogs; read another page only when the consumer asks."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError('Dialog limit must be a nonnegative integer or None')
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('Dialog page_size must be between 1 and 100')
        seen, cursors = set(), set()
        cursor = None
        yielded = 0
        while limit is None or yielded < limit:
            size = page_size if limit is None else min(page_size, limit - yielded)
            page = await self.get_dialogs_page(limit=size, cursor=cursor, folder_id=folder_id)
            for dialog in page.dialogs:
                if dialog.peer.key in seen:
                    continue
                seen.add(dialog.peer.key)
                yielded += 1
                yield dialog
                if limit is not None and yielded >= limit:
                    return
            cursor = page.next_cursor
            if cursor is None:
                return
            if cursor in cursors:
                raise TLCodecError('Dialog pagination repeated a cursor')
            cursors.add(cursor)

    async def get_dialogs(self, limit: int = 100, *, folder_id: int | None = None) -> list[AccountDialog]:
        """Return up to limit dialogs; use iter_dialogs for lazy iteration."""
        return [dialog async for dialog in self.iter_dialogs(limit=limit, folder_id=folder_id)]

    def _message_owner(self, owner_id):
        self._require_authorized()
        if owner_id != self._current_session().user_id:
            raise PeerResolveError('Message peer belongs to a different account connection')

    def _message_result(self, value):
        if not isinstance(value, dict) or value.get('_') not in (
                'messages.messages', 'messages.messagesSlice', 'messages.channelMessages'):
            raise TLCodecError('Unexpected uncached account messages response')
        self._cache_entities(value['users'] + value['chats'])
        return value['messages']

    async def get_message(self, peer: str | int | AccountPeer, message_id: int) -> AccountMessage | None:
        """Read one message. Empty/deleted or another peer's message returns None."""
        if type(message_id) is not int or not 0 < message_id < 2**31:
            raise ValueError('message_id must be a positive signed 32-bit integer')
        resolved = await self.resolve_peer(peer)
        owner = self._current_session().user_id
        params: dict[str, Any] = {'id': [{'_': 'inputMessageID', 'id': message_id}]}
        method = 'messages.getMessages'
        if resolved.kind == PeerKind.CHANNEL:
            method = 'channels.getMessages'
            params['channel'] = {'_': 'inputChannel', 'channel_id': resolved.id,
                                 'access_hash': resolved.access_hash}
        async with self._lock:
            self._message_owner(owner)
            values = self._message_result((await self._read_rpc(method, params)).result)
            if len(values) > 1:
                raise TLCodecError('Single-message lookup returned multiple messages')
            if not values:
                return None
            value = values[0]
            if value.get('id') != message_id:
                raise TLCodecError('Single-message lookup returned an unrelated ID')
            if value.get('_') == 'messageEmpty':
                return None
            # User/basic-chat message IDs share a namespace on the account.
            # Never expose a different conversation just because its ID matched.
            if peer_key(value.get('peer_id')) != resolved.key:
                return None
            return parse_message(value, resolved, self._peers)

    async def get_history_page(self, peer: str | int | AccountPeer, *, limit: int = 100,
                               offset_id: int = 0) -> HistoryPage:
        """Read newest first, strictly before offset_id (zero starts at newest)."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('History page limit must be between 1 and 100')
        if type(offset_id) is not int or not 0 <= offset_id < 2**31:
            raise ValueError('offset_id must be a nonnegative signed 32-bit integer')
        resolved = await self.resolve_peer(peer)
        owner = self._current_session().user_id
        params = {'peer': resolved.to_input_peer(), 'offset_id': offset_id,
                  'offset_date': 0, 'add_offset': 0, 'limit': limit,
                  'max_id': 0, 'min_id': 0, 'hash': 0}
        async with self._lock:
            self._message_owner(owner)
            value = (await self._read_rpc('messages.getHistory', params)).result
            values = self._message_result(value)
            ids, messages = [], {}
            for item in values:
                parsed = parse_message(item, resolved, self._peers)
                identifier = item['id']
                if offset_id and identifier >= offset_id:
                    raise TLCodecError('History response did not respect the requested offset')
                ids.append(identifier)
                if parsed is not None:
                    messages[identifier] = parsed
            count = value.get('count')
            if count is not None and (type(count) is not int or count < 0):
                raise TLCodecError('Invalid history count')
            # Empty placeholders still move the cursor past deleted messages.
            next_offset = min(ids) if ids and value['_'] != 'messages.messages' else None
            if next_offset == 1:
                next_offset = None
            ordered = tuple(messages[key] for key in sorted(messages, reverse=True))
            return HistoryPage(resolved, ordered, count, next_offset)

    async def iter_history(self, peer: str | int | AccountPeer, *, limit: int | None = None,
                           page_size: int = 100, offset_id: int = 0):
        """Yield newest first; fetch pages lazily and never mark messages read."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError('History limit must be a nonnegative integer or None')
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('History page_size must be between 1 and 100')
        if type(offset_id) is not int or not 0 <= offset_id < 2**31:
            raise ValueError('Invalid history offset_id')
        if limit == 0:
            return
        resolved = await self.resolve_peer(peer)
        owner = self._current_session().user_id
        yielded = 0
        while limit is None or yielded < limit:
            self._message_owner(owner)
            size = page_size if limit is None else min(page_size, limit - yielded)
            page = await self.get_history_page(resolved, limit=size, offset_id=offset_id)
            for message in page.messages:
                yield message
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if page.next_offset_id is None:
                return
            if offset_id and page.next_offset_id >= offset_id:
                raise TLCodecError('History pagination did not advance')
            offset_id = page.next_offset_id

    async def get_history(self, peer: str | int | AccountPeer, limit: int = 100,
                          *, offset_id: int = 0) -> list[AccountMessage]:
        return [message async for message in self.iter_history(peer, limit=limit, offset_id=offset_id)]

    async def _verified_operation_messages(self, peer, identifiers):
        """Read before writing; common message IDs are not scoped by peer on wire.

        Caller holds _lock. A failed read is not an uncertain mutation because
        no mutation has started yet. This is not a transaction with the server.
        """
        params: dict[str, Any] = {'id': [{'_': 'inputMessageID', 'id': mid} for mid in identifiers]}
        method = 'messages.getMessages'
        if peer.kind == PeerKind.CHANNEL:
            method = 'channels.getMessages'
            params['channel'] = {'_': 'inputChannel', 'channel_id': peer.id,
                                 'access_hash': peer.access_hash}
        values = self._message_result((await self._read_rpc(method, params)).result)
        requested, found = set(identifiers), {}
        for value in values:
            if not isinstance(value, dict) or value.get('id') not in requested or value['id'] in found:
                raise TLCodecError('Source verification returned unrelated or duplicate message IDs')
            if value.get('_') == 'messageEmpty' or peer_key(value.get('peer_id')) != peer.key:
                raise PeerResolveError('A source message is unavailable in the selected conversation')
            parse_message(value, peer, self._peers)
            found[value['id']] = value
        if set(found) != requested:
            raise PeerResolveError('Not all source messages are available in the selected conversation')
        return [found[mid] for mid in identifiers]

    async def _message_operation(self, operation, method, params, identifiers, random_ids, decode, *, transport=None):
        """Caller holds _lock. Preflight locally, invoke exactly once, match ACK."""
        transport = transport if transport is not None else self._rpc_transport()
        transport.encode_request(method, params)
        try:
            reply = await transport.invoke(method, params)
            value = reply.result
            if isinstance(value, dict) and value.get('_') in ('updates', 'updatesCombined'):
                self._cache_entities(value.get('users', []) + value.get('chats', []))
            return decode(value)
        except asyncio.CancelledError:
            raise MessageOperationCancelledError(operation, identifiers, random_ids) from None
        except AccountRPCError as exc:
            if exc.rpc_type in REVOKED:
                self._authorized = False
                raise SessionRevokedError('Saved account session was rejected; file retained') from None
            raise
        except Exception:
            raise MessageOperationUncertainError(operation, identifiers, random_ids) from None

    async def edit_message(self, peer: str | int | AccountPeer, message_id: int, text: str, *,
                           no_webpage: bool = False) -> AccountMessage:
        """Replace text/caption and clear styled entities; no automatic retry.

        Media attachments are not replaced. Permissions/edit deadlines remain
        server enforced. MESSAGE_NOT_MODIFIED is an explicit RPC error.
        """
        validate_text(text)
        identifiers = validate_ids([message_id])
        if type(no_webpage) is not bool:
            raise ValueError('no_webpage must be bool')
        resolved = await self.resolve_peer(peer)
        owner = self._current_session().user_id
        params = dict(peer=resolved.to_input_peer(), id=message_id, message=text,
                      entities=[], no_webpage=no_webpage)
        async with self._lock:
            self._message_owner(owner)
            return await self._message_operation('edit', 'messages.editMessage', params, identifiers, (),
                lambda value: correlate_edit(value, resolved, message_id, text, self._peers))

    async def delete_messages(self, peer: str | int | AccountPeer, message_ids, *,
                              revoke: bool = False) -> DeletedMessages:
        """Delete 1..100 verified messages in one RPC, without automatic splitting.

        revoke=False requests local deletion in user/basic-group chats. Channel
        deletion is shared and requires explicit revoke=True. AffectedMessages
        acknowledges the RPC; it does not enumerate which messages disappeared.
        """
        identifiers = validate_ids(message_ids)
        if type(revoke) is not bool:
            raise ValueError('revoke must be bool')
        resolved = await self.resolve_peer(peer)
        owner = self._current_session().user_id
        method = 'messages.deleteMessages'
        params: dict[str, Any] = {'id': list(identifiers), 'revoke': revoke}
        if resolved.kind == PeerKind.CHANNEL:
            if not revoke:
                raise ValueError('Channel deletion is shared; pass revoke=True explicitly')
            method = 'channels.deleteMessages'
            params = {'channel': {'_': 'inputChannel', 'channel_id': resolved.id,
                                  'access_hash': resolved.access_hash}, 'id': list(identifiers)}
        async with self._lock:
            self._message_owner(owner)
            await self._verified_operation_messages(resolved, identifiers)
            return await self._message_operation('delete', method, params, identifiers, (),
                lambda value: correlate_delete(value, resolved, identifiers, revoke))

    async def _forward_messages(self, to_peer, from_peer, message_ids, *, random_ids, silent, is_copy):
        identifiers = validate_ids(message_ids)
        tokens = operation_random_ids(len(identifiers), random_ids)
        if type(silent) is not bool:
            raise ValueError('silent must be bool')
        source = await self.resolve_peer(from_peer)
        owner = self._current_session().user_id
        destination = await self.resolve_peer(to_peer)
        params = dict(from_peer=source.to_input_peer(), id=list(identifiers), random_id=list(tokens),
                      to_peer=destination.to_input_peer(), silent=silent, drop_author=is_copy)
        async with self._lock:
            self._message_owner(owner)
            messages = await self._verified_operation_messages(source, identifiers)
            if any(item.get('_') == 'messageService' or item.get('pFlags', {}).get('noforwards')
                   for item in messages):
                raise UnsupportedOperationError('Service or protected messages cannot be forwarded/copied')
            return await self._message_operation('copy' if is_copy else 'forward',
                'messages.forwardMessages', params, identifiers, tokens,
                lambda value: correlate_forward(value, source, destination, identifiers, tokens,
                                                self._peers, is_copy=is_copy))

    async def forward_messages(self, to_peer: str | int | AccountPeer,
                               from_peer: str | int | AccountPeer, message_ids, *,
                               random_ids=None, silent: bool = False) -> tuple[ForwardedMessage, ...]:
        """Forward 1..100 source messages; receipts preserve input order.

        Supply random_ids if recovery across a crash matters. No write retries,
        automatic splitting, or server deduplication lifetime is promised.
        """
        return await self._forward_messages(to_peer, from_peer, message_ids,
            random_ids=random_ids, silent=silent, is_copy=False)

    async def forward_message(self, to_peer: str | int | AccountPeer,
                              from_peer: str | int | AccountPeer, message_id: int, *,
                              random_id: int | None = None, silent: bool = False) -> ForwardedMessage:
        result = await self.forward_messages(to_peer, from_peer, [message_id],
            random_ids=None if random_id is None else [random_id], silent=silent)
        return result[0]

    async def copy_message(self, to_peer: str | int | AccountPeer,
                           from_peer: str | int | AccountPeer, message_id: int, *,
                           random_id: int | None = None, silent: bool = False) -> ForwardedMessage:
        """Server-side forward with drop_author=True; never download/reupload.

        Server restrictions still apply. Album grouping and media transfer are
        separate features. A receipt may omit the full destination message.
        """
        result = await self._forward_messages(to_peer, from_peer, [message_id],
            random_ids=None if random_id is None else [random_id], silent=silent, is_copy=True)
        return result[0]

    async def send_message(self, peer: str | int | AccountPeer, text: str, *,
                           reply_to_msg_id: int | None = None, silent: bool = False,
                           no_webpage: bool = False, random_id: int | None = None) -> AccountMessage:
        """Send one plain-text message, once. No splitting or automatic resend.

        Supply/store random_id before calling if you need recovery across a
        process crash. Server-side deduplication lifetime is not guaranteed.
        """
        if not isinstance(text, str) or not text:
            raise ValueError('Message text must be a nonempty string')
        if len(text) > 4096:
            raise ValueError('This SDK sends at most 4096 UTF-16 units in one message')
        try:
            units = len(text.encode('utf-16-le')) // 2
        except UnicodeEncodeError:
            raise ValueError('Message text contains invalid Unicode') from None
        if units > 4096:
            raise ValueError('This SDK sends at most 4096 UTF-16 units in one message')
        if reply_to_msg_id is not None and not message_id(reply_to_msg_id):
            raise ValueError('reply_to_msg_id must be a positive signed 32-bit integer')
        if type(silent) is not bool or type(no_webpage) is not bool:
            raise ValueError('silent and no_webpage must be bool')
        if random_id is None:
            random_id = secrets.randbits(63) or 1
        if type(random_id) is not int or not -2**63 <= random_id < 2**63 or random_id == 0:
            raise ValueError('random_id must be a nonzero signed 64-bit integer')
        resolved = await self.resolve_peer(peer)
        owner = self._current_session().user_id
        params = {'peer': resolved.to_input_peer(), 'message': text, 'random_id': random_id,
                  'silent': silent, 'no_webpage': no_webpage}
        if reply_to_msg_id is not None:
            params['reply_to_msg_id'] = reply_to_msg_id
        async with self._lock:
            self._message_owner(owner)
            transport = self._rpc_transport()
            # Fail local encoding/disabled-method checks before a possible write.
            transport.encode_request('messages.sendMessage', params)
            try:
                reply = await transport.invoke('messages.sendMessage', params)
                value = reply.result
                if isinstance(value, dict) and value.get('_') in ('updates', 'updatesCombined'):
                    self._cache_entities(value.get('users', []) + value.get('chats', []))
                return correlate_sent(value, resolved, random_id, text, reply_to_msg_id, self._peers)
            except asyncio.CancelledError:
                raise SendMessageCancelledError(random_id) from None
            except AccountRPCError as exc:
                if exc.rpc_type in REVOKED:
                    self._authorized = False
                    raise SessionRevokedError('Saved account session was rejected; file retained') from None
                raise
            except Exception:
                # Includes HTTP failure, invalid response and token-control ACKs.
                # A new random ID here could create a duplicate real message.
                raise SendMessageUncertainError(random_id) from None
