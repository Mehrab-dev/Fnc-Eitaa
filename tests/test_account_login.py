import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fnceitaa import (AccountClient, AccountRPCError, AuthenticationError,
                      PasswordRequiredError, SessionError, SessionRevokedError)
from fnceitaa.account import AccountHTTPTransport, AccountReply
from fnceitaa.account_client import create_device_id, normalize_phone
from fnceitaa.errors import AccountStateError, AccountTransportError, UnsupportedOperationError
from fnceitaa.password import legacy_password_hash
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore


USER = {'_': 'user', 'id': 123, 'self': True, 'first_name': 'Fixture'}
SENT = {'_': 'auth.sentCode', 'type': {'_': 'auth.sentCodeTypeSms', 'length': 6},
        'phone_code_hash': 'fixture-code-hash', 'timeout': 120}
AUTH = {'_': 'auth.authorization', 'token': 'fixture-account-token', 'user': USER}
PHONE = '+989120000000'


def reply(value, control=None):
    return AccountReply(1234567890, value, control=control)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, 'account.session')
        self.session = AccountSession('secret-token', 'secret-device', 123,
                                      'https://hasan.eitaa.ir/eitaa/', 2496)

    def test_atomic_roundtrip_and_secret_repr(self):
        store = SessionStore(self.path)
        self.assertIsNone(store.load())
        store.save(self.session)
        self.assertEqual(SessionStore(self.path).load(), self.session)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertNotIn('secret-token', repr(self.session))
        self.assertNotIn('secret-device', repr(self.session))
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failed_replace_keeps_existing_session_and_removes_temp(self):
        store = SessionStore(self.path)
        store.load()
        store.save(self.session)
        before = self.path.read_bytes()
        with patch('fnceitaa.session.os.replace', side_effect=OSError('secret-leak')):
            with self.assertRaises(SessionError) as raised:
                store.save(self.session)
        self.assertNotIn('secret-leak', str(raised.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_corruption_versions_size_and_external_change(self):
        for value in [b'not-json', b'[]', b'{"version": 2}', b'x' * 32769,
                      json.dumps({'version': 1, 'token': 'x'}).encode()]:
            self.path.write_bytes(value)
            with self.assertRaises(SessionError):
                SessionStore(self.path).load()
        self.path.unlink()
        store = SessionStore(self.path)
        store.load()
        self.path.write_text('external data')
        with self.assertRaises(SessionError):
            store.save(self.session)
        self.assertEqual(self.path.read_text(), 'external data')

    def test_invalid_session_values_and_save_without_load(self):
        with self.assertRaises(SessionError):
            AccountSession('', 'device', 123, 'endpoint', 2496)
        with self.assertRaises(SessionError):
            AccountSession('token', 'device', True, 'endpoint', 2496)
        with self.assertRaises(SessionError):
            SessionStore(self.path).save(self.session)

    def test_phone_and_device_match_web_algorithm(self):
        self.assertEqual(normalize_phone('00۹۸ (۹۱۲) ۰۰۰-۰۰۰۰'), PHONE)
        for phone in ('09120000000', '+0989120000000', 'token', None, '+98abc9120000000'):
            with self.assertRaises(ValueError):
                normalize_phone(phone)
        with patch('fnceitaa.account_client.time.time_ns', return_value=1_000_000_000):
            device = create_device_id(PHONE)
        prefix = hashlib.sha256(b'989120000000').hexdigest()[:6]
        self.assertEqual(device, prefix + '000000rs1__web')
        self.assertEqual(len(device), 20)

    def test_legacy_hash_utf8_vector(self):
        # Independently calculated salted SHA-256 vector, not a plaintext RPC.
        self.assertEqual(legacy_password_hash('password', b'salt').hex(),
                         '142842b4c729fae19da47dcebbc739564ada2d9fbbe401905ddd3b964f2befab')
        self.assertEqual(legacy_password_hash('رمز', b'salt'),
                         hashlib.sha256(b'salt' + 'رمز'.encode() + b'salt').digest())


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, 'account.session')
        self.client = AccountClient(self.path)
        self.invoke_patch = patch.object(AccountHTTPTransport, 'invoke')
        self.rpc = self.invoke_patch.start()
        self.addCleanup(self.invoke_patch.stop)
        await self.client.connect()

    async def login(self):
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([USER])]
        await self.client.request_code(PHONE)
        return await self.client.sign_in('123456')

    async def test_otp_session_and_reconnect_without_another_code(self):
        await self.login()
        self.assertTrue(self.client.is_authorized)
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list],
                         ['auth.sendCode', 'auth.signIn', 'users.getUsers'])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['token'], AUTH['token'])
        for secret in ('123456', PHONE, SENT['phone_code_hash']):
            self.assertNotIn(secret, self.path.read_text())
        await self.client.disconnect()
        self.rpc.reset_mock()
        self.rpc.side_effect = [reply([USER])]
        second = await AccountClient(self.path).connect()
        self.assertTrue(second.is_authorized)
        self.rpc.assert_awaited_once_with('users.getUsers', {'id': [{'_': 'inputUserSelf'}]})

    async def test_pong_is_only_an_ack_for_send_code(self):
        self.rpc.side_effect = [reply({'_': 'pong', 'msg_id': 0, 'ping_id': 0}, 'pong')]
        receipt = await self.client.request_code(PHONE)
        self.assertTrue(receipt.synthetic_ack)
        self.assertEqual(receipt.phone_code_hash, '1234567890')
        self.assertFalse(self.client.is_authorized)
        self.assertFalse(self.path.exists())
        self.rpc.side_effect = [reply({'_': 'pong'}, 'pong')]
        with self.assertRaises(AuthenticationError):
            await self.client.sign_in('123456')
        self.assertFalse(self.path.exists())
        self.assertNotIn(PHONE, repr(receipt))

    async def test_send_code_invalid_login_is_not_a_challenge_and_is_never_retried(self):
        self.rpc.side_effect = AccountRPCError(401, 'INVALID_LOGIN')
        with self.assertRaises(AccountRPCError) as raised:
            await self.client.request_code(PHONE)
        self.assertEqual((raised.exception.code, raised.exception.rpc_type), (401, 'INVALID_LOGIN'))
        self.rpc.assert_awaited_once()
        self.assertEqual(self.rpc.call_args.args[0], 'auth.sendCode')
        self.assertFalse(self.client.is_authorized)
        self.assertIsNone(self.client._challenge)
        self.assertIsNone(self.client._session)
        self.assertFalse(self.path.exists())

    async def test_wrong_code_can_retry_expired_code_requires_new_request(self):
        self.rpc.side_effect = [reply(SENT), AccountRPCError(400, 'PHONE_CODE_INVALID'),
                                AccountRPCError(400, 'PHONE_CODE_EXPIRED'), reply(SENT)]
        await self.client.request_code(PHONE)
        for _ in range(2):
            with self.assertRaises(AccountRPCError):
                await self.client.sign_in('123456')
        with self.assertRaises(AuthenticationError):
            await self.client.sign_in('123456')
        await self.client.request_code(PHONE)
        self.assertFalse(self.path.exists())

    async def test_password2_only_sends_salted_hash_and_keeps_otp_ephemeral(self):
        state = {'_': 'account.password2', 'current_salt': b'fixture-salt'}
        self.rpc.side_effect = [reply(SENT), AccountRPCError(401, 'SESSION_PASSWORD_NEEDED'),
                                reply(state), AccountRPCError(400, 'PASSWORD_HASH_INVALID'),
                                reply(state), reply(AUTH), reply([USER])]
        await self.client.request_code(PHONE)
        with self.assertRaises(PasswordRequiredError):
            await self.client.sign_in('123456')
        with self.assertRaises(AccountRPCError):
            await self.client.sign_in_password('bad-password')
        await self.client.sign_in_password('secret-password')
        params = self.rpc.call_args_list[-2].args[1]
        self.assertEqual(params['password_hash'], legacy_password_hash('secret-password', b'fixture-salt'))
        self.assertEqual(params['phone_code'], '123456')
        self.assertNotIn('secret-password', repr(params))
        self.assertNotIn('secret-password', self.path.read_text())
        self.assertIsNone(self.client._password_code)

    async def test_srp_branch_uses_fresh_state_and_never_plaintext(self):
        state = {'_': 'account.password'}
        proof = {'_': 'inputCheckPasswordSRP', 'srp_id': 5, 'A': b'A', 'M1': b'M'}
        self.rpc.side_effect = [reply(SENT), AccountRPCError(401, 'SESSION_PASSWORD_NEEDED'),
                                reply(state), reply(AUTH), reply([USER])]
        await self.client.request_code(PHONE)
        with self.assertRaises(PasswordRequiredError):
            await self.client.sign_in('123456')
        with patch('fnceitaa.account_client.srp_password_proof', return_value=proof) as compute:
            await self.client.sign_in_password('secret-password')
        compute.assert_called_once_with('secret-password', state)
        self.assertEqual(self.rpc.call_args_list[-2].args, ('auth.checkPassword', {'password': proof}))

    async def test_revoked_session_is_retained_and_no_otp_requested(self):
        await self.login()
        before = self.path.read_bytes()
        self.rpc.reset_mock()
        self.rpc.side_effect = [AccountRPCError(401, 'INVALID_LOGIN')]
        with self.assertRaises(SessionRevokedError):
            await AccountClient(self.path).connect()
        self.assertEqual(self.path.read_bytes(), before)
        self.rpc.assert_awaited_once()
        self.assertEqual(self.rpc.call_args.args[0], 'users.getUsers')

    async def test_token_expiry_renews_once_persists_and_retries_identity(self):
        await self.login()
        token_reply = reply({'_': 'eitaa_updates_token', 'token': 'replacement-token',
                             'expire': 100, 'date': 50}, 'eitaa_updates_token')
        self.rpc.reset_mock()
        self.rpc.side_effect = [AccountStateError('eitaa_updates_expire_token'), token_reply, reply([USER])]
        await self.client.get_me()
        self.assertEqual(json.loads(self.path.read_text())['token'], 'replacement-token')
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list],
                         ['users.getUsers', 'eitaaRefreshToken', 'users.getUsers'])
        self.rpc.side_effect = [AccountStateError('eitaa_updates_expire_token'), token_reply,
                                AccountStateError('eitaa_updates_expire_token')]
        with self.assertRaises(AccountStateError):
            await self.client.get_me()

    async def test_token_renewal_write_failure_keeps_previous_file_and_rejects_reuse(self):
        await self.login()
        before = self.path.read_bytes()
        self.rpc.side_effect = [AccountStateError('eitaa_updates_expire_token'),
                                reply({'_': 'eitaa_updates_token', 'token': 'new-token'}, 'eitaa_updates_token')]
        with patch.object(self.client._store, 'save', side_effect=SessionError('write failed')):
            with self.assertRaises(SessionError):
                await self.client.get_me()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.client.is_authorized)
        with self.assertRaises(AuthenticationError):
            await self.client.connect()

    async def test_expired_password_continuation_discards_temporary_code(self):
        self.rpc.side_effect = [reply(SENT), AccountRPCError(401, 'SESSION_PASSWORD_NEEDED'),
                                reply({'_': 'account.password2', 'current_salt': b'salt'}),
                                AccountRPCError(400, 'PHONE_CODE_EXPIRED')]
        await self.client.request_code(PHONE)
        with self.assertRaises(PasswordRequiredError):
            await self.client.sign_in('123456')
        with self.assertRaises(AccountRPCError):
            await self.client.sign_in_password('password')
        self.assertIsNone(self.client._password_code)
        self.assertIsNone(self.client._challenge)

    async def test_save_failure_never_reports_authorized(self):
        self.rpc.side_effect = [reply(SENT), reply(AUTH)]
        await self.client.request_code(PHONE)
        with patch.object(self.client._store, 'save', side_effect=SessionError('write failed')):
            with self.assertRaises(SessionError):
                await self.client.sign_in('123456')
        self.assertFalse(self.client.is_authorized)
        self.assertFalse(self.path.exists())
        with self.assertRaises(AuthenticationError):
            await self.client.sign_in('123456')

    async def test_login_identity_mismatch_does_not_report_success(self):
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([{'_': 'user', 'id': 999}])]
        await self.client.request_code(PHONE)
        with self.assertRaises(TLCodecError):
            await self.client.sign_in('123456')
        self.assertFalse(self.client.is_authorized)

    async def test_bad_authorization_and_signup_do_not_save(self):
        for value in [{'_': 'auth.authorization', 'verified': True},
                      {'_': 'auth.authorization', 'user': USER, 'token': ''},
                      {'_': 'auth.authorizationSignUpRequired'}]:
            client = await AccountClient(self.path).connect()
            self.rpc.side_effect = [reply(SENT), reply(value)]
            await client.request_code(PHONE)
            with self.assertRaises((AuthenticationError, SessionError, UnsupportedOperationError)):
                await client.sign_in('123456')
            self.assertFalse(self.path.exists())
            self.assertFalse(client.is_authorized)

    async def test_cancelled_login_and_uncertain_http_are_not_retried(self):
        for error in (asyncio.CancelledError(), AccountTransportError('timeout')):
            client = await AccountClient(self.path).connect()
            self.rpc.side_effect = error
            with self.assertRaises(type(error)):
                await client.request_code(PHONE)
            count = self.rpc.await_count
            with self.assertRaises(AuthenticationError):
                await client.request_code(PHONE)
            self.assertEqual(self.rpc.await_count, count)
            self.assertFalse(self.path.exists())

    async def test_state_order_validation_and_no_network_for_missing_session(self):
        self.rpc.assert_not_awaited()
        with self.assertRaises(AuthenticationError):
            await self.client.sign_in('123456')
        with self.assertRaises(AuthenticationError):
            await self.client.sign_in_password('password')
        with self.assertRaises(AuthenticationError):
            await self.client.get_me()
        self.rpc.side_effect = [reply(SENT), reply(SENT)]
        await self.client.request_code(PHONE)
        with self.assertRaises(AuthenticationError):
            await self.client.request_code(PHONE)
        await self.client.resend_code()
        self.assertEqual(self.rpc.call_args.args[0], 'auth.resendCode')
        await self.client.disconnect()
        self.assertNotIn(PHONE, repr(self.client))
        with self.assertRaises(AuthenticationError):
            await self.client.request_code(PHONE)


class LoginWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_132_byte_authorization_with_60_byte_suffix_and_reconnect(self):
        from test_account_transport import response_for
        codec = TLCodec()
        user = {'_': 'user', 'id': 123, 'self': True, 'access_hash': 456,
                'first_name': 'f' * 14, 'last_name': 'l' * 18,
                'phone': '98912000000',
                'status': {'_': 'userStatusOffline', 'was_online': 1}}
        authorization = {'_': 'auth.authorization', 'token': 't' * 32, 'user': user}
        prefix = bytearray(codec.encode(authorization))
        # Observed structural metadata only; these are synthetic credentials.
        prefix[4:8] = (0x400).to_bytes(4, 'little')
        self.assertEqual(len(prefix), 132)
        packet = bytes(prefix) + bytes(range(60))
        with self.assertRaises(TLCodecError):
            codec.decode(packet, 'auth.Authorization')
        candidate, tail_bytes = codec.decode_authorization_prefix(packet)
        self.assertEqual(tail_bytes, 60)
        self.assertEqual(candidate['user']['id'], 123)
        identity = codec.encode([user], 'Vector<User>')
        packets = [codec.encode(SENT), packet, identity, identity]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    await client.request_code(PHONE)
                    await client.sign_in('12345')
                    self.assertTrue(client.is_authorized)
                    self.assertEqual(post.call_count, 3)
                async with AccountClient(path) as reconnected:
                    self.assertTrue(reconnected.is_authorized)
                self.assertEqual(post.call_count, 4)
                self.assertIn(b't' * 32, post.call_args.kwargs['data'])

    async def test_authorization_suffix_still_requires_matching_identity(self):
        from test_account_transport import response_for
        codec = TLCodec()
        for verification in (codec.encode([{'_': 'user', 'id': 999}], 'Vector<User>'),
                             codec.encode([USER], 'Vector<User>') + b'\0' * 4):
            packets = [codec.encode(SENT), codec.encode(AUTH) + b'\0' * 60, verification]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory, 'account.session')
                with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                    async with AccountClient(path) as client:
                        await client.request_code(PHONE)
                        with self.assertRaises(TLCodecError):
                            await client.sign_in('12345')
                        self.assertFalse(client.is_authorized)
                    self.assertEqual(post.call_count, 3)

    async def test_binary_http_login_and_legacy_password_decode(self):
        from test_account_transport import response_for
        codec = TLCodec()
        password2 = {'_': 'account.password2', 'current_salt': b'salt', 'new_salt': b'new',
                     'new_secure_salt': b'secure', 'secure_random': b'random',
                     'hint': '', 'email_unconfirmed_pattern': ''}
        data = codec.encode(password2, 'Object')
        self.assertEqual(codec.decode(data, 'account.Password')['_'], 'account.password2')
        with self.assertRaises(TLCodecError):
            codec.decode(data, 'User')
        packets = [codec.encode(SENT, 'Object'), codec.encode(AUTH, 'Object'),
                   codec.encode([USER], 'Vector<User>')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    await client.request_code(PHONE)
                    await client.sign_in('123456')
                    self.assertTrue(client.is_authorized)
                self.assertEqual(post.call_count, 3)
                self.assertIn(b'fixture-account-token', post.call_args.kwargs['data'])
                self.assertNotIn(b'123456', path.read_bytes())

    async def test_password2_over_binary_http_uses_both_optional_flags(self):
        from test_account_transport import response_for
        codec = TLCodec()
        state = {'_': 'account.password2', 'current_salt': b'salt', 'new_salt': b'new',
                 'new_secure_salt': b'secure', 'secure_random': b'random',
                 'hint': '', 'email_unconfirmed_pattern': ''}
        error = {'_': 'error', 'code': 401, 'text': 'SESSION_PASSWORD_NEEDED'}
        packets = [codec.encode(value, kind) for value, kind in [
            (SENT, 'Object'), (error, 'Object'), (state, 'Object'),
            (AUTH, 'Object'), ([USER], 'Vector<User>')]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    await client.request_code(PHONE)
                    with self.assertRaises(PasswordRequiredError):
                        await client.sign_in('123456')
                    await client.sign_in_password('secret-password')
                password_packet = post.call_args_list[3].kwargs['data']
                expected = codec.encode_method('auth.checkPassword2', {
                    'password_hash': legacy_password_hash('secret-password', b'salt'),
                    'phone_code': '123456', 'phone_number': PHONE,
                })
                self.assertIn(expected, password_packet)
                self.assertEqual(expected[40:44], b'\x03\x00\x00\x00')
                self.assertNotIn(b'secret-password', password_packet)


class LoginExampleTests(unittest.TestCase):
    def test_invalid_phone_reprompts_locally_without_network_or_confirmation(self):
        from examples.account_login import read_phone
        with patch('examples.account_login.secret', side_effect=['09120000000', '', '+989120000000']), \
                patch('builtins.print') as output, patch('builtins.input') as confirmation, \
                patch('fnceitaa.account.requests.post') as network:
            self.assertEqual(read_phone(), PHONE)
        network.assert_not_called()
        confirmation.assert_not_called()
        self.assertNotIn('09120000000', repr(output.call_args_list))

    def test_default_example_never_calls_network_or_reads_secrets(self):
        from examples.account_login import main
        with patch('sys.argv', ['account_login']), patch('builtins.print'), \
                patch('fnceitaa.account.requests.post') as network, \
                patch('getpass.getpass') as prompt:
            main()
        network.assert_not_called()
        prompt.assert_not_called()


class LoginExampleLiveFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_flood_wait_stops_before_code_prompt_and_does_not_retry(self):
        from examples.account_login import login
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            with patch('examples.account_login.read_phone', return_value=PHONE), \
                    patch('builtins.input', return_value='yes'), \
                    patch('examples.account_login.secret') as secret, \
                    patch.object(AccountHTTPTransport, 'invoke', side_effect=AccountRPCError(400, 'FLOOD_WAIT_1418')) as rpc:
                with self.assertRaises(AccountRPCError) as caught:
                    await login(path)
            self.assertEqual(caught.exception.retry_after, 1418)
            rpc.assert_awaited_once()
            secret.assert_not_called()
            self.assertFalse(path.exists())

    async def test_invalid_login_reports_stage_without_credentials_or_retry(self):
        from examples.account_login import login
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            with patch('examples.account_login.read_phone', return_value=PHONE), \
                    patch('builtins.input', return_value='yes'), patch('builtins.print') as output, \
                    patch.object(AccountHTTPTransport, 'invoke', side_effect=AccountRPCError(401, 'INVALID_LOGIN')) as rpc:
                with self.assertRaises(AccountRPCError):
                    await login(path)
            rpc.assert_awaited_once()
            messages = repr(output.call_args_list)
            self.assertIn('rejected auth.sendCode', messages)
            self.assertNotIn(PHONE, messages)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
