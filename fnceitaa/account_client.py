"""Experimental phone login and persistent Eitaa user-account sessions."""
import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import re
import time
import unicodedata

from .account import AccountHTTPTransport, DEFAULT_ENDPOINT
from .errors import (AccountRPCError, AccountStateError, AuthenticationError,
                     PasswordRequiredError, SessionError, SessionRevokedError,
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
        raise ValueError('Phone must be an international number, for example +98...')
    value = re.sub(r'[ ()\-]', '', _digits(phone.strip()))
    if value.startswith('00'):
        value = '+' + value[2:]
    if not re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
        raise ValueError('Phone must include + and country code, for example +98...')
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
    A missing session permits explicit request_code/sign_in; connect never sends
    an OTP automatically. Use one client/process per session file.
    """

    def __init__(self, session_path='account.session', *, api_id=WEB_API_ID,
                 api_hash=WEB_API_HASH, endpoint=DEFAULT_ENDPOINT):
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
        self._lock = asyncio.Lock()

    def __repr__(self):
        return f'AccountClient(authorized={self.is_authorized})'

    @property
    def is_authorized(self):
        return self._connected and self._authorized and not self._uncertain

    async def __aenter__(self):
        return await self.connect()

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
            except BaseException:
                self._connected = self._authorized = False
                self._session = self._transport = None
                raise
            self._authorized = True
            return self

    async def disconnect(self):
        """Forget credentials in this object; keep the saved session and remote login."""
        async with self._lock:
            self._connected = self._authorized = False
            self._challenge = self._password_code = None
            self._session = self._transport = None

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
        return value[0]

    async def get_me(self):
        async with self._lock:
            self._ready()
            if self._session is None or not self._authorized:
                raise AuthenticationError('Account is not authorized')
            return await self._get_me()
