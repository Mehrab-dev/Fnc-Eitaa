import argparse
import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fnceitaa import (AccountClient, AccountMedia, MediaItem, MediaKind, UploadInterruptedError,
                      UploadCancelledError, MessageOperationUncertainError, MessageOperationCancelledError,
                      SessionError, UnsupportedOperationError)
from fnceitaa.account import AccountHTTPTransport
from fnceitaa.account_files import parse_media
from fnceitaa.account_media import PART_SIZE, BIG_FILE_SIZE, UPLOAD_ENDPOINT, DOWNLOAD_ENDPOINT
from fnceitaa.account_messages import parse_message
from fnceitaa.errors import AccountRPCError, AccountTransportError, AccountStateError, SessionRevokedError
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore
import test_account_peers as fixtures
from test_account_peers import message, rpc, SELF
from test_account_operations import source_messages, updates


JPEG = b'\xff\xd8\xff' + b'test bytes, not a real encoded photo'
OPUS = b'OggS' + b'\0' * 24 + b'OpusHead' + b'fixture'


def document(size=3, *, identifier=700, reference=b'private-reference', attributes=None):
    return dict(_='messageMediaDocument', document=dict(_='document', id=identifier, access_hash=-800,
        file_reference=reference, date=100, mime_type='application/octet-stream', size=size,
        dc_id=1, attributes=attributes or [{'_': 'documentAttributeFilename', 'file_name': '../../untrusted.bin'}]))


def photo(size=3):
    return dict(_='messageMediaPhoto', photo=dict(_='photo', id=900, access_hash=-901,
        file_reference=b'photo-reference', date=100, dc_id=1, sizes=[dict(_='photoSize', type='x', w=10, h=20, size=size)]))


def media_reply(tokens=(777,), ids=(100,), *, media=None, grouped=None):
    items = [dict(_='updateMessageID', random_id=token, id=mid) for token, mid in zip(tokens, ids)]
    for mid in ids:
        raw = {**message(mid=mid), 'media': media or document()}
        if grouped is not None:
            raw['grouped_id'] = grouped
        items.append(dict(_='updateNewMessage', message=raw, pts=20, pts_count=1))
    return updates(items)


def file_reply(data):
    return rpc({'_': 'upload.file', 'type': {'_': 'storage.fileUnknown'}, 'mtime': 100, 'bytes': data})


