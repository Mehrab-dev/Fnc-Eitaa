import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from fnceitaa.transport import EitaaBackend, TIMEOUT


PAGE = '''<div class="etme_channel_info_header_title"><span> Test Channel </span></div>
<div class="etme_page_photo_image"><img src="/avatar.jpg"></div>
<span class="counter_value">1K</span>
<div class="etme_widget_message_bubble">
  <a class="etme_widget_message_photo_wrap" style="background-image:url('https://cdn.example/image.jpg')"></a>
  <span class="etme_widget_message_meta"><a href="https://eitaa.com/channel/7"><time datetime="2026-10-14"></time></a></span>
</div>
<div class="etme_widget_message_bubble">
  <div class="etme_widget_message_text">hello<br>world</div>
  <span class="etme_widget_message_meta"><a href="/channel/8"><time datetime="2026-10-14"></time></a></span>
</div>'''


class TransportTests(unittest.TestCase):
    def test_media_only_message_and_absolute_image_url(self):
        messages = EitaaBackend._parse_messages(BeautifulSoup(PAGE, 'html.parser'))
        self.assertEqual([m['message_number'] for m in messages], [7, 8])
        self.assertEqual(messages[0]['text'], '')
        self.assertEqual(messages[0]['image_link'], 'https://cdn.example/image.jpg')
        self.assertEqual(messages[1]['text'], 'hello\nworld')

    @patch('fnceitaa.transport.requests.get')
    def test_get_message_bounds_history_and_sets_timeout(self, get):
        get.return_value = Mock(text=PAGE)
        msg = EitaaBackend.get_message('channel', 7)
        self.assertEqual(msg['message_number'], 7)
        get.assert_called_once_with('https://eitaa.com/channel', params={'before': 8}, timeout=TIMEOUT)
        get.return_value.raise_for_status.assert_called_once()

    @patch('fnceitaa.transport.requests.get')
    def test_chat_info(self, get):
        get.return_value = Mock(text=PAGE)
        info = EitaaBackend.get_info('channel')
        self.assertEqual(info['name'], 'Test Channel')
        self.assertTrue(info['is_channel'])
        self.assertEqual(info['image_url'], 'https://eitaa.com/avatar.jpg')

    @patch('fnceitaa.transport.requests.post')
    def test_upload_closes_handle_and_uses_timeout(self, post):
        handles = []
        def request(url, *, data, files, timeout):
            self.assertEqual(url, 'https://eitaayar.ir/api/test/sendFile')
            self.assertEqual(timeout, TIMEOUT)
            self.assertEqual(files['file'].read(), b'image')
            handles.append(files['file'])
            response = Mock()
            response.json.return_value = {'ok': True, 'result': {'message_id': 9}}
            return response
        post.side_effect = request
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.jpg'
            path.write_bytes(b'image')
            EitaaBackend('test').send_file(123, str(path))
        self.assertTrue(handles[0].closed)
