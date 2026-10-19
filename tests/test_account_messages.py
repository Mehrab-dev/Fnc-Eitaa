import asyncio
import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock

from fnceitaa import (AccountClient, AccountPeer, PeerKind, SendMessageUncertainError,
                      SendMessageCancelledError, SessionRevokedError)
from fnceitaa.account import AccountHTTPTransport
from fnceitaa.account_messages import correlate_sent, parse_message
from fnceitaa.errors import AccountRPCError, AccountStateError, AccountTransportError
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore
import test_account_peers as peer_fixtures
from test_account_peers import SELF, USER, message, rpc


def history(messages, name='messages.messagesSlice'):
    return {'_': name, 'messages': messages, 'users': [USER], 'chats': [],
            **({'count': 100} if name != 'messages.messages' else {})}


def short_sent(identifier=100):
    return {'_': 'updateShortSentMessage', 'id': identifier, 'date': 1000,
            'pts': 1, 'pts_count': 1, 'pFlags': {'out': True}}


def full_sent(random_id=777, updates=None):
    return {'_': 'updates', 'updates': updates if updates is not None else [
        {'_': 'updateMessageID', 'random_id': random_id, 'id': 100},
        {'_': 'updateNewMessage', 'message': {**message(mid=100), 'pFlags': {'out': True}},
         'pts': 1, 'pts_count': 1}], 'users': [USER], 'chats': [], 'date': 1000, 'seq': 1}


class MessageModelTests(unittest.TestCase):
    def test_text_service_reply_sender_and_privacy(self):
        peer = AccountPeer(42, access_hash=-987)
        value = {**message(), 'from_id': {'_': 'peerUser', 'user_id': 99},
                 'message': 'private fixture text', 'pFlags': {'out': True},
                 'reply_to': {'_': 'messageReplyHeader', 'reply_to_msg_id': 7},
                 'media': {'_': 'messageMediaEmpty'}}
        result = parse_message(value, peer, {})
        self.assertEqual(result.sender.id, 99)
        self.assertTrue(result.outgoing)
        self.assertTrue(result.has_media)
        self.assertEqual(result.reply_to_msg_id, 7)
        self.assertNotIn('private fixture text', repr(result))
        self.assertIsNone(parse_message({'_': 'messageEmpty', 'id': 7}, peer, {}))
        service = {**message(), '_': 'messageService'}
        service.pop('message')
        self.assertTrue(parse_message(service, peer, {}).is_service)

    def test_parser_rejects_cross_peer_and_invalid_metadata(self):
        peer = AccountPeer(42, access_hash=-987)
        for value in (message(identifier=99), {**message(), 'id': 0},
                      {**message(), 'date': True}, {**message(), 'message': None},
                      {**message(), 'reply_to': {'reply_to_msg_id': -1}}):
            with self.assertRaises(TLCodecError):
                parse_message(value, peer, {})

    def test_unrelated_updates_do_not_confirm_send(self):
        peer = AccountPeer(42, access_hash=-987)
        for value in ({'_': 'pong'}, {'_': 'updatesTooLong'}, full_sent(random_id=888),
                      full_sent(updates=full_sent()['updates'][1:]),
                      full_sent(updates=[{'_': 'updateMessageID', 'random_id': 777, 'id': 0}]),
                      full_sent(updates=full_sent()['updates'] * 2)):
            with self.assertRaises(TLCodecError):
                correlate_sent(value, peer, 777, 'text', None, {})

    def test_full_and_combined_binary_channel_send_updates(self):
        codec = TLCodec()
        peer = AccountPeer(42, PeerKind.CHANNEL, access_hash=654)
        updates = [{'_': 'updateMessageID', 'random_id': 777, 'id': 100},
                   {'_': 'updateNewChannelMessage', 'message': message('channel', mid=100),
                    'pts': 1, 'pts_count': 1}]
        for name in ('updates', 'updatesCombined'):
            value = full_sent(updates=updates)
            value['_'] = name
            if name == 'updatesCombined':
                value['seq_start'] = 1
            decoded = codec.decode(codec.encode(value), 'Updates')
            result = correlate_sent(decoded, peer, 777, 'text', None, {})
            self.assertEqual((result.id, result.peer.kind), (100, PeerKind.CHANNEL))


class MessageClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await peer_fixtures.AccountReadTests.asyncSetUp(self)
        self.peer = AccountPeer(42, access_hash=-987)
        self.channel = AccountPeer(42, PeerKind.CHANNEL, access_hash=654)

    async def test_short_send_once_with_reply_and_optional_flags(self):
        self.invoke.return_value = rpc(short_sent())
        result = await self.client.send_message(self.peer, 'سلام 😀', reply_to_msg_id=7,
                                                silent=True, no_webpage=True, random_id=777)
        self.assertEqual(result.id, 100)
        self.assertEqual(result.random_id, 777)
        self.assertTrue(result.outgoing)
        self.assertEqual(result.reply_to_msg_id, 7)
        self.invoke.assert_awaited_once_with('messages.sendMessage', {
            'peer': self.peer.to_input_peer(), 'message': 'سلام 😀', 'reply_to_msg_id': 7,
            'silent': True, 'no_webpage': True, 'random_id': 777})

    async def test_full_updates_correlate_exact_random_id(self):
        self.invoke.return_value = rpc(full_sent())
        result = await self.client.send_message(self.peer, 'requested text', random_id=777)
        self.assertEqual(result.id, 100)
        self.assertEqual(result.text, 'Fixture text')
        self.assertEqual(result.random_id, 777)

    async def test_mapping_only_and_update_short_confirm_id(self):
        mapping = {'_': 'updateMessageID', 'random_id': 777, 'id': 100}
        for value in (full_sent(updates=[mapping]), {'_': 'updateShort', 'update': mapping, 'date': 1000}):
            self.invoke.return_value = rpc(value)
            result = await self.client.send_message(self.peer, 'text', random_id=777)
            self.assertEqual(result.id, 100)
            self.assertEqual(result.text, 'text')

    async def test_uncertain_send_retains_id_without_retry_or_text_disclosure(self):
        for response in (AccountTransportError('timeout'), TLCodecError('bad response'),
                         AccountStateError('eitaa_updates_expire_token'), rpc({'_': 'pong'}),
                         rpc(full_sent(random_id=888))):
            self.invoke.reset_mock()
            if isinstance(response, Exception):
                self.invoke.side_effect = response
            else:
                self.invoke.side_effect = None
                self.invoke.return_value = response
            with self.assertRaises(SendMessageUncertainError) as caught:
                await self.client.send_message(self.peer, 'private fixture text', random_id=777)
            self.assertEqual(caught.exception.random_id, 777)
            self.assertNotIn('private fixture text', str(caught.exception))
            self.invoke.assert_awaited_once()

    async def test_cancelled_send_remains_asyncio_cancellation_with_operation_id(self):
        entered = asyncio.Event()
        async def pending(*args):
            entered.set()
            await asyncio.Future()
        self.invoke.side_effect = pending
        task = asyncio.create_task(self.client.send_message(self.peer, 'text', random_id=777))
        await entered.wait()
        task.cancel()
        with self.assertRaises(SendMessageCancelledError) as caught:
            await task
        self.assertIsInstance(caught.exception, asyncio.CancelledError)
        self.assertEqual(caught.exception.random_id, 777)
        self.assertTrue(task.cancelled())
        self.invoke.assert_awaited_once()

    async def test_explicit_rpc_failure_is_propagated_and_never_retried(self):
        for error in ('CHAT_WRITE_FORBIDDEN', 'FLOOD_WAIT_30', 'MESSAGE_EMPTY'):
            self.invoke.reset_mock()
            self.invoke.side_effect = AccountRPCError(400, error)
            with self.assertRaises(AccountRPCError):
                await self.client.send_message(self.peer, 'text', random_id=777)
            self.invoke.assert_awaited_once()

    async def test_send_revocation_clears_authorization(self):
        self.invoke.side_effect = AccountRPCError(401, 'INVALID_LOGIN')
        with self.assertRaises(SessionRevokedError):
            await self.client.send_message(self.peer, 'text')
        self.assertFalse(self.client.is_authorized)

    async def test_text_and_option_validation_happens_before_rpc(self):
        for text, kwargs in [('', {}), (None, {}), ('x' * 4097, {}), ('😀' * 2049, {}),
                             ('\ud800', {}), ('text', {'reply_to_msg_id': True}),
                             ('text', {'reply_to_msg_id': 0}), ('text', {'random_id': 0}),
                             ('text', {'random_id': True}), ('text', {'random_id': 2**63}),
                             ('text', {'silent': 1}), ('text', {'no_webpage': None})]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.client.send_message(self.peer, text, **kwargs)
        self.invoke.assert_not_awaited()

    async def test_utf16_boundary_and_generated_random_id(self):
        self.invoke.return_value = rpc(short_sent())
        with patch('fnceitaa.account_client.secrets.randbits', return_value=888):
            result = await self.client.send_message(self.peer, '😀' * 2048)
        self.assertEqual(result.random_id, 888)

    async def test_channel_single_message_uses_channel_rpc_and_access_hash(self):
        self.invoke.return_value = rpc(history([message('channel')], 'messages.messages'))
        result = await self.client.get_message(self.channel, 10)
        self.assertEqual(result.id, 10)
        self.invoke.assert_awaited_once_with('channels.getMessages', {
            'channel': {'_': 'inputChannel', 'channel_id': 42, 'access_hash': 654},
            'id': [{'_': 'inputMessageID', 'id': 10}]})

    async def test_user_lookup_never_exposes_message_from_other_dialog(self):
        self.invoke.return_value = rpc(history([message(identifier=99)], 'messages.messages'))
        self.assertIsNone(await self.client.get_message(self.peer, 10))
        self.assertEqual(self.invoke.call_args.args[0], 'messages.getMessages')

    async def test_empty_missing_and_wrong_id_single_message(self):
        for messages in ([], [{'_': 'messageEmpty', 'id': 10}]):
            self.invoke.return_value = rpc(history(messages))
            self.assertIsNone(await self.client.get_message(self.peer, 10))
        self.invoke.return_value = rpc(history([message(mid=99)]))
        with self.assertRaises(TLCodecError):
            await self.client.get_message(self.peer, 10)

    async def test_history_sorts_newest_first_and_paginates_deleted_placeholders(self):
        self.invoke.return_value = rpc(history([message(mid=9), message(mid=10), {'_': 'messageEmpty', 'id': 8}]))
        result = await self.client.get_history_page(self.peer, limit=3)
        self.assertEqual([m.id for m in result.messages], [10, 9])
        self.assertEqual(result.next_offset_id, 8)
        self.assertEqual(result.total_count, 100)
        self.invoke.assert_awaited_once_with('messages.getHistory', {
            'peer': self.peer.to_input_peer(), 'offset_id': 0, 'offset_date': 0,
            'add_offset': 0, 'limit': 3, 'max_id': 0, 'min_id': 0, 'hash': 0})

    async def test_history_iteration_is_lazy_bounded_and_uses_exclusive_offsets(self):
        self.invoke.side_effect = [rpc(history([message(mid=10), message(mid=9)])),
                                   rpc(history([message(mid=8)], 'messages.messages'))]
        iterator = self.client.iter_history(self.peer, limit=3, page_size=2)
        first = await anext(iterator)
        self.assertEqual(first.id, 10)
        self.assertEqual(self.invoke.await_count, 1)
        self.assertEqual([m.id async for m in iterator], [9, 8])
        self.assertEqual(self.invoke.await_count, 2)
        self.assertEqual(self.invoke.call_args.args[1]['offset_id'], 9)
        self.assertEqual(self.invoke.call_args.args[1]['limit'], 1)

    async def test_empty_only_history_page_advances_to_older_messages(self):
        self.invoke.side_effect = [rpc(history([{'_': 'messageEmpty', 'id': 10}])),
                                   rpc(history([message(mid=9)], 'messages.messages'))]
        self.assertEqual([m.id for m in await self.client.get_history(self.peer, limit=1)], [9])
        self.assertEqual(self.invoke.await_count, 2)

    async def test_empty_complete_and_id_one_history_terminate(self):
        for value in (history([]), history([message(mid=10)], 'messages.messages'), history([message(mid=1)])):
            self.invoke.reset_mock()
            self.invoke.return_value = rpc(value)
            await self.client.get_history(self.peer)
            self.invoke.assert_awaited_once()

    async def test_history_rejects_wrong_peer_offsets_and_notmodified(self):
        for value in (history([message(mid=10)]), history([message(identifier=99, mid=8)]),
                      {'_': 'messages.messagesNotModified', 'count': 100}):
            self.invoke.return_value = rpc(value)
            with self.assertRaises(TLCodecError):
                await self.client.get_history_page(self.peer, offset_id=10)

    async def test_invalid_reads_and_zero_limit_make_no_rpc(self):
        for identifier in (True, 0, -1, 2**31):
            with self.assertRaises(ValueError):
                await self.client.get_message(self.peer, identifier)
        for kwargs in ({'limit': 0}, {'limit': 101}, {'limit': True}, {'offset_id': -1}, {'offset_id': True}):
            with self.assertRaises(ValueError):
                await self.client.get_history_page(self.peer, **kwargs)
        self.assertEqual(await self.client.get_history(self.peer, limit=0), [])
        self.invoke.assert_not_awaited()


class MessageWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_binary_send_reply_history_and_read(self):
        from test_account_transport import response_for
        codec = TLCodec()
        packets = [codec.encode([SELF], 'Vector<User>'), codec.encode(short_sent()),
                   codec.encode(history([message(mid=100)])),
                   codec.encode(history([message(mid=100)], 'messages.messages'))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            store = SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123,
                                      'https://hasan.eitaa.ir/eitaa/', 2496))
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    peer = AccountPeer(42, access_hash=-987)
                    sent = await client.send_message(peer, 'سلام', random_id=777,
                                                     reply_to_msg_id=7, silent=True, no_webpage=True)
                    self.assertEqual(sent.id, 100)
                    expected = codec.encode_method('messages.sendMessage', {
                        'peer': peer.to_input_peer(), 'message': 'سلام', 'random_id': 777,
                        'reply_to_msg_id': 7, 'silent': True, 'no_webpage': True})
                    self.assertEqual(expected[:8], bytes.fromhex('70380c5223000000'))
                    self.assertIn(expected, post.call_args.kwargs['data'])
                    result = await client.get_history_page(peer)
                    self.assertEqual(result.messages[0].id, sent.id)
                    self.assertEqual((await client.get_message(peer, sent.id)).id, sent.id)
                self.assertEqual(post.call_count, 4)

    async def test_malformed_http_ack_stays_uncertain_without_post_retry(self):
        from test_account_transport import response_for
        codec = TLCodec()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            store = SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123,
                                      'https://hasan.eitaa.ir/eitaa/', 2496))
            packets = [codec.encode([SELF], 'Vector<User>'), codec.encode(short_sent()) + b'\0' * 4]
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    with self.assertRaises(SendMessageUncertainError) as caught:
                        await client.send_message('self', 'private text', random_id=777)
                    self.assertEqual(caught.exception.random_id, 777)
                self.assertEqual(post.call_count, 2)