class MediaMetadataTests(unittest.TestCase):
    def test_document_audio_voice_video_and_private_repr(self):
        for attribute, expected in [({'_': 'documentAttributeAudio', 'pFlags': {'voice': True}, 'duration': 2}, MediaKind.VOICE),
                                     ({'_': 'documentAttributeAudio', 'duration': 2}, MediaKind.AUDIO),
                                     ({'_': 'documentAttributeVideo', 'duration': 2, 'w': 10, 'h': 20}, MediaKind.VIDEO)]:
            result = parse_media(document(attributes=[attribute]))
            self.assertEqual(result.kind, expected)
            self.assertEqual(result.duration, 2)
            self.assertEqual(result.to_location()['_'], 'inputDocumentFileLocation')
            self.assertNotIn('private-reference', repr(result))
        raw = parse_media(document())
        self.assertEqual(raw.filename, '../../untrusted.bin')  # Never used as a local path.
        self.assertNotIn('untrusted', repr(raw))

    def test_photo_largest_size_and_progressive_cached_or_stripped(self):
        value = photo()
        value['photo']['sizes'] += [dict(_='photoSizeProgressive', type='y', w=30, h=40, sizes=[4, 8]),
                                    dict(_='photoStrippedSize', type='i', bytes=b'not a JPEG')]
        info = parse_media(value)
        self.assertEqual((info.size, info.thumb_size, info.width), (8, 'y', 30))
        self.assertEqual(info.to_location()['_'], 'inputPhotoFileLocation')
        value['photo']['sizes'] = [dict(_='photoCachedSize', type='s', w=1, h=1, bytes=b'jpeg')]
        self.assertEqual(parse_media(value).cached_bytes, b'jpeg')
        value['photo']['sizes'] = [dict(_='photoStrippedSize', type='i', bytes=b'preview')]
        self.assertIsNone(parse_media(value))

    def test_binary_message_preserves_media_metadata_and_grouped_id(self):
        value = {**message(), 'media': photo(), 'grouped_id': 12345}
        codec = TLCodec()
        result = parse_message(codec.decode(codec.encode(value), 'Message'), fixtures.entity_peer(fixtures.USER), {})
        self.assertTrue(result.has_media)
        self.assertEqual(result.media.size, 3)
        self.assertEqual(result.grouped_id, 12345)

    def test_empty_cached_preview_does_not_hide_downloadable_photo(self):
        value = photo(size=47316)
        value['photo']['sizes'].insert(0, dict(_='photoCachedSize', type='s', w=67, h=90, bytes=b''))
        value['photo']['sizes'].insert(1, dict(_='photoSize', type='m', w=240, h=320, size=7687))
        codec = TLCodec()
        decoded = codec.decode(codec.encode({**message(), 'media': value}), 'Message')
        result = parse_message(decoded, fixtures.entity_peer(fixtures.USER), {})
        self.assertEqual((result.media.size, result.media.thumb_size), (47316, 'x'))
        self.assertIsNone(result.media.cached_bytes)

    def test_only_empty_cached_preview_has_no_downloadable_media(self):
        value = photo()
        value['photo']['sizes'] = [dict(_='photoCachedSize', type='s', w=67, h=90, bytes=b'')]
        self.assertIsNone(parse_media(value))

    def test_malformed_cached_preview_still_fails_validation(self):
        for data in (None, '', [], 0):
            value = photo()
            value['photo']['sizes'].insert(0, dict(_='photoCachedSize', type='s', w=67, h=90, bytes=data))
            with self.assertRaisesRegex(TLCodecError, 'Invalid cached photo bytes'):
                parse_media(value)

    def test_bad_known_media_metadata_is_rejected(self):
        for value in (document(size=-1), document(reference='bad'),
                      {'_': 'messageMediaPhoto', 'photo': {'_': 'document'}}):
            with self.assertRaises(TLCodecError):
                parse_media(value)
        self.assertIsNone(parse_media({'_': 'messageMediaEmpty'}))


class MediaClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AccountReadTests.asyncSetUp(self)
        self.peer = fixtures.entity_peer(fixtures.USER)
        self.media = self.client.media()
        self.output = Path(self.directory.name, 'received.bin')

    async def test_upload_chunk_boundaries_md5_and_first_part_flags(self):
        payload = b'x' * PART_SIZE + b'end'
        self.invoke.return_value = rpc(True)
        progress = []
        uploaded = await self.client.upload_file(payload, peer=self.peer, filename='fixture.bin',
                                                  progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(uploaded.md5_checksum, hashlib.md5(payload).hexdigest())
        self.assertEqual((uploaded.parts, uploaded.big, uploaded.size), (2, False, len(payload)))
        calls = self.invoke.await_args_list
        self.assertEqual([c.args[0] for c in calls], ['upload.saveFilePart'] * 2)
        self.assertEqual(calls[0].args[1]['totalFileSize'], len(payload))
        self.assertEqual(calls[0].args[1]['peer'], {'_': 'peerUser', 'user_id': 42})
        self.assertNotIn('peer', calls[1].args[1])
        self.assertEqual(calls[1].args[1]['bytes'], b'end')
        self.assertEqual(progress[-1], (len(payload), len(payload)))
        self.assertNotIn('fixture.bin', repr(uploaded))

    async def test_exact_big_boundary_announces_quota_before_big_parts(self):
        path = Path(self.directory.name, 'big.bin')
        with path.open('wb') as stream:
            stream.truncate(BIG_FILE_SIZE)
        self.invoke.return_value = rpc(True)
        uploaded = await self.media.upload_file(path, peer=self.peer)
        self.assertTrue(uploaded.big)
        self.assertEqual(uploaded.to_input_file()['_'], 'inputFileBig')
        calls = self.invoke.await_args_list
        self.assertEqual(calls[0].args[0], 'upload.saveFilePart')
        self.assertEqual(calls[0].args[1]['bytes'], b'\0')
        self.assertEqual(calls[1].args[0], 'upload.saveBigFilePart')
        self.assertEqual(calls[1].args[1]['file_total_parts'], BIG_FILE_SIZE // PART_SIZE)
        self.assertEqual(len(calls), 1 + BIG_FILE_SIZE // PART_SIZE)

    async def test_document_send_uses_force_file_and_exact_random_mapping(self):
        self.invoke.side_effect = [rpc(True), rpc(media_reply())]
        result = await self.client.send_file(self.peer, b'abc', filename='fixture.txt', caption='caption', random_id=777, reply_to_msg_id=7)
        self.assertEqual((result.id, result.media.size), (100, 3))
        params = self.invoke.call_args.args[1]
        self.assertEqual(params['reply_to_msg_id'], 7)
        self.assertEqual(params['message'], 'caption')
        self.assertTrue(params['media']['force_file'])
        self.assertEqual(params['media']['mime_type'], 'text/plain')
        self.assertEqual([c.args[0] for c in self.invoke.await_args_list], ['upload.saveFilePart', 'messages.sendMedia'])

    async def test_photo_voice_video_attributes(self):
        for kind, data, kwargs in [('photo', JPEG, {}), ('voice', OPUS, {'duration': 2}),
                                   ('video', b'video fixture', {'duration': 3, 'width': 10, 'height': 20, 'mime_type': 'video/mp4'})]:
            self.invoke.reset_mock()
            self.invoke.side_effect = [rpc(True), rpc(media_reply(media=photo() if kind == 'photo' else document()))]
            await getattr(self.client, 'send_' + kind)(self.peer, data, random_id=777, **kwargs)
            media = self.invoke.call_args.args[1]['media']
            if kind == 'photo':
                self.assertEqual(media['_'], 'inputMediaUploadedPhoto')
            else:
                self.assertFalse(media['force_file'])
                self.assertEqual(media['attributes'][-1]['duration'], kwargs['duration'])
                if kind == 'voice':
                    self.assertTrue(media['attributes'][-1]['voice'])
                    self.assertEqual(media['mime_type'], 'audio/ogg')

    async def test_uploaded_file_reuse_has_no_second_chunk_upload_and_is_session_bound(self):
        self.invoke.return_value = rpc(True)
        uploaded = await self.client.upload_file(b'abc', peer=self.peer)
        self.invoke.reset_mock()
        self.invoke.return_value = rpc(media_reply())
        await self.client.send_file(self.peer, uploaded, random_id=777)
        self.invoke.assert_awaited_once()
        self.assertEqual(self.invoke.call_args.args[0], 'messages.sendMedia')
        self.invoke.reset_mock()
        with self.assertRaises(SessionError):
            await self.client.send_file(self.peer, replace(uploaded, _owner=('different',)), random_id=777)
        self.invoke.assert_not_awaited()

    async def test_invalid_files_formats_and_options_never_upload(self):
        for kind, data, kwargs in [('photo', b'not jpeg', {}), ('voice', b'OggSnotOpus', {'duration': 1}),
                                   ('video', b'x', {'duration': None, 'width': 1, 'height': 1})]:
            with self.assertRaises(ValueError):
                await self.client.send_file(self.peer, data, kind=kind, **kwargs)
        for kwargs in ({'caption': 'x' * 1025}, {'caption': '\ud800'}, {'random_id': 0},
                       {'silent': 1}, {'reply_to_msg_id': 0}, {'filename': '../bad'}, {'mime_type': 'bad type'}):
            with self.assertRaises(ValueError):
                await self.client.send_file(self.peer, b'abc', **kwargs)
        with self.assertRaises(ValueError):
            await self.client.upload_file(b'')
        self.invoke.assert_not_awaited()

    async def test_false_or_interrupted_chunk_never_sends_message(self):
        for failure in (rpc(False), AccountTransportError('offline'), TLCodecError('bad')):
            self.invoke.reset_mock()
            self.invoke.side_effect = [failure]
            with self.assertRaises(UploadInterruptedError):
                await self.client.send_file(self.peer, b'abc')
            self.invoke.assert_awaited_once()
            self.assertEqual(self.invoke.call_args.args[0], 'upload.saveFilePart')

    async def test_source_modified_during_upload_never_sends_message(self):
        path = Path(self.directory.name, 'changing.bin')
        path.write_bytes(b'abc')
        self.invoke.return_value = rpc(True)

        def change(done, total):
            path.write_bytes(b'abcd')

        with self.assertRaises(ValueError):
            await self.client.send_file(self.peer, path, progress=change)
        self.assertEqual([c.args[0] for c in self.invoke.await_args_list], ['upload.saveFilePart'])

    async def test_chunk_cancellation_and_message_cancellation_are_distinct(self):
        self.invoke.side_effect = asyncio.CancelledError
        with self.assertRaises(UploadCancelledError):
            await self.client.send_file(self.peer, b'abc')
        self.invoke.side_effect = [rpc(True), asyncio.CancelledError]
        with self.assertRaises(MessageOperationCancelledError) as caught:
            await self.client.send_file(self.peer, b'abc', random_id=777)
        self.assertEqual(caught.exception.random_ids, (777,))

    async def test_final_send_failure_not_retried_or_reuploaded(self):
        for failure in (AccountTransportError('timeout'), rpc(updates([])), rpc(media_reply(tokens=(888,)))):
            self.invoke.reset_mock()
            self.invoke.side_effect = [rpc(True), failure]
            with self.assertRaises(MessageOperationUncertainError) as caught:
                await self.client.send_file(self.peer, b'abc', random_id=777)
            self.assertEqual(caught.exception.operation, 'send_media')
            self.assertEqual(caught.exception.random_ids, (777,))
            self.assertEqual(self.invoke.await_count, 2)

    async def test_album_materializes_each_item_then_sends_one_correlated_batch(self):
        self.invoke.side_effect = [rpc(True), rpc(photo()), rpc(True), rpc(photo()),
                                  rpc(media_reply(tokens=(888, 777), ids=(101, 100), media=photo(), grouped=123))]
        results = await self.client.send_album(self.peer, [MediaItem(JPEG, caption='one'), MediaItem(JPEG)], random_ids=[777, 888])
        self.assertEqual([r.id for r in results], [100, 101])
        self.assertEqual([r.grouped_id for r in results], [123, 123])
        self.assertEqual([c.args[0] for c in self.invoke.await_args_list],
                         ['upload.saveFilePart', 'messages.uploadMedia'] * 2 + ['messages.sendMultiMedia'])
        params = self.invoke.call_args.args[1]
        self.assertEqual(params['multi_media'][0]['media']['_'], 'inputMediaPhoto')
        self.assertEqual(params['multi_media'][0]['random_id'], 777)

    async def test_album_validates_all_items_before_upload_and_refuses_mixed_documents(self):
        for items in ([MediaItem(JPEG)], [MediaItem(JPEG), MediaItem(b'bad')],
                      [MediaItem(JPEG), MediaItem(b'doc', MediaKind.DOCUMENT)],
                      [MediaItem(OPUS, MediaKind.VOICE, duration=1)] * 2):
            with self.assertRaises(ValueError):
                await self.client.send_album(self.peer, items)
        self.invoke.assert_not_awaited()

    async def test_album_staging_failure_never_sends_batch(self):
        self.invoke.side_effect = [rpc(True), AccountRPCError(400, 'PHOTO_INVALID')]
        with self.assertRaises(AccountRPCError):
            await self.client.send_album(self.peer, [MediaItem(JPEG), MediaItem(JPEG)])
        self.assertNotIn('messages.sendMultiMedia', [c.args[0] for c in self.invoke.await_args_list])

    async def test_download_chunks_hash_and_remote_filename_not_used(self):
        data = b'x' * PART_SIZE + b'end'
        self.invoke.side_effect = [rpc(source_messages([{**message(), 'media': document(len(data))}])),
                                  file_reply(data[:PART_SIZE]), file_reply(data[PART_SIZE:])]
        result = await self.client.download_media(self.peer, 10, self.output)
        self.assertEqual(result.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(self.output.read_bytes(), data)
        self.assertEqual(self.invoke.call_args.args[1]['offset'], PART_SIZE)
        self.assertEqual(self.invoke.call_args.args[1]['location']['thumb_size'], '')
        self.assertEqual(len(list(self.output.parent.glob('*.part'))), 0)

    async def test_download_does_not_overwrite_and_cleans_truncated_transfer(self):
        self.output.write_bytes(b'original')
        self.invoke.return_value = rpc(source_messages([{**message(), 'media': document(3)}]))
        with self.assertRaises(FileExistsError):
            await self.client.download_media(self.peer, 10, self.output)
        self.assertEqual(self.output.read_bytes(), b'original')
        self.invoke.side_effect = [self.invoke.return_value, file_reply(b'ab')]
        with self.assertRaises(TLCodecError):
            await self.client.download_media(self.peer, 10, self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b'original')
        self.assertEqual(list(self.output.parent.glob('*.part')), [])

    async def test_file_reference_expired_refreshes_once_and_only_same_media(self):
        original = rpc(source_messages([{**message(), 'media': document(reference=b'old')}]))
        new = rpc(source_messages([{**message(), 'media': document(reference=b'new')}]))
        self.invoke.side_effect = [original, AccountRPCError(400, 'FILE_REFERENCE_EXPIRED'), new, file_reply(b'abc')]
        await self.client.download_media(self.peer, 10, self.output)
        self.assertEqual(self.invoke.call_args.args[1]['location']['file_reference'], b'new')
        self.assertEqual(self.invoke.await_count, 4)

    async def test_changed_media_or_cdn_redirect_never_publishes_partial(self):
        original = rpc(source_messages([{**message(), 'media': document()}]))
        for replies in ([original, AccountRPCError(400, 'FILE_REFERENCE_EXPIRED'),
                         rpc(source_messages([{**message(), 'media': document(identifier=999)}]))],
                        [original, rpc({'_': 'upload.fileCdnRedirect'})]):
            self.invoke.side_effect = replies
            with self.assertRaises((TLCodecError, UnsupportedOperationError)):
                await self.client.download_media(self.peer, 10, self.output)
            self.assertFalse(self.output.exists())
            self.assertEqual(list(self.output.parent.glob('*.part')), [])

    async def test_cancelled_download_never_publishes_or_leaves_temporary(self):
        self.invoke.side_effect = [rpc(source_messages([{**message(), 'media': document()}])), asyncio.CancelledError]
        with self.assertRaises(asyncio.CancelledError):
            await self.client.download_media(self.peer, 10, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.glob('*.part')), [])

    async def test_download_size_limit_and_no_media_fail_before_transfer(self):
        self.invoke.return_value = rpc(source_messages([{**message(), 'media': document(4)}]))
        with self.assertRaises(ValueError):
            await self.client.download_media(self.peer, 10, self.output, max_size=3)
        self.invoke.assert_awaited_once()
        self.invoke.reset_mock()
        self.invoke.return_value = rpc(source_messages())
        with self.assertRaises(UnsupportedOperationError):
            await self.client.download_media(self.peer, 10, self.output)
        self.invoke.assert_awaited_once()

    async def test_cached_photo_and_zero_size_document_need_no_file_rpc(self):
        cached = photo()
        cached['photo']['sizes'] = [dict(_='photoCachedSize', type='s', w=1, h=1, bytes=b'abc')]
        for value, expected in [(cached, b'abc'), (document(0), b'')]:
            self.invoke.reset_mock()
            self.invoke.return_value = rpc(source_messages([{**message(), 'media': value}]))
            await self.client.download_media(self.peer, 10, self.output, overwrite=True)
            self.invoke.assert_awaited_once()
            self.assertEqual(self.output.read_bytes(), expected)

    async def test_upload_flood_control_and_revocation_do_not_retry_or_send(self):
        self.invoke.side_effect = AccountRPCError(400, 'FLOOD_WAIT_20')
        with self.assertRaises(AccountRPCError):
            await self.client.send_file(self.peer, b'abc')
        self.invoke.assert_awaited_once()
        self.invoke.reset_mock()
        self.invoke.side_effect = AccountStateError('eitaa_updates_expire_token')
        with patch.object(self.client, '_refresh_token', new_callable=AsyncMock) as renew:
            with self.assertRaises(UploadInterruptedError):
                await self.client.send_file(self.peer, b'abc')
            renew.assert_not_awaited()
        self.invoke.side_effect = AccountRPCError(401, 'SESSION_REVOKED')
        with self.assertRaises(SessionRevokedError):
            await self.client.send_file(self.peer, b'abc')
        self.assertFalse(self.client.is_authorized)

    async def test_download_can_renew_token_once_before_read_retry(self):
        self.invoke.side_effect = [rpc(source_messages([{**message(), 'media': document()}])),
                                  AccountStateError('eitaa_updates_expire_token'), file_reply(b'abc')]
        with patch.object(self.client, '_refresh_token', new_callable=AsyncMock) as renew:
            await self.client.download_media(self.peer, 10, self.output)
            renew.assert_awaited_once()
        self.assertEqual(self.invoke.await_count, 3)

    async def test_second_expired_reference_is_not_retried_forever(self):
        source = rpc(source_messages([{**message(), 'media': document()}]))
        self.invoke.side_effect = [source, AccountRPCError(400, 'FILE_REFERENCE_EXPIRED'),
                                  source, AccountRPCError(400, 'FILE_REFERENCE_EXPIRED')]
        with self.assertRaises(AccountRPCError):
            await self.client.download_media(self.peer, 10, self.output)
        self.assertEqual(self.invoke.await_count, 4)
        self.assertFalse(self.output.exists())

    async def test_atomic_publication_refuses_a_new_destination_created_mid_download(self):
        self.invoke.side_effect = [rpc(source_messages([{**message(), 'media': document()}])), file_reply(b'abc')]

        def appeared(source, target):
            Path(target).write_bytes(b'someone else')
            raise FileExistsError

        with patch('fnceitaa.account_media.os.link', side_effect=appeared):
            with self.assertRaises(FileExistsError):
                await self.client.download_media(self.peer, 10, self.output)
        self.assertEqual(self.output.read_bytes(), b'someone else')
        self.assertEqual(list(self.output.parent.glob('*.part')), [])

    async def test_identity_switch_after_upload_never_sends_to_the_old_peer(self):
        self.invoke.return_value = rpc(True)

        def switch(done, total):
            self.client._session = replace(self.client._current_session(), user_id=999)

        with self.assertRaises(SessionError):
            await self.client.send_file(self.peer, b'abc', progress=switch)
        self.assertEqual([c.args[0] for c in self.invoke.await_args_list], ['upload.saveFilePart'])

    async def test_full_nonmedia_ack_is_uncertain_and_partial_album_is_uncertain(self):
        plain = media_reply()
        plain['updates'][1]['message'].pop('media')
        self.invoke.side_effect = [rpc(True), rpc(plain)]
        with self.assertRaises(MessageOperationUncertainError):
            await self.client.send_file(self.peer, b'abc', random_id=777)
        self.invoke.side_effect = [rpc(True), rpc(photo()), rpc(True), rpc(photo()), rpc(media_reply())]
        with self.assertRaises(MessageOperationUncertainError) as caught:
            await self.client.send_album(self.peer, [MediaItem(JPEG)] * 2, random_ids=[777, 888])
        self.assertEqual(caught.exception.operation, 'send_album')
        self.assertEqual(caught.exception.random_ids, (777, 888))

    async def test_document_album_stages_documents_and_retains_caption_order(self):
        self.invoke.side_effect = [rpc(True), rpc(document()), rpc(True), rpc(document()),
                                  rpc(media_reply(tokens=(777, 888), ids=(100, 101), grouped=123))]
        result = await self.client.send_album(self.peer,
            [MediaItem(b'one', MediaKind.DOCUMENT, caption='first'), MediaItem(b'two', MediaKind.DOCUMENT, caption='second')],
            random_ids=[777, 888])
        self.assertEqual(len(result), 2)
        media = self.invoke.call_args.args[1]['multi_media']
        self.assertEqual([item['media']['_'] for item in media], ['inputMediaDocument'] * 2)
        self.assertEqual([item['message'] for item in media], ['first', 'second'])


class MediaWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_specific_urls_binary_chunks_and_round_trip_hash(self):
        from test_account_transport import response_for
        codec = TLCodec()
        replies = [codec.encode([SELF], 'Vector<User>'), codec.encode(True, 'Bool'),
                   codec.encode(media_reply()), codec.encode(source_messages([{**message(), 'media': document()}])),
                   codec.encode(file_reply(b'abc').result)]
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory, 'account.session')
            store = SessionStore(session)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123, 'https://hasan.eitaa.ir/eitaa/', 2496))
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in replies]) as post:
                async with AccountClient(session) as client:
                    peer = fixtures.entity_peer(fixtures.USER)
                    result = await client.send_file(peer, b'abc', filename='test.bin', random_id=777)
                    self.assertEqual(result.media.kind, MediaKind.DOCUMENT)
                    download = await client.download_media(peer, 10, Path(directory, 'download.bin'))
                    self.assertEqual(download.sha256, hashlib.sha256(b'abc').hexdigest())
                self.assertEqual([c.args[0] for c in post.call_args_list],
                                 ['https://hasan.eitaa.ir/eitaa/', UPLOAD_ENDPOINT, UPLOAD_ENDPOINT,
                                  'https://hasan.eitaa.ir/eitaa/', DOWNLOAD_ENDPOINT])


