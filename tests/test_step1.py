"""Exercise the real Client/backend boundary, mocking only HTTP."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from fnceitaa import APIRequestError, Client
from fnceitaa.transport import TIMEOUT


class StepOneTests(unittest.IsolatedAsyncioTestCase):
    @patch('fnceitaa.transport.requests.post')
    async def test_text_send_and_reply_through_http(self, post):
        response = Mock()
        response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        post.return_value = response
        async with Client(token='fixture-token') as client:
            message = await client.send_message('@channel', 'hello')
            self.assertEqual(message.id, 42)
            response.json.return_value = {'ok': True, 'result': {'message_id': 43}}
            reply = await message.reply('reply')
            self.assertEqual((reply.id, reply.reply_to_msg_id), (43, 42))
        post.assert_called_with('https://eitaayar.ir/api/fixture-token/sendMessage',
                                data={'chat_id': 'channel', 'text': 'reply',
                                      'reply_to_message_id': 42}, files=None, timeout=TIMEOUT)

    @patch('fnceitaa.transport.requests.post')
    async def test_file_upload_through_http(self, post):
        handles = []
        def upload(url, *, data, files, timeout):
            self.assertEqual(url, 'https://eitaayar.ir/api/fixture-token/sendFile')
            self.assertEqual(data, {'chat_id': 123, 'caption': 'caption', 'reply_to_message_id': 8})
            self.assertEqual(files['file'].read(), b'fixture')
            handles.append(files['file'])
            response = Mock()
            response.json.return_value = {'ok': True, 'result': {'message_id': 44}}
            return response
        post.side_effect = upload
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'photo.jpg'
            path.write_bytes(b'fixture')
            async with Client(token='fixture-token') as client:
                msg = await client.send_file(123, str(path), 'caption', 8)
                self.assertEqual((msg.id, msg.text, msg.media.caption), (44, 'caption', 'caption'))
        self.assertTrue(handles[0].closed)

    @patch('fnceitaa.transport.requests.post')
    async def test_timeout_closes_upload_and_does_not_retry(self, post):
        handles = []
        def fail(url, *, data, files, timeout):
            handles.append(files['file'])
            raise requests.Timeout('https://host/fixture-token')
        post.side_effect = fail
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file.bin'
            path.write_bytes(b'fixture')
            async with Client(token='fixture-token') as client:
                with self.assertRaises(APIRequestError) as raised:
                    await client.send_file(123, str(path))
        self.assertTrue(handles[0].closed)
        self.assertNotIn('fixture-token', str(raised.exception))
        post.assert_called_once()

    @patch('fnceitaa.transport.requests.get')
    async def test_public_read_and_alias_without_token(self, get):
        response = Mock()
        response.text = '''<div class="etme_channel_info_header_title">Channel</div>
        <div class="etme_widget_message_bubble">
        <div class="etme_widget_message_text">hello</div>
        <span class="etme_widget_message_meta"><a href="/channel/7"></a></span></div>'''
        get.return_value = response
        async with Client() as client:
            self.assertEqual(await client.get_info('channel'), await client.get_chat_info('channel'))
            message = await client.get_message('channel', 7)
            self.assertIsNotNone(message)
            if message is not None:
                self.assertEqual((message.id, message.text), (7, 'hello'))
            self.assertIsNone(await client.get_message('channel', 8))

    @patch('fnceitaa.transport.requests.post')
    async def test_invalid_send_inputs_never_reach_http(self, post):
        async with Client(token='fixture-token') as client:
            for reply_id in (True, 0, -1, '7', 1.5):
                with self.subTest(reply_id=reply_id), self.assertRaises(ValueError):
                    await client.send_message(123, 'hello', reply_id)
            with self.assertRaises(ValueError):
                await client.send_file(123, 'missing.bin', caption=123)
            with self.assertRaises(FileNotFoundError):
                await client.send_file(123, 'missing.bin')
        post.assert_not_called()