class MessageExampleTests(unittest.TestCase):
    def test_offline_send_does_not_open_session_prompt_or_call_network(self):
        from examples.account_messages import main
        with patch('sys.argv', ['account_messages', 'send']), patch('builtins.print'), \
                patch('examples.account_messages.AccountClient') as client, \
                patch('builtins.input') as prompt, patch('fnceitaa.account.requests.post') as network:
            main()
        client.assert_not_called()
        prompt.assert_not_called()
        network.assert_not_called()


class MessageExampleSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_requires_exact_local_confirmation_after_preview(self):
        from examples.account_messages import run
        peer = AccountPeer(123, is_self=True)
        args = argparse.Namespace(session='fixture.session', load_dialogs=False,
                                  peer='self', command='send', reply_to=7)
        for confirmation in ('no', '', 'yes'):
            with patch('examples.account_messages.AccountClient') as factory, \
                    patch('builtins.input', side_effect=['private fixture text', confirmation]), \
                    patch('builtins.print') as output, \
                    patch('examples.account_messages.secrets.randbits', return_value=777):
                client = factory.return_value.__aenter__.return_value
                client.is_authorized = True
                client.resolve_peer = AsyncMock(return_value=peer)
                client.send_message = AsyncMock(return_value=correlate_sent(
                    short_sent(), peer, 777, 'private fixture text', 7, {}))
                await run(args)
                self.assertIn('Destination:', str(output.call_args_list))
                if confirmation == 'yes':
                    client.send_message.assert_awaited_once_with(
                        peer, 'private fixture text', reply_to_msg_id=7, no_webpage=True, random_id=777)
                else:
                    client.send_message.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
