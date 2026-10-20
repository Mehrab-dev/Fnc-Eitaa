"""Offline checks for terminal login; never send a real login request."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fnceitaa import AccountClient, AccountRPCError, PasswordRequiredError


class TerminalLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_uses_visible_phone_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = AccountClient(Path(directory) / 'test.session')
            challenge = SimpleNamespace(delivery='Sms', length=5, timeout=60,
                                        synthetic_ack=False)

            async def sign_in(code):
                client._authorized = True

            with (patch('fnceitaa.account_login._terminal_available', return_value=True),
                  patch('builtins.input', return_value='09120000000') as phone_input,
                  patch('fnceitaa.account_login._secret', return_value='12345') as secret,
                  patch.object(client, 'request_code', AsyncMock(return_value=challenge)) as request,
                  patch.object(client, 'sign_in', AsyncMock(side_effect=sign_in)) as submit,
                  contextlib.redirect_stdout(io.StringIO())):
                async with client:
                    self.assertTrue(client.is_authorized)
                phone_input.assert_called_once_with('Phone number (09... or +country code): ')
                request.assert_awaited_once_with('+989120000000')
                secret.assert_called_once_with('Login code (hidden): ')
                submit.assert_awaited_once_with('12345')

    async def test_flood_at_each_login_stage_exits_without_running_body(self):
        for stage in ('request_code', 'sign_in', 'sign_in_password'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                client = AccountClient(Path(directory) / 'test.session')
                challenge = SimpleNamespace(delivery='Sms', length=5, timeout=60,
                                            synthetic_ack=False)
                request = AsyncMock(return_value=challenge)
                sign_in = AsyncMock()
                password = AsyncMock()
                methods = dict(request_code=request, sign_in=sign_in,
                               sign_in_password=password)
                methods[stage].side_effect = AccountRPCError(400, 'FLOOD_WAIT_1349')
                if stage == 'sign_in_password':
                    async def need_password(code):
                        client._password_code = code
                        raise PasswordRequiredError('Password required')
                    sign_in.side_effect = need_password
                output = io.StringIO()
                with (patch('fnceitaa.account_login._terminal_available', return_value=True),
                      patch('builtins.input', return_value='09120000000'),
                      patch('fnceitaa.account_login._secret', return_value='12345'),
                      patch.multiple(client, **methods),
                      contextlib.redirect_stdout(output)):
                    with self.assertRaises(SystemExit) as caught:
                        async with client:
                            self.fail('Unauthenticated body must not execute')
                self.assertEqual(caught.exception.code, 1)
                self.assertIn('22 min 29 sec (1349 seconds)', output.getvalue())
                self.assertFalse(client._connected)
                self.assertFalse((Path(directory) / 'test.session').exists())
                request.assert_awaited_once()
                if stage == 'request_code':
                    sign_in.assert_not_awaited()
                if stage != 'sign_in_password':
                    password.assert_not_awaited()

    async def test_other_rpc_errors_still_propagate(self):
        with tempfile.TemporaryDirectory() as directory:
            client = AccountClient(Path(directory) / 'test.session')
            error = AccountRPCError(400, 'PHONE_NUMBER_INVALID')
            with (patch('fnceitaa.account_login._terminal_available', return_value=True),
                  patch('builtins.input', return_value='09120000000'),
                  patch.object(client, 'request_code', AsyncMock(side_effect=error)),
                  contextlib.redirect_stdout(io.StringIO())):
                with self.assertRaises(AccountRPCError) as caught:
                    async with client:
                        self.fail('Unauthenticated body must not execute')
            self.assertIs(caught.exception, error)


if __name__ == '__main__':
    unittest.main()
