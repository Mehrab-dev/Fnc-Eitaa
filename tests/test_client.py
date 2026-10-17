import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fnceitaa import Client, Message, Peer, Router, APIRequestError, UnsupportedOperationError


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_requires_connection_and_token(self):
        for client in (Client(), Client(token='test')):
            with self.assertRaises(APIRequestError):
                await client.send_message(123, 'hello')

    async def test_send_preserves_response(self):
        client = Client()
        response = {'ok': True, 'result': {'message_id': 42}}
        client._engine = Mock()
        client._engine.send_message.return_value = response
        msg = await client.send_message('@channel', 'hello', 12)
        self.assertEqual(msg.id, 42)
        self.assertIs(msg.raw, response)
        client._engine.send_message.assert_called_once_with(
            chat_id='channel', text='hello', reply_to_message_id=12)

    async def test_invalid_send_responses_never_invent_ids(self):
        client = Client()
        client._engine = Mock()
        for response in (None, True, {}, {'ok': False}, {'ok': True},
                         {'ok': True, 'result': {'message_id': True}}):
            client._engine.send_message.return_value = response
            with self.assertRaises(APIRequestError):
                await client.send_message(123, 'hello')

    async def test_send_file_matches_upstream_signature(self):
        client = Client()
        # The keyword-only signature catches the previous file_path_or_id bug.
        def send_file(*, chat_id, file, caption, reply_to_message_id):
            self.assertTrue(Path(file).is_file())
            self.assertEqual((chat_id, caption, reply_to_message_id), (123, 'photo', 7))
            return {'ok': True, 'result': {'message_id': 9}}
        client._engine = Mock(send_file=send_file)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.jpg'
            path.write_bytes(b'test fixture')
            msg = await client.send_file(123, str(path), 'photo', 7)
        self.assertEqual(msg.id, 9)
        self.assertEqual(msg.media.caption, 'photo')

    async def test_public_info_without_token(self):
        with patch('fnceitaa.client.Eitaa.get_info', return_value={'name': 'channel'}) as get:
            self.assertEqual(await Client().get_chat_info('@channel'), {'name': 'channel'})
            get.assert_called_once_with('channel')

    async def test_get_message_normalizes_public_data_and_missing(self):
        client = Client()
        with patch('fnceitaa.client.Eitaa.get_message', return_value={'message_text': 'hello'}) as get:
            msg = await client.get_message('@channel', 23)
            self.assertEqual((msg.id, msg.text, msg.peer.username), (23, 'hello', 'channel'))
            get.assert_called_once_with('channel', 23)
        with patch('fnceitaa.client.Eitaa.get_message', return_value=None):
            self.assertIsNone(await client.get_message('channel', 23))

    def test_public_message_id_validation(self):
        client = Client()
        peer = Peer(0, username='channel')
        for value in (None, True, False, 1.5, [], {}, '', 'invalid', 0, -1):
            with self.subTest(value=value), self.assertRaises(APIRequestError):
                client._public_message({'message_number': value}, peer)
        for value in (7, '7'):
            with self.subTest(value=value):
                self.assertEqual(client._public_message({'message_number': value}, peer).id, 7)

    async def test_delete_delegates_and_reports_unsupported(self):
        client = Mock(delete_messages=AsyncMock(return_value=True))
        msg = Message(7, Peer(123), _client=client)
        self.assertTrue(await msg.delete())
        client.delete_messages.assert_awaited_once_with(peer=msg.peer, message_ids=[7])
        with self.assertRaises(UnsupportedOperationError):
            await Client().delete_messages(Peer(123), [7])

    async def test_request_errors_do_not_expose_token(self):
        client = Client()
        client._engine = Mock()
        client._engine.send_message.side_effect = RuntimeError('https://host/api/SECRET/send')
        with self.assertRaises(APIRequestError) as raised:
            await client.send_message(123, 'hello')
        self.assertNotIn('SECRET', str(raised.exception))

    async def test_polling_delivers_all_new_messages_in_order(self):
        client = Client()
        received = []
        @client.router.on_message()
        async def receive(msg):
            received.append(msg.id)
            if msg.id == 3:
                client.stop_polling()
        batches = [[{'message_number': 1}],
                   [{'message_number': 3, 'text': 'three'}, {'message_number': 2, 'text': 'two'}]]
        with patch('fnceitaa.client.Eitaa.get_latest_messages', side_effect=batches):
            await asyncio.wait_for(client.run_polling('channel', interval=0.001), 2)
        self.assertEqual(received, [2, 3])
        self.assertFalse(client.is_running)

    async def test_failed_handler_retries_message(self):
        client = Client()
        attempts = []
        @client.router.on_message()
        async def receive(msg):
            attempts.append(msg.id)
            if len(attempts) == 1:
                raise RuntimeError('transient')
            client.stop_polling()
        batches = [[{'message_number': 1}], [{'message_number': 2}], [{'message_number': 2}]]
        with patch('fnceitaa.client.Eitaa.get_latest_messages', side_effect=batches):
            await asyncio.wait_for(client.run_polling('channel', interval=0.001), 2)
        self.assertEqual(attempts, [2, 2])

    async def test_cancellation_propagates_and_resets_state(self):
        client = Client()
        with patch('fnceitaa.client.Eitaa.get_latest_messages', return_value=[]):
            task = asyncio.create_task(client.run_polling('channel', interval=60))
            await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(client.is_running)

    async def test_context_manager_cleans_up(self):
        with patch('fnceitaa.client.Eitaa') as engine:
            async with Client(token='test') as client:
                self.assertIs(client._engine, engine.return_value)
            self.assertIsNone(client._engine)

    async def test_router_runs_remaining_handlers_before_reporting_failure(self):
        router = Router()
        visited = []
        @router.on_message()
        async def first(msg):
            raise RuntimeError('failure')
        @router.on_message()
        def second(msg):
            visited.append(msg.id)
        with self.assertRaises(RuntimeError):
            await router.emit('message', Message(7, Peer(123)))
        self.assertEqual(visited, [7])

    async def test_polling_rejects_invalid_interval(self):
        for interval in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                await Client().run_polling('channel', interval)


if __name__ == '__main__':
    unittest.main()
