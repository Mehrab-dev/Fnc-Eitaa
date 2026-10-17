import asyncio
import json
from pathlib import Path
import struct
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from fnceitaa.account import ACCOUNT_USER_AGENT, AccountHTTPTransport, correlate
from fnceitaa.errors import (AccountRPCError, AccountStateError, AccountTransportError,
                             UnsupportedOperationError)
from fnceitaa.protocol import Limits, TLCodec, TLCodecError


FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'research/protocol/public_probe.json').read_text())


def response_for(data, status=200, headers=None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = status
    response.headers = headers or {}
    response.iter_content.return_value = [data]
    return response


class AccountTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = AccountHTTPTransport(device_id='000000000000001__web')

    def test_envelope_matches_real_fixture(self):
        self.assertEqual(self.transport.encode_request('updates.getState').hex(), FIXTURE['request_hex'])
        transport = AccountHTTPTransport(device_id='device-secret', token='token-secret')
        self.assertNotIn('token-secret', repr(transport))
        self.assertNotIn('device-secret', repr(transport))

    @patch('fnceitaa.account.requests.post')
    async def test_login_error_with_suffix_is_not_accepted_as_authorization(self, post):
        codec = TLCodec()
        post.return_value = response_for(codec.encode({
            '_': 'error', 'code': 401, 'text': 'SESSION_PASSWORD_NEEDED'}) + b'\0' * 60)
        with self.assertRaisesRegex(TLCodecError, 'auth.signIn: Unexpected trailing TL data'):
            await self.transport.invoke('auth.signIn', {
                'phone_number': 'fixture', 'phone_code_hash': 'fixture', 'phone_code': '12345'})
        post.assert_called_once()

    @patch('fnceitaa.account.requests.post')
    async def test_real_error_fixture_over_http_boundary(self, post):
        response = response_for(bytes.fromhex(FIXTURE['response_prefix_hex']))
        post.return_value = response
        with self.assertRaises(AccountRPCError) as raised:
            await self.transport.invoke('updates.getState')
        self.assertEqual((raised.exception.code, raised.exception.rpc_type), (401, 'INVALID_LOGIN'))
        self.assertEqual(post.call_args.kwargs['data'].hex(), FIXTURE['request_hex'])
        self.assertTrue(post.call_args.kwargs['stream'])
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(post.call_args.kwargs['headers']['User-Agent'], ACCOUNT_USER_AGENT)
        response.__exit__.assert_called_once()
        post.assert_called_once()

    @patch('fnceitaa.account.requests.post')
    async def test_empty_phone_reaches_remote_validation_with_sdk_user_agent(self, post):
        # Live observation: python-requests UA got 401; our explicit SDK UA got
        # PHONE_NUMBER_INVALID for the same empty-phone packet. No real OTP.
        codec = TLCodec()
        post.return_value = response_for(codec.encode({
            '_': 'error', 'code': 400, 'text': 'PHONE_NUMBER_INVALID',
        }, 'Object'))
        with self.assertRaises(AccountRPCError) as raised:
            await self.transport.invoke('auth.sendCode', {
                'phone_number': '', 'api_id': 2496,
                'api_hash': '8da85b0d5bfe62527e5b244c209159c3',
                'settings': {'_': 'codeSettings'},
            })
        self.assertEqual((raised.exception.code, raised.exception.rpc_type), (400, 'PHONE_NUMBER_INVALID'))
        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers['User-Agent'], 'Fnc-Eitaa/0.1.0')
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertNotIn('Origin', headers)
        post.assert_called_once()

    @patch('fnceitaa.account.requests.post')
    async def test_direct_typed_success_and_monotonic_request_ids(self, post):
        # auth.cancelCode returns Bool, but this is a mocked response, not a live action.
        post.side_effect = [response_for(bytes.fromhex('b5757299')), response_for(bytes.fromhex('b5757299'))]
        params = {'phone_number': 'fixture', 'phone_code_hash': 'fixture'}
        with patch('fnceitaa.account.time.time_ns', return_value=1_000_000_000):
            first = await self.transport.invoke('auth.cancelCode', params)
            second = await self.transport.invoke('auth.cancelCode', params)
        self.assertIs(first.result, True)
        self.assertEqual(second.request_id, first.request_id + 4)

    @patch('fnceitaa.account.requests.post')
    async def test_rpc_result_is_correlated_and_typed(self, post):
        request_id = 1 << 32
        body = bytes.fromhex('016d5cf3') + struct.pack('<q', request_id) + bytes.fromhex('b5757299')
        post.return_value = response_for(body)
        with patch('fnceitaa.account.time.time_ns', return_value=1_000_000_000):
            reply = await self.transport.invoke('auth.cancelCode', {'phone_number': 'x', 'phone_code_hash': 'y'})
        self.assertTrue(reply.result)

    @patch('fnceitaa.account.requests.post')
    async def test_timeouts_do_not_retry_or_leak_request_url(self, post):
        post.side_effect = requests.Timeout('https://host/token-secret')
        with self.assertRaises(AccountTransportError) as raised:
            await self.transport.invoke('updates.getState')
        self.assertNotIn('token-secret', str(raised.exception))
        post.assert_called_once()

    @patch('fnceitaa.account.requests.post')
    async def test_http_status_empty_and_over_limit_response_close(self, post):
        self.transport.codec = TLCodec(limits=Limits(max_payload_bytes=64))
        cases = [response_for(b'x', status=302), response_for(b'x', status=500),
                 response_for(b''), response_for(b'x', headers={'Content-Length': '65'}),
                 response_for(b'x', headers={'Content-Length': '-1'}),
                 response_for(b'x', headers={'Content-Length': 'bad'}), response_for(b'x' * 65)]
        for response in cases:
            post.return_value = response
            with self.subTest(response=response), self.assertRaises(AccountTransportError):
                await self.transport.invoke('updates.getState')
            response.__exit__.assert_called_once()
        self.assertEqual(post.call_count, len(cases))

    @patch('fnceitaa.account.requests.post')
    async def test_elapsed_time_limit_checked_even_on_empty_stream(self, post):
        response = response_for(b'')
        response.iter_content.return_value = []
        post.return_value = response
        with patch('fnceitaa.account.time.monotonic', side_effect=[0, 31]):
            with self.assertRaises(AccountTransportError):
                self.transport._post(b'fixture')
        response.__exit__.assert_called_once()

    @patch('fnceitaa.account.requests.post')
    async def test_invalid_or_blocked_requests_never_reach_network(self, post):
        with self.assertRaises(UnsupportedOperationError):
            await self.transport.invoke('auth.exportLoginToken', {})
        with self.assertRaises(TLCodecError):
            await self.transport.invoke('unknown')
        post.assert_not_called()

    def test_endpoint_and_timeout_validation(self):
        for endpoint in ('http://hasan.eitaa.ir/eitaa/', 'https://eitaa.ir.attacker.test/eitaa/',
                         'https://user:password@hasan.eitaa.ir/eitaa/',
                         'https://hasan.eitaa.ir/eitaa/?secret=x', 'https://hasan.eitaa.ir/else/'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                AccountHTTPTransport(device_id='fixture', endpoint=endpoint)
        for timeout in (0, -1, True, float('inf'), float('nan')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                AccountHTTPTransport(device_id='fixture', read_timeout=timeout)

    async def test_cancelled_request_keeps_slot_until_worker_exits(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def slow_post(payload):
            calls.append(payload)
            entered.set()
            if not release.wait(2):
                raise RuntimeError('Test release timeout')
            return bytes.fromhex(FIXTURE['response_prefix_hex'])
        with patch.object(self.transport, '_post', side_effect=slow_post):
            first = asyncio.create_task(self.transport.invoke('updates.getState'))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                second = asyncio.create_task(self.transport.invoke('updates.getState'))
                await asyncio.sleep(0.02)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            if second is not None:
                with self.assertRaises(AccountRPCError):
                    await asyncio.wait_for(second, 2)
            self.assertEqual(len(calls), 2)

    async def test_cancel_queued_request_never_transmits_it(self):
        await self.transport._lock.acquire()
        with patch.object(self.transport, '_post') as post:
            waiting = asyncio.create_task(self.transport.invoke('updates.getState'))
            await asyncio.sleep(0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            self.transport._lock.release()
        post.assert_not_called()


class CorrelationTests(unittest.TestCase):
    def test_correlated_container_with_updates(self):
        value = {'_': 'msg_container', 'messages': [
            {'body': {'_': 'updateDeleteMessages', 'messages': [1], 'pts': 2, 'pts_count': 1}},
            {'body': {'_': 'rpc_result', 'req_msg_id': 7, 'result': True}}]}
        reply = correlate(value, 7)
        self.assertIs(reply.result, True)
        self.assertEqual(reply.updates[0]['_'], 'updateDeleteMessages')
        self.assertNotIn('messages', repr(reply))

    def test_wrong_duplicate_missing_and_uncorrelated_results_rejected(self):
        result = {'_': 'rpc_result', 'req_msg_id': 7, 'result': True}
        invalid = [({'_': 'rpc_result', 'req_msg_id': 8, 'result': True}, 7),
                   ({'_': 'msg_container', 'messages': []}, 7),
                   ({'_': 'msg_container', 'messages': [{'body': result}, {'body': result}]}, 7),
                   ({'_': 'msg_container', 'messages': [{'body': {'_': 'boolTrue'}}]}, 7)]
        for value, request_id in invalid:
            with self.subTest(value=value), self.assertRaises(TLCodecError):
                correlate(value, request_id)

    def test_token_control_and_unsanitized_remote_errors(self):
        with self.assertRaises(AccountStateError) as raised:
            correlate({'_': 'eitaa_updates_expire_token'}, 7)
        self.assertEqual(raised.exception.signal, 'eitaa_updates_expire_token')
        with self.assertRaises(AccountRPCError) as raised:
            correlate({'_': 'error', 'code': 400, 'text': 'token=private-value'}, 7)
        self.assertNotIn('private-value', str(raised.exception))
        self.assertEqual(raised.exception.rpc_type, 'REMOTE_ERROR')

    def test_acknowledgement_is_explicit_control_not_rpc_success(self):
        reply = correlate({'_': 'pong', 'msg_id': 7, 'ping_id': 9}, 7)
        self.assertEqual(reply.control, 'pong')
        self.assertEqual(reply.request_id, 7)
        with self.assertRaises(TLCodecError):
            correlate({'_': 'rpc_result', 'req_msg_id': 7,
                       'result': {'_': 'rpc_result', 'req_msg_id': 8, 'result': True}}, 7)

    def test_packaged_schema_is_pinned_to_research_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / 'fnceitaa/protocol/schema.json').read_bytes(),
                         (root / 'research/protocol/schema.json').read_bytes())
        config = json.loads((root / 'fnceitaa/protocol/config.json').read_text())
        inventory = json.loads((root / 'research/protocol/inventory.json').read_text())
        self.assertEqual(config['blocked_methods'], sorted(set(inventory['not_sent_by_web_client'])))
        self.assertEqual(config['source_sha256'], inventory['metadata']['source_sha256'])
