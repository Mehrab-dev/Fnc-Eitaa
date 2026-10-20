"""Synthetic renewal responses only; no live credentials or network calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fnceitaa import AccountClient
from fnceitaa.account import AccountHTTPTransport, DEFAULT_ENDPOINT
from fnceitaa.errors import AccountStateError
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore


def renewal(codec):
    return codec.encode({'_': 'eitaa_updates_token', 'token': 't' * 35,
                         'expire': 3600, 'date': 1}, namespace='MTProto')


class RenewalCodecTests(unittest.TestCase):
    def test_reported_offsets_and_strict_general_decode(self):
        codec = TLCodec()
        prefix = renewal(codec)
        self.assertEqual(len(prefix), 48)
        data = prefix + b'x' * 68
        with self.assertRaisesRegex(TLCodecError, 'read=48, total=116, remaining=68'):
            codec.decode(data)
        value, remaining = codec.decode_token_refresh_prefix(data)
        self.assertEqual(value['token'], 't' * 35)
        self.assertEqual(remaining, 68)

    def test_invalid_candidates_rejected(self):
        codec = TLCodec()
        empty = codec.encode({'_': 'eitaa_updates_token', 'token': '',
                              'expire': 0, 'date': 0}, namespace='MTProto')
        error = codec.encode({'_': 'rpc_error', 'error_code': 400,
                              'error_message': 'INVALID_LOGIN'}, namespace='MTProto')
        envelope = codec.encode({'_': 'rpc_result', 'req_msg_id': 4,
                                 'result': {'_': 'eitaa_updates_token', 'token': 't',
                                            'expire': 1, 'date': 1}}, namespace='MTProto')
        for data in (renewal(codec)[:-1], empty, error, envelope, b''):
            with self.subTest(size=len(data)), self.assertRaises(TLCodecError):
                codec.decode_token_refresh_prefix(data)


class RenewalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_limits_fallback_to_refresh_method(self):
        codec = TLCodec()
        data = renewal(codec) + b'x' * 68
        transport = AccountHTTPTransport(device_id='synthetic', codec=codec)
        with patch.object(transport, '_post', return_value=data):
            reply = await transport.invoke('eitaaRefreshToken', {'app_info': {
                '_': 'eitaaAppInfo', 'build_version': 2496, 'device_model': 'test',
                'system_version': 'test', 'app_version': 'test', 'lang_code': 'en', 'sign': ''}})
            self.assertEqual(reply.token_refresh_tail_bytes, 68)
            self.assertEqual(reply.authorization_tail_bytes, 0)
            self.assertEqual(reply.control, 'eitaa_updates_token')
            with self.assertRaises(TLCodecError):
                await transport.invoke('users.getUsers', {'id': [{'_': 'inputUserSelf'}]})

    async def test_reconnect_verifies_candidate_before_saving(self):
        for identity in ('matching', 'different', 'trailing', 'expired'):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'synthetic.session'
                store = SessionStore(path)
                store.load()
                store.save(AccountSession('old-synthetic', 'device', 7, DEFAULT_ENDPOINT, 2496))
                original = path.read_bytes()
                codec = TLCodec()
                expire = codec.encode({'_': 'eitaa_updates_expire_token'}, namespace='MTProto')
                user = codec.encode([{'_': 'user', 'id': 7 if identity != 'different' else 8,
                                      'pFlags': {'self': True}}], 'Vector<User>', namespace='API')
                verification = (user + b'xxxx' if identity == 'trailing' else
                                expire if identity == 'expired' else user)
                responses = [expire, renewal(codec) + b'x' * 68, verification, user]
                client = AccountClient(path, auto_login=False)
                with patch.object(AccountHTTPTransport, '_post', side_effect=responses) as post:
                    if identity == 'matching':
                        async with client:
                            self.assertTrue(client.is_authorized)
                            self.assertEqual(store.load().token, 't' * 35)
                        self.assertEqual(post.call_count, 4)
                    else:
                        expected = AccountStateError if identity == 'expired' else TLCodecError
                        with self.assertRaises(expected):
                            async with client:
                                self.fail('Unverified token must not authorize the client')
                        self.assertFalse(client.is_authorized)
                        self.assertEqual(path.read_bytes(), original)
                        self.assertEqual(post.call_count, 3)


if __name__ == '__main__':
    unittest.main()
