"""Experimental sequential account RPC transport; login lives in account_client."""
import asyncio
from dataclasses import dataclass, field
from importlib.resources import files
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

import requests

from .errors import (AccountRPCError, AccountStateError, AccountTransportError,
                     UnsupportedOperationError)
from .protocol import TLCodec, TLCodecError

DEFAULT_ENDPOINT = 'https://hasan.eitaa.ir/eitaa/'
ACCOUNT_USER_AGENT = 'Fnc-Eitaa/0.1.0'
TOKEN_SIGNALS = {'eitaa_updates_expire_token', 'eitaa_token_updating'}


@dataclass(frozen=True)
class AccountReply:
    request_id: int
    result: Any = field(repr=False)
    updates: tuple = field(default=(), repr=False)
    control: str | None = None
    authorization_tail_bytes: int = 0


def _rpc_failure(value):
    if not isinstance(value, dict):
        return
    name = value.get('_')
    if name in ('error', 'eitta_error', 'rpc_error'):
        code = value['error_code'] if name == 'rpc_error' else value['code']
        text = value['error_message'] if name == 'rpc_error' else value['text']
        # Arbitrary remote text could echo credentials; only expose symbolic errors.
        symbol = text if re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', text) else 'REMOTE_ERROR'
        raise AccountRPCError(code, symbol)
    if name in TOKEN_SIGNALS:
        raise AccountStateError(name)


def correlate(value, request_id):
    """Only an exactly matching rpc_result can be accepted from a container."""
    _rpc_failure(value)

    def reply(result, updates=()):
        _rpc_failure(result)
        name = result.get('_') if isinstance(result, dict) else None
        if name in ('rpc_result', 'msg_container'):
            raise TLCodecError('Unexpected nested response envelope')
        control = name if name in ('pong', 'eitaa_updates_token') else None
        return AccountReply(request_id, result, updates, control)

    if not isinstance(value, dict):
        return reply(value)
    if value.get('_') == 'rpc_result':
        if value['req_msg_id'] != request_id:
            raise TLCodecError('RPC response belongs to another request')
        return reply(value['result'])
    if value.get('_') != 'msg_container':
        return reply(value)
    matches = []
    updates = []

    def visit(body):
        if not isinstance(body, dict):
            raise TLCodecError('Invalid uncorrelated container body')
        name = body.get('_')
        if name == 'msg_container':
            for message in body['messages']:
                visit(message['body'])
        elif name == 'rpc_result':
            if body['req_msg_id'] != request_id:
                raise TLCodecError('Container contains an unrelated RPC result')
            matches.append(body['result'])
        elif isinstance(name, str) and (name.startswith('update') or name.startswith('eitaa_')
                                       or name in ('msgs_ack', 'pong', 'new_session_created')):
            updates.append(body)
        else:
            raise TLCodecError('Container body cannot be correlated to this request')

    visit(value)
    if len(matches) != 1:
        for update in updates:
            _rpc_failure(update)
        raise TLCodecError('Container must contain exactly one matching RPC result')
    _rpc_failure(matches[0])
    for update in updates:
        _rpc_failure(update)
    return reply(matches[0], tuple(updates))