class MediaExampleTests(unittest.TestCase):
    def test_every_offline_command_never_opens_session_network_or_prompt(self):
        from examples.account_media import main
        for verb in ('send', 'photo', 'voice', 'video', 'album', 'download'):
            with patch('sys.argv', ['account_media', verb]), patch('builtins.print'), \
                    patch('examples.account_media.AccountClient') as client, patch('builtins.input') as prompt, \
                    patch('fnceitaa.account.requests.post') as network:
                main()
                client.assert_not_called()
                prompt.assert_not_called()
                network.assert_not_called()


class MediaExampleLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_needs_confirmation_and_preserves_explicit_path(self):
        from examples.account_media import run
        peer = fixtures.entity_peer(fixtures.USER)
        args = argparse.Namespace(session='fixture', command='download', peer='self', load_dialogs=False,
                                  message_id=10, output='chosen.bin', overwrite=False)
        for confirmation in ('no', 'yes'):
            with patch('examples.account_media.AccountClient') as factory, \
                    patch('builtins.input', return_value=confirmation), patch('builtins.print'):
                client = factory.return_value.__aenter__.return_value
                client.resolve_peer = AsyncMock(return_value=peer)
                client.get_message = AsyncMock(return_value=parse_message({**message(), 'media': document()}, peer, {}))
                client.download_media = AsyncMock()
                await run(args)
                self.assertEqual(client.download_media.await_count, 1 if confirmation == 'yes' else 0)
                if confirmation == 'yes':
                    client.download_media.assert_awaited_once_with(peer, 10, 'chosen.bin', overwrite=False)

    async def test_upload_and_send_require_exact_confirmation_after_preview(self):
        from examples.account_media import run
        peer = fixtures.entity_peer(fixtures.USER)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'test.txt')
            path.write_bytes(b'abc')
            for confirmation in ('', 'no', 'y', 'yes'):
                args = argparse.Namespace(session='fixture', command='send', file=[str(path)], peer='self',
                                          load_dialogs=False, caption='', kind='photo', mime_type=None,
                                          duration=None, width=None, height=None, reply_to=None)
                with patch('examples.account_media.AccountClient') as factory, \
                        patch('builtins.input', return_value=confirmation), patch('builtins.print') as output:
                    client = factory.return_value.__aenter__.return_value
                    client.resolve_peer = AsyncMock(return_value=peer)
                    client.media = Mock(return_value=AccountMedia(client))
                    client.send_file = AsyncMock(return_value=parse_message({**message(), 'media': document()}, peer, {}))
                    await run(args)
                    self.assertIn('File:', str(output.call_args_list))
                    self.assertEqual(client.send_file.await_count, 1 if confirmation == 'yes' else 0)


if __name__ == '__main__':
    unittest.main()
