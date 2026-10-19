"""Automatic terminal authentication; all RPC and credential input is mocked."""
import asyncio
import getpass
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fnceitaa import (AccountClient, AccountRPCError, AuthenticationError,
                      SessionError, SessionRevokedError, SendMessageUncertainError)
from fnceitaa.account import AccountHTTPTransport, DEFAULT_ENDPOINT
from fnceitaa.errors import AccountTransportError
from fnceitaa.protocol import TLCodecError
from fnceitaa.session import AccountSession, SessionStore
from test_account_login import AUTH, PHONE, SENT, USER, reply


SENT_MESSAGE = {'_': 'updateShortSentMessage', 'id': 42, 'date': 100,
                'pts': 1, 'pts_count': 1, 'pFlags': {'out': True}}


class AutomaticLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'account.session'
        self.rpc = self.enterContext(patch.object(AccountHTTPTransport, 'invoke', new_callable=AsyncMock))
        self.terminal = self.enterContext(patch('fnceitaa.account_login._terminal_available', return_value=True))
        self.secret = self.enterContext(patch('fnceitaa.account_login._read_secret', new_callable=AsyncMock))
        self.confirm = self.enterContext(patch('builtins.input', return_value='yes'))
        self.output = self.enterContext(patch('builtins.print'))
        self.secret.side_effect = [PHONE, '123456']

    def saved_session(self):
        store = SessionStore(self.path)
        store.load()
        store.save(AccountSession('old-token', 'fixture-device', USER['id'], DEFAULT_ENDPOINT, 2496))
        return self.path.read_bytes()

    def methods(self):
        return [call.args[0] for call in self.rpc.call_args_list]

    async def test_original_context_example_logs_in_then_sends_exactly_once(self):
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([USER]), reply(SENT_MESSAGE)]
        client = AccountClient(self.path)
        async with client:
            sent = await client.send_message('self', 'test', random_id=99)
            self.assertEqual(sent.id, 42)
            self.assertTrue(client.is_authorized)
        self.assertEqual(self.methods(), ['auth.sendCode', 'auth.signIn', 'users.getUsers', 'messages.sendMessage'])
        self.assertFalse(client.is_authorized)
        self.assertTrue(self.path.exists())
        saved = self.path.read_text()
        for secret in (PHONE, '123456', SENT['phone_code_hash']):
            self.assertNotIn(secret, saved)
            self.assertNotIn(secret, repr(self.output.call_args_list))

    async def test_valid_saved_session_skips_all_terminal_input(self):
        self.saved_session()
        self.rpc.side_effect = [reply([USER]), reply(SENT_MESSAGE)]
        async with AccountClient(self.path) as client:
            await client.send_message('self', 'test', random_id=99)
        self.assertEqual(self.methods(), ['users.getUsers', 'messages.sendMessage'])
        self.terminal.assert_not_called()
        self.secret.assert_not_awaited()
        self.confirm.assert_not_called()

    async def test_start_without_context_and_concurrent_starts_share_one_login(self):
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([USER])]
        client = AccountClient(self.path)
        results = await asyncio.gather(client.start(), client.start())
        self.assertEqual(results, [client, client])
        self.assertEqual(self.methods(), ['auth.sendCode', 'auth.signIn', 'users.getUsers'])
        self.confirm.assert_called_once()
        await client.disconnect()

    async def test_auto_login_false_preserves_manual_context_and_operation_error(self):
        async with AccountClient(self.path, auto_login=False) as client:
            self.assertFalse(client.is_authorized)
            with self.assertRaises(AuthenticationError):
                await client.send_message('self', 'test')
        self.rpc.assert_not_awaited()
        self.secret.assert_not_awaited()

    async def test_low_level_connect_remains_noninteractive(self):
        client = await AccountClient(self.path).connect()
        self.assertFalse(client.is_authorized)
        self.rpc.assert_not_awaited()
        self.terminal.assert_not_called()
        await client.disconnect()

    async def test_noninteractive_environment_fails_before_any_code_or_operation(self):
        self.terminal.return_value = False
        client = AccountClient(self.path)
        with self.assertRaisesRegex(AuthenticationError, 'no interactive terminal'):
            async with client:
                self.fail('Operation body must not run')
        self.assertFalse(client._connected)
        self.rpc.assert_not_awaited()
        self.secret.assert_not_awaited()
        self.assertFalse(self.path.exists())

    async def test_user_declines_code_request_no_network_or_session(self):
        self.confirm.return_value = 'no'
        with self.assertRaisesRegex(AuthenticationError, 'cancelled'):
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.rpc.assert_not_awaited()
        self.assertFalse(self.path.exists())

    async def test_invalid_phone_reprompts_locally_before_one_request(self):
        self.secret.side_effect = ['0912000000', PHONE, '123456']
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([USER])]
        async with AccountClient(self.path) as client:
            self.assertTrue(client.is_authorized)
        self.assertEqual(self.methods().count('auth.sendCode'), 1)
        self.assertEqual(self.secret.await_count, 3)

    async def test_local_iranian_phone_is_normalized_before_otp_request(self):
        self.secret.side_effect = ['۰۹۱۲۰۰۰۰۰۰۰', '123456']
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([USER])]
        async with AccountClient(self.path) as client:
            self.assertTrue(client.is_authorized)
        self.assertEqual(self.rpc.call_args_list[0].args[1]['phone_number'], PHONE)
        self.assertEqual(self.methods().count('auth.sendCode'), 1)
        self.assertEqual(self.secret.await_count, 2)

    async def test_incorrect_code_reuses_challenge_without_new_otp(self):
        self.secret.side_effect = [PHONE, '000000', '123456']
        self.rpc.side_effect = [reply(SENT), AccountRPCError(400, 'PHONE_CODE_INVALID'), reply(AUTH), reply([USER])]
        async with AccountClient(self.path) as client:
            self.assertTrue(client.is_authorized)
        self.assertEqual(self.methods(), ['auth.sendCode', 'auth.signIn', 'auth.signIn', 'users.getUsers'])

    async def test_code_attempts_are_bounded_and_no_message_sent(self):
        self.secret.side_effect = [PHONE, '000001', '000002', '000003']
        self.rpc.side_effect = [reply(SENT)] + [AccountRPCError(400, 'PHONE_CODE_INVALID')] * 3
        with self.assertRaises(AccountRPCError):
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.assertEqual(self.methods().count('auth.signIn'), 3)
        self.assertNotIn('messages.sendMessage', self.methods())

    async def test_password_required_flow_does_not_store_password(self):
        self.secret.side_effect = [PHONE, '123456', 'fixture-password']
        self.rpc.side_effect = [reply(SENT), AccountRPCError(401, 'SESSION_PASSWORD_NEEDED'),
                                reply({'_': 'account.password2', 'current_salt': b'salt'}),
                                reply(AUTH), reply([USER])]
        async with AccountClient(self.path) as client:
            self.assertTrue(client.is_authorized)
        self.assertEqual(self.methods(), ['auth.sendCode', 'auth.signIn', 'account.getPassword', 'auth.checkPassword2', 'users.getUsers'])
        self.assertNotIn('fixture-password', self.path.read_text())
        self.assertNotIn('fixture-password', repr(self.output.call_args_list))

    async def test_existing_manual_challenge_is_continued_without_requesting_another(self):
        client = await AccountClient(self.path).connect()
        self.rpc.side_effect = [reply(SENT), reply(AUTH), reply([USER])]
        await client.request_code(PHONE)
        self.secret.side_effect = ['123456']
        await client.start()
        self.assertTrue(client.is_authorized)
        self.assertEqual(self.methods().count('auth.sendCode'), 1)
        self.confirm.assert_not_called()
        await client.disconnect()

    async def test_revoked_saved_session_reauthenticates_same_account_then_sends(self):
        self.saved_session()
        self.rpc.side_effect = [AccountRPCError(401, 'INVALID_LOGIN'), reply(SENT), reply(AUTH), reply([USER]), reply(SENT_MESSAGE)]
        async with AccountClient(self.path) as client:
            await client.send_message('self', 'test', random_id=99)
        self.assertEqual(self.methods(), ['users.getUsers', 'auth.sendCode', 'auth.signIn', 'users.getUsers', 'messages.sendMessage'])
        self.assertIn(AUTH['token'], self.path.read_text())

    async def test_revoked_session_declined_login_retains_original_file(self):
        before = self.saved_session()
        self.rpc.side_effect = [AccountRPCError(401, 'SESSION_REVOKED')]
        self.confirm.return_value = 'no'
        with self.assertRaises(AuthenticationError):
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.methods(), ['users.getUsers'])

    async def test_revoked_session_manual_mode_does_not_prompt(self):
        before = self.saved_session()
        self.rpc.side_effect = SessionRevokedError('fixture')
        with self.assertRaises(SessionRevokedError):
            async with AccountClient(self.path, auto_login=False):
                self.fail('Operation body must not run')
        self.secret.assert_not_awaited()
        self.assertEqual(self.path.read_bytes(), before)

    async def test_reauthentication_wrong_account_never_overwrites_or_sends(self):
        before = self.saved_session()
        wrong = {**AUTH, 'user': {**USER, 'id': 999}}
        self.rpc.side_effect = [AccountRPCError(401, 'INVALID_LOGIN'), reply(SENT), reply(wrong)]
        with self.assertRaisesRegex(SessionError, 'different account'):
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('messages.sendMessage', self.methods())

    async def test_concurrent_session_file_change_during_reauth_is_rejected(self):
        self.saved_session()
        external = b'external replacement'
        def response(method, params=None):
            if method == 'users.getUsers':
                raise AccountRPCError(401, 'INVALID_LOGIN')
            if method == 'auth.sendCode':
                self.path.write_bytes(external)
                return reply(SENT)
            return reply(AUTH)
        self.rpc.side_effect = response
        with self.assertRaisesRegex(SessionError, 'changed outside'):
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.assertEqual(self.path.read_bytes(), external)

    async def test_transport_decode_and_flood_failure_do_not_trigger_relogin(self):
        before = self.saved_session()
        for error in (AccountTransportError('timeout'), TLCodecError('decode'), AccountRPCError(400, 'FLOOD_WAIT_10')):
            self.rpc.reset_mock()
            self.rpc.side_effect = error
            with self.assertRaises(type(error)):
                async with AccountClient(self.path):
                    self.fail('Operation body must not run')
            self.assertEqual(self.methods(), ['users.getUsers'])
            self.secret.assert_not_awaited()
            self.assertEqual(self.path.read_bytes(), before)

    async def test_flood_during_code_request_does_not_retry_or_execute_body(self):
        self.rpc.side_effect = AccountRPCError(400, 'FLOOD_WAIT_60')
        with self.assertRaises(AccountRPCError) as raised:
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.assertEqual(raised.exception.retry_after, 60)
        self.assertEqual(self.methods(), ['auth.sendCode'])
        self.assertFalse(self.path.exists())

    async def test_uncertain_send_is_not_reauthenticated_or_repeated(self):
        self.saved_session()
        self.rpc.side_effect = [reply([USER]), AccountTransportError('timeout')]
        with self.assertRaises(SendMessageUncertainError):
            async with AccountClient(self.path) as client:
                await client.send_message('self', 'test', random_id=99)
        self.assertEqual(self.methods(), ['users.getUsers', 'messages.sendMessage'])
        self.secret.assert_not_awaited()

    async def test_cancelled_login_does_not_continue_the_operation(self):
        self.secret.side_effect = asyncio.CancelledError()
        client = AccountClient(self.path)
        with self.assertRaises(asyncio.CancelledError):
            async with client:
                self.fail('Operation body must not run')
        self.assertFalse(client._connected)
        self.rpc.assert_not_awaited()

    async def test_corrupt_session_is_not_silently_replaced(self):
        self.path.write_text('invalid session')
        with self.assertRaises(SessionError):
            async with AccountClient(self.path):
                self.fail('Operation body must not run')
        self.secret.assert_not_awaited()
        self.rpc.assert_not_awaited()
        self.assertEqual(self.path.read_text(), 'invalid session')


class TerminalInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_getpass_warning_does_not_fallback_to_echo(self):
        from fnceitaa.account_login import _read_secret
        with patch('fnceitaa.account_login.getpass.getpass', side_effect=getpass.GetPassWarning('no tty')):
            with self.assertRaisesRegex(AuthenticationError, 'Secure terminal input unavailable'):
                await _read_secret('Secret: ')

    def test_auto_login_flag_must_be_bool(self):
        for value in ('yes', 1, None):
            with self.assertRaises(ValueError):
                AccountClient(auto_login=value)