class AccountHTTPTransport:
    """Bounded HTTP; one in-flight RPC, no automatic retries or token renewal.

    `token` is an Eitaa ACCOUNT token, never an Eitaayar token. A cancelled await
    cannot recall HTTP already transmitted; its slot stays occupied until the
    worker exits. Treat interrupted writes as potentially uncertain.
    """

    def __init__(self, *, device_id: str, token: str = '', endpoint=DEFAULT_ENDPOINT,
                 codec=None, connect_timeout=10.0, read_timeout=10.0, deadline=30.0):
        if not isinstance(device_id, str) or not device_id or len(device_id) > 128:
            raise ValueError('device_id must be a nonempty string of at most 128 characters')
        if not isinstance(token, str):
            raise ValueError('Account token must be a string')
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.path != '/eitaa/'
                or parsed.query or parsed.fragment
                or not any(host == domain or host.endswith('.' + domain)
                           for domain in ('eitaa.com', 'eitaa.ir'))):
            raise ValueError('Use an HTTPS Eitaa endpoint with /eitaa/ path')
        for value in (connect_timeout, read_timeout, deadline):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 60:
                raise ValueError('Timeouts must be finite, positive and at most 60 seconds')
        self.endpoint = endpoint
        self._token = token
        self._device_id = device_id
        self.codec = codec if codec is not None else TLCodec()
        config = json.loads(files('fnceitaa.protocol').joinpath('config.json').read_text(encoding='utf-8'))
        if config['source_sha256'] != self.codec.schema.source_sha256:
            raise ValueError('Protocol schema and transport configuration do not match')
        self._blocked = frozenset(config['blocked_methods'])
        self._timeout = (connect_timeout, read_timeout)
        self._deadline = deadline
        self._lock = asyncio.Lock()
        self._last_request_id = 0

    def __repr__(self):
        return f'AccountHTTPTransport(endpoint={self.endpoint!r})'

    def set_token(self, token: str):
        """Replace credentials between RPCs, after durable session storage."""
        if not isinstance(token, str) or not token or len(token) > 16384:
            raise ValueError('Invalid account token')
        if self._lock.locked():
            raise RuntimeError('Cannot replace account token during an RPC')
        self._token = token

    def encode_request(self, method, params=None):
        if method in self._blocked:
            raise UnsupportedOperationError('This RPC is disabled in the pinned Eitaa web client')
        body = self.codec.encode_method(method, params)
        return self.codec.encode_method('eitaaObject', {
            'token': self._token, 'imei': self._device_id, 'packed_data': body,
            'layer': self.codec.schema.layer, 'flags': 128,
        })

    def _post(self, payload):
        started = time.monotonic()
        maximum = self.codec.limits.max_payload_bytes
        try:
            with requests.post(self.endpoint, data=payload, stream=True,
                               headers={'Content-Type': 'application/octet-stream',
                                        'User-Agent': ACCOUNT_USER_AGENT},
                               timeout=self._timeout, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise AccountTransportError('Account HTTP request failed', response.status_code)
                length = response.headers.get('Content-Length')
                if length is not None:
                    try:
                        declared = int(length)
                    except (TypeError, ValueError):
                        raise AccountTransportError('Invalid response Content-Length') from None
                    if not 0 <= declared <= maximum:
                        raise AccountTransportError('Account response exceeds size limit')
                data = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() - started > self._deadline:
                        raise AccountTransportError('Account response exceeded elapsed-time limit')
                    if len(data) + len(chunk) > maximum:
                        raise AccountTransportError('Account response exceeds size limit')
                    data.extend(chunk)
                if time.monotonic() - started > self._deadline:
                    raise AccountTransportError('Account response exceeded elapsed-time limit')
                if not data:
                    raise AccountTransportError('Empty account response; outcome may be uncertain')
                return bytes(data)
        except requests.RequestException:
            raise AccountTransportError('Account HTTP request failed; outcome may be uncertain') from None

    async def invoke(self, method, params=None) -> AccountReply:
        payload = self.encode_request(method, params)
        result_type = self.codec.result_type(method, params)
        await self._lock.acquire()
        request_id = max((time.time_ns() * (1 << 32) // 1_000_000_000) & ~3,
                         self._last_request_id + 4)
        self._last_request_id = request_id
        # Shield the worker; cancellation must not free the slot while it is still sending.
        try:
            task = asyncio.create_task(asyncio.to_thread(self._post, payload))
        except BaseException:
            self._lock.release()
            raise

        def completed(future):
            self._lock.release()
            if not future.cancelled():
                future.exception()  # Retrieve late failures after caller cancellation.

        task.add_done_callback(completed)
        data = await asyncio.shield(task)
        tail_bytes = 0
        try:
            try:
                value = self.codec.decode(data, result_type, namespace='MTProto',
                                          result_types={request_id: result_type})
            except TLCodecError as strict_error:
                # The official no-auth-key HTTP parser fetches one TL object
                # without a whole-buffer finish check. Limit compatibility to
                # direct login authorization; all other RPCs stay strict.
                if method not in ('auth.signIn', 'auth.checkPassword', 'auth.checkPassword2'):
                    raise
                try:
                    value, tail_bytes = self.codec.decode_authorization_prefix(data)
                except TLCodecError:
                    raise strict_error from None
        except TLCodecError as exc:
            # Method names are public schema identifiers; params/data may be secrets.
            raise TLCodecError(f'{method}: {exc}') from None
        reply = correlate(value, request_id)
        if tail_bytes:
            return AccountReply(reply.request_id, reply.result, reply.updates,
                                reply.control, tail_bytes)
        return reply
