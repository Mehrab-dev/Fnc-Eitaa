"""Explicit live acceptance checks restricted to newly created Saved Messages data."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fnceitaa import AccountClient, MediaItem, MediaKind, Router
from fnceitaa.errors import AccountTransportError


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


class AcceptanceRun:
    def __init__(self, directory):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=False)
        self.tag = directory.name
        self.records = []
        self.created = set()
        self.log_path = directory / 'results.jsonl'

    def record(self, name, status, **details):
        entry = dict(time=datetime.now(timezone.utc).isoformat(), test=name, status=status, **details)
        self.records.append(entry)
        with self.log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        print(json.dumps(entry, ensure_ascii=True), flush=True)

    async def check(self, name, operation):
        self.record(name, 'started')
        try:
            result = await asyncio.wait_for(operation(), timeout=120)
        except Exception as error:
            details = {'error_type': type(error).__name__}
            if hasattr(error, 'rpc_type'):
                details['rpc_type'] = error.rpc_type
            if isinstance(error, AssertionError):
                details['assertion'] = str(error)
            self.record(name, 'failed', **details)
            return None
        self.record(name, 'passed')
        return result

    async def send(self, client, label, **options):
        message = await client.send_message('self', f'[{self.tag}] {label}', **options)
        self.created.add(message.id)
        self.record(label, 'receipt', message_ids=[message.id])
        return message

    async def readback(self, client, message, *, text=None, media=None):
        fetched = await self.wait_message(client, message.id)
        require(fetched is not None, 'Created message could not be read back')
        if text is not None:
            require(fetched.text == text, 'Readback text differs')
        if media is not None:
            require(fetched.media is not None and fetched.media.kind == media, 'Readback media kind differs')
        return fetched

    async def wait_message(self, client, identifier, *, deleted=False):
        for attempt in range(6):
            try:
                fetched = await client.get_message('self', identifier)
            except AccountTransportError:
                if attempt == 5:
                    raise
                await asyncio.sleep(1)
                continue
            if (fetched is None) == deleted:
                return fetched
            if attempt < 5:
                await asyncio.sleep(1)
        require(False, 'Readback did not reach expected presence/absence within bounded read-only polling')

    def fixtures(self):
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        fixtures = self.directory / 'fixtures'
        fixtures.mkdir()
        (fixtures / 'sample.txt').write_text(f'{self.tag}\nSynthetic UTF-8 document.\nفایل آزمایشی ایتا\n', encoding='utf-8')
        (fixtures / 'second.txt').write_text(f'{self.tag}: second document', encoding='utf-8')
        (fixtures / 'multipart.bin').write_bytes(bytes(range(256)) * 8192)
        for filename, color in [('photo.png', (35, 130, 210)), ('photo2.png', (220, 110, 40))]:
            def chunk(kind, data):
                return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
            pixels = (b'\x00' + bytes(color) * 320) * 240
            png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 320, 240, 8, 2, 0, 0, 0))
            png += chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')
            (fixtures / filename).write_bytes(png)
        commands = [
            ['-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=15:d=2', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(fixtures / 'video.mp4')],
            ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:a', 'libopus', str(fixtures / 'voice.ogg')],
        ]
        for command in commands:
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', *command], check=True, capture_output=True)
        self.record('synthetic_fixtures', 'passed', files=7)
        return fixtures

    async def event(self, updates, router, kind, identifier, *, replay_pts=0):
        found = False
        for attempt in range(4):
            batch = await updates.poll(replay_pts=replay_pts if attempt == 0 else 0)
            for event in batch.events:
                if identifier in event.message_ids and event.kind == kind:
                    await router.emit(event.kind, event)
                    found = True
            await updates.ack(batch)
            if found:
                return
            await asyncio.sleep(1)
        require(found, f'{kind} event for new test message was not observed')

    async def execute(self, session):
        fixtures = self.fixtures()
        client = AccountClient(session, auto_login=False)
        await client.connect()
        require(client.is_authorized, 'Existing session is not authorized; no OTP requested')
        self.record('existing_session_connect', 'passed')
        try:
            await self.scenarios(client, fixtures)
        finally:
            await client.disconnect()
            self.record('disconnect', 'passed')
        await client.connect()
        require(client.is_authorized, 'Reconnect did not authorize the saved session')
        self.record('reconnect_without_otp', 'passed')
        await client.disconnect()

    async def scenarios(self, client, fixtures):
        async def identity():
            me = await client.get_me()
            peer = await client.resolve_peer('self')
            require(peer.is_self and peer.id == me['id'], 'Self peer identity mismatch')
        await self.check('get_me_and_resolve_self', identity)
        await self.check('get_full_user_self', lambda: client.get_full_user('self'))
        await self.check('get_chat_info_self', lambda: client.get_chat_info('self'))
        updates = client.updates(self.directory / 'events.checkpoint')
        await self.check('initialize_updates', updates.initialize)
        router = Router()
        handled = Counter()
        async def handle(event):
            handled[event.kind] += 1
        for kind in ('new', 'edit', 'delete'):
            router.on(kind)(handle)

        async def short_text():
            message = await self.send(client, 'سلام؛ تست متن کوتاه ✅')
            await self.readback(client, message, text=f'[{self.tag}] سلام؛ تست متن کوتاه ✅')
            return message
        original = await self.check('send_short_text_and_readback', short_text)
        if original is not None:
            await self.check('new_event_and_router', lambda: self.event(updates, router, 'new', original.id))
            async def reply():
                sent = await self.send(client, 'ریپلای آزمایشی', reply_to_msg_id=original.id)
                fetched = await self.readback(client, sent)
                require(fetched.reply_to_msg_id == original.id, 'Reply linkage differs')
            await self.check('reply_and_verify_link', reply)
            async def edit():
                text = f'[{self.tag}] متن ویرایش شد ✅'
                await client.edit_message('self', original.id, text)
                await self.readback(client, original, text=text)
            await self.check('edit_and_readback', edit)
            await self.check('edit_event_and_router', lambda: self.event(updates, router, 'edit', original.id))
            for name in ('forward_message', 'copy_message'):
                async def forward(method=name):
                    receipt = await getattr(client, method)('self', 'self', original.id)
                    self.created.add(receipt.id)
                    self.record(method, 'receipt', message_ids=[receipt.id])
                    fetched = await self.wait_message(client, receipt.id)
                    require(fetched is not None and fetched.text == f'[{self.tag}] متن ویرایش شد ✅', 'Forward/copy readback differs')
                await self.check(name + '_and_readback', forward)

        async def long_text():
            text = f'[{self.tag}] چندخطی و یونیکد\n' + 'آزمایش Python 🚀\n' * 120
            sent = await client.send_message('self', text, silent=True, no_webpage=True)
            self.created.add(sent.id)
            await self.readback(client, sent, text=text)
            return sent
        second = await self.check('multiline_unicode_silent_no_webpage', long_text)
        if original is not None and second is not None:
            async def multi_forward():
                receipts = await client.forward_messages('self', 'self', [original.id, second.id])
                require(len(receipts) == 2, 'Expected two forward receipts')
                self.record('forward_multiple', 'receipt', message_ids=[receipt.id for receipt in receipts])
                for receipt in receipts:
                    self.created.add(receipt.id)
                    await self.wait_message(client, receipt.id)
            await self.check('forward_multiple_and_readback', multi_forward)

        async def history():
            page = await client.get_history_page('self', limit=2)
            require(len(page.messages) > 0, 'History page is empty')
            if page.next_offset_id is not None:
                older = await client.get_history_page('self', limit=2, offset_id=page.next_offset_id)
                require(all(item.id < page.next_offset_id for item in older.messages), 'History pagination boundary differs')
            recent = await client.get_history('self', limit=3)
            streamed = [item async for item in client.iter_history('self', limit=3, page_size=2)]
            require([item.id for item in recent] == [item.id for item in streamed], 'History iterator differs')
        await self.check('history_list_pages_and_iterator', history)

        for filename, kind, options in [
            ('sample.txt', MediaKind.DOCUMENT, {}), ('photo.png', MediaKind.PHOTO, {}),
            ('video.mp4', MediaKind.VIDEO, dict(duration=2, width=320, height=240)),
            ('voice.ogg', MediaKind.VOICE, dict(duration=2)), ('multipart.bin', MediaKind.DOCUMENT, {}),
        ]:
            async def media_test(filename=filename, kind=kind, options=options):
                progress = []
                method = {MediaKind.DOCUMENT: client.send_file, MediaKind.PHOTO: client.send_photo,
                          MediaKind.VIDEO: client.send_video, MediaKind.VOICE: client.send_voice}[kind]
                sent = await method('self', fixtures / filename, caption=f'[{self.tag}] {kind.value}',
                                    progress=lambda *values: progress.append(values), **options)
                self.created.add(sent.id)
                self.record('media_' + filename, 'receipt', message_ids=[sent.id])
                await self.readback(client, sent, media=kind)
                destination = self.directory / ('download_' + filename)
                downloaded = await client.download_media('self', sent.id, destination)
                require(downloaded.size > 0, 'Downloaded file is empty')
                require(downloaded.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest(), 'Download digest differs')
                if kind != MediaKind.PHOTO:
                    require(destination.read_bytes() == (fixtures / filename).read_bytes(), 'Media roundtrip bytes differ')
                require(bool(progress), 'Upload progress callback was not invoked')
                self.record('download_' + filename, 'evidence', size=downloaded.size, sha256=downloaded.sha256,
                            byte_identical=kind != MediaKind.PHOTO)
            await self.check('send_read_download_' + filename, media_test)

        async def staged_upload():
            uploaded = await client.upload_file(fixtures / 'sample.txt', peer='self')
            sent = await client.send_file('self', uploaded, caption=f'[{self.tag}] staged upload')
            self.created.add(sent.id)
            await self.readback(client, sent, media=MediaKind.DOCUMENT)
        await self.check('upload_file_then_send_uploaded_file', staged_upload)

        for label, items in [
            ('photo_album', [MediaItem(fixtures / 'photo.png', caption=f'[{self.tag}] photo album'), MediaItem(fixtures / 'photo2.png')]),
            ('mixed_album', [MediaItem(fixtures / 'photo.png', caption=f'[{self.tag}] mixed album'), MediaItem(fixtures / 'video.mp4', kind=MediaKind.VIDEO, duration=2, width=320, height=240)]),
            ('document_album', [MediaItem(fixtures / 'sample.txt', kind=MediaKind.DOCUMENT, caption=f'[{self.tag}] documents'), MediaItem(fixtures / 'second.txt', kind=MediaKind.DOCUMENT)]),
        ]:
            async def album(items=items, label=label):
                messages = await client.send_album('self', items)
                require(len(messages) == 2, 'Expected two album messages')
                self.record(label, 'receipt', message_ids=[message.id for message in messages])
                fetched = []
                for message in messages:
                    self.created.add(message.id)
                    fetched.append(await self.readback(client, message))
                require(fetched[0].grouped_id is not None and fetched[0].grouped_id == fetched[1].grouped_id, 'Album grouping differs')
            await self.check(label + '_and_grouping', album)

        async def deletion():
            disposable = await self.send(client, 'این پیام برای تست حذف ساخته شده')
            await self.check('disposable_new_event', lambda: self.event(updates, router, 'new', disposable.id, replay_pts=1))
            require(disposable.id in self.created, 'Delete target is not owned by this run')
            await self.wait_message(client, disposable.id)
            await client.delete_messages('self', [disposable.id], revoke=False)
            await self.wait_message(client, disposable.id, deleted=True)
            return disposable.id
        deleted = await self.check('delete_new_test_message_and_verify', deletion)
        if deleted is not None:
            await self.check('delete_event_and_router', lambda: self.event(updates, router, 'delete', deleted))
        self.record('router_handlers', 'evidence', counts=dict(handled))
        await self.check('durable_queue_scenarios', lambda: self.queue_scenarios(client, fixtures))
        for feature in ('OTP/resend/password/revocation', 'groups/channels/members', 'Eitaayar token API',
                        'large files above 2 MiB', 'forced network/process failure', 'browser visual confirmation'):
            self.record(feature, 'not_tested', reason='Outside Saved Messages scope or requires separate setup')

    async def queue_scenarios(self, client, fixtures):
        path = self.directory / 'acceptance.queue.sqlite3'
        with client.queue(path) as queue:
            await queue.updates.initialize()
            pending = await queue.enqueue_message('self', f'[{self.tag}] durable queue', key='message')
            duplicate = await queue.enqueue_message('self', f'[{self.tag}] durable queue', key='message')
            require(pending.id == duplicate.id, 'Queue dedupe key created a duplicate')
            cancelled = await queue.enqueue_message('self', f'[{self.tag}] never transmit', key='cancel')
            if cancelled.state == 'pending':
                queue.store.cancel_job(cancelled.id)
            else:
                require(cancelled.state == 'cancelled', 'Unexpected cancellation job state')
            require(await queue.dispatch_one(cancelled.id) is None, 'Cancelled job was dispatched')
        with client.queue(path) as queue:
            require(queue.store.get_job(pending.id).state in ('pending', 'confirmed'), 'Job did not persist')
            self.record('queue_persistence_dedupe_cancel', 'passed')

            async def dispatch(job):
                result = queue.store.get_job(job.id)
                if result.state == 'pending':
                    result = await queue.dispatch_one(job.id)
                require(result is not None and result.state == 'confirmed', 'Queued operation was not confirmed')
                ids = result.receipt['message_ids']
                self.created.update(ids)
                self.record('queue_' + result.operation, 'receipt', message_ids=ids)
                return ids

            identifiers = await dispatch(pending)
            target = identifiers[0]
            await self.wait_message(client, target)
            self.record('queue_send_readback', 'passed')
            edit = await queue.enqueue_edit('self', target, f'[{self.tag}] queue edited', key='edit')
            await dispatch(edit)
            fetched = await self.wait_message(client, target)
            require(fetched is not None and fetched.text == f'[{self.tag}] queue edited', 'Queued edit differs')
            self.record('queue_edit_readback', 'passed')
            for copy in (False, True):
                job = await queue.enqueue_forward('self', 'self', [target], copy=copy, key='copy' if copy else 'forward')
                for identifier in await dispatch(job):
                    await self.wait_message(client, identifier)
                self.record('queue_copy' if copy else 'queue_forward', 'passed')
            for filename, kind, options in [
                ('sample.txt', MediaKind.DOCUMENT, {}), ('photo.png', MediaKind.PHOTO, {}),
                ('video.mp4', MediaKind.VIDEO, dict(duration=2, width=320, height=240)),
                ('voice.ogg', MediaKind.VOICE, dict(duration=2)),
            ]:
                job = await queue.enqueue_file('self', fixtures / filename, kind=kind,
                                               caption=f'[{self.tag}] queued {kind.value}', key=filename, **options)
                for identifier in await dispatch(job):
                    fetched = await self.wait_message(client, identifier)
                    require(fetched is not None and fetched.media is not None and fetched.media.kind == kind, 'Queued media readback differs')
                self.record('queue_media_' + kind.value, 'passed')
            items = [MediaItem(fixtures / 'photo.png', caption=f'[{self.tag}] queued album'), MediaItem(fixtures / 'photo2.png')]
            job = await queue.enqueue_album('self', items, key='album')
            ids = await dispatch(job)
            photos = [await self.wait_message(client, identifier) for identifier in ids]
            require(len(photos) == 2 and all(photo is not None for photo in photos), 'Queued album missing')
            require(photos[0].grouped_id is not None and photos[0].grouped_id == photos[1].grouped_id, 'Queued album grouping differs')
            self.record('queue_album', 'passed')
            require(target in self.created, 'Queued delete target is not owned by this run')
            await dispatch(await queue.enqueue_delete('self', [target], key='delete'))
            await self.wait_message(client, target, deleted=True)
            self.record('queue_delete_readback', 'passed')
            await queue.poll_once(replay_pts=1)
            inbox = queue.store.list_inbox(limit=1000)
            own = [item for item in inbox if set(item.event.message_ids) & self.created]
            require(bool(own), 'No test event persisted in SQLite inbox')
            event_id = own[0].event.event_id
        with client.queue(path) as queue:
            claimed = queue.store.claim_inbox(event_id)
            require(claimed is not None, 'Inbox event did not persist across reopen')
            queue.store.finish_inbox(claimed, success=True)
            require(queue.store.claim_inbox(event_id) is None, 'Acknowledged inbox event was redelivered')
            self.record('queue_inbox_persistence_and_ack', 'passed')
            self.record('queue_counts', 'evidence', counts=queue.store.stats())

    async def continue_checks(self, session):
        fixtures = self.directory / 'fixtures'
        async with AccountClient(session, auto_login=False) as client:
            receipts = {record['test']: record['message_ids'] for record in self.records if record['status'] == 'receipt'}
            original_id = receipts['سلام؛ تست متن کوتاه ✅'][0]
            reply_id = receipts['ریپلای آزمایشی'][0]
            async def reply_check():
                message = await self.wait_message(client, reply_id)
                require(message.reply_to_msg_id == original_id, 'Reply linkage differs after reread')
            await self.check('reply_delayed_readback_verified', reply_check)

            async def forwards_check():
                history = await client.get_history('self', limit=100)
                test_messages = [message for message in history if self.tag in message.text]
                forwarded = []
                copied = []
                for message in test_messages:
                    if message.id <= original_id:
                        continue
                    raw = (await client._read_rpc('messages.getMessages', {'id': [{'_': 'inputMessageID', 'id': message.id}]})).result
                    for value in raw.get('messages', []):
                        if value.get('fwd_from') is not None:
                            forwarded.append(message.id)
                        if message.id in receipts.get('copy_message', []) and value.get('fwd_from') is None:
                            copied.append(message.id)
                require(len(forwarded) >= 3, 'Single and multiple forward headers not verified')
                require(len(copied) == 1, 'Copy without author header not verified')
                self.created.update(forwarded + copied)
                self.record('forward_copy_headers', 'evidence', forwarded_ids=forwarded, copied_ids=copied)
            await self.check('single_multi_forward_and_copy_attribution', forwards_check)

            async def photo_download():
                identifier = receipts['media_photo.png'][0]
                message = await self.wait_message(client, identifier)
                require(message.media is not None and message.media.kind == MediaKind.PHOTO, 'Photo media type differs')
                result = await client.download_media('self', identifier, self.directory / 'download_photo.jpg')
                require(result.size > 0, 'Photo download is empty')
                self.record('photo_download', 'evidence', size=result.size, sha256=result.sha256)
            await self.check('photo_delayed_readback_and_download', photo_download)

            async def albums_check():
                history = await client.get_history('self', limit=100)
                for caption, expected in [('mixed album', {MediaKind.PHOTO, MediaKind.VIDEO}),
                                          ('documents', {MediaKind.DOCUMENT})]:
                    first = next((message for message in history if self.tag in message.text and caption in message.text), None)
                    require(first is not None and first.grouped_id is not None, 'Album caption/group missing')
                    members = [message for message in history if message.grouped_id == first.grouped_id]
                    require(len(members) == 2, 'Album does not contain two members')
                    require({message.media.kind for message in members if message.media} == expected, 'Album media types differ')
                    for message in members:
                        self.created.add(message.id)
                        await self.wait_message(client, message.id)
                    self.record('album_' + caption, 'evidence', message_ids=[message.id for message in members])
            await self.check('mixed_and_document_albums_delayed_readback', albums_check)

            deleted_id = receipts['این پیام برای تست حذف ساخته شده'][0]
            await self.check('delete_delayed_absence_verified', lambda: self.wait_message(client, deleted_id, deleted=True))
            updates = client.updates(self.directory / 'events.checkpoint')
            router = Router()
            seen = []
            async def handle(event):
                seen.append(event.kind)
            router.on('delete')(handle)
            await self.check('delete_event_checkpoint_reopen', lambda: self.event(updates, router, 'delete', deleted_id))
            await self.check('durable_queue_continued', lambda: self.queue_scenarios(client, fixtures))

            async def restart():
                checkpoint = self.directory / 'restart.checkpoint'
                before = client.updates(checkpoint)
                await before.initialize()
                sent = await self.send(client, 'restart catch-up test')
                after = client.updates(checkpoint)
                await after.initialize()
                router = Router()
                received = []
                async def remember(event):
                    received.append(event.event_id)
                router.on('new')(remember)
                await self.event(after, router, 'new', sent.id)
                reopened = client.updates(checkpoint)
                await reopened.initialize()
                batch = await reopened.poll()
                require(not any(event.event_id in received for event in batch.events), 'Acknowledged event redelivered after reopen')
                await reopened.ack(batch)
            await self.check('ordinary_restart_catchup_and_ack_persistence', restart)

            async def revoke_delete():
                first = await self.send(client, 'disposable revoke delete 1')
                second = await self.send(client, 'disposable revoke delete 2')
                require({first.id, second.id}.issubset(self.created), 'Delete targets not owned by this run')
                await client.delete_messages('self', [first.id, second.id], revoke=True)
                await self.wait_message(client, first.id, deleted=True)
                await self.wait_message(client, second.id, deleted=True)
            await self.check('multiple_delete_with_revoke_and_absence', revoke_delete)

    async def extra_checks(self, session):
        fixtures = self.directory / 'fixtures'
        receipts = {record['test']: record['message_ids'] for record in self.records if record['status'] == 'receipt'}
        async with AccountClient(session, auto_login=False) as client:
            for label, caption, kinds in [('mixed', 'mixed album', {MediaKind.PHOTO, MediaKind.VIDEO}),
                                         ('documents', 'documents', {MediaKind.DOCUMENT})]:
                async def verify_album(caption=caption, kinds=kinds, label=label):
                    history = await client.get_history('self', limit=100)
                    first = next(message for message in history if self.tag in message.text and caption in message.text)
                    members = [message for message in history if message.grouped_id == first.grouped_id]
                    require(first.grouped_id is not None and len(members) == 2, 'Album grouping or size differs')
                    require({message.media.kind for message in members if message.media} == kinds, 'Album media kinds differ')
                    for message in members:
                        await self.wait_message(client, message.id)
                    self.record(label + '_album', 'evidence', message_ids=[message.id for message in members])
                await self.check(label + '_album_final_readback', verify_album)

            async def delete_preflight():
                identifiers = receipts['disposable revoke delete 1'] + receipts['disposable revoke delete 2']
                for identifier in identifiers:
                    message = await self.wait_message(client, identifier)
                    require(self.tag in message.text and identifier in self.created, 'Delete target is not an owned test message')
                await client.delete_messages('self', identifiers, revoke=True)
                for identifier in identifiers:
                    await self.wait_message(client, identifier, deleted=True)
            await self.check('multiple_revoke_delete_after_preflight_readback', delete_preflight)

            async def edit_caption():
                identifier = receipts['media_photo.png'][0]
                text = f'[{self.tag}] کپشن عکس ویرایش شد'
                await client.edit_message('self', identifier, text)
                for attempt in range(6):
                    message = await self.wait_message(client, identifier)
                    if message.text == text:
                        require(message.media is not None and message.media.kind == MediaKind.PHOTO, 'Caption edit changed media')
                        return
                    await asyncio.sleep(1)
                require(False, 'Edited photo caption not observed')
            await self.check('edit_photo_caption_preserve_media', edit_caption)

            async def bytes_reply():
                parent = receipts['سلام؛ تست متن کوتاه ✅'][0]
                sent = await client.send_file('self', (fixtures / 'sample.txt').read_bytes(), filename='bytes-input.txt',
                                              caption=f'[{self.tag}] bytes input and media reply', reply_to_msg_id=parent)
                self.created.add(sent.id)
                self.record('bytes_media_reply', 'receipt', message_ids=[sent.id])
                message = await self.readback(client, sent, media=MediaKind.DOCUMENT)
                require(message.reply_to_msg_id == parent, 'Media reply linkage differs')
            await self.check('bytes_upload_and_media_reply', bytes_reply)

            for name, source_name in [('copy_message', 'media_photo.png'), ('forward_message', 'media_video.mp4')]:
                async def media_operation(name=name, source_name=source_name):
                    source = await self.wait_message(client, receipts[source_name][0])
                    result = await getattr(client, name)('self', 'self', source.id)
                    self.created.add(result.id)
                    self.record(name + '_media', 'receipt', message_ids=[result.id])
                    copied = await self.wait_message(client, result.id)
                    require(copied.media is not None and source.media is not None and copied.media.kind == source.media.kind,
                            'Forwarded/copied media kind differs')
                    raw = (await client._read_rpc('messages.getMessages', {'id': [{'_': 'inputMessageID', 'id': result.id}]})).result
                    if name == 'copy_message':
                        require(raw['messages'][0].get('fwd_from') is None, 'Media copy still has author attribution')
                await self.check(name + '_media_and_readback', media_operation)

            async def maximum_text():
                prefix = f'[{self.tag}] maximum plain text\n'
                text = prefix + 'x' * (4096 - len(prefix))
                sent = await client.send_message('self', text)
                self.created.add(sent.id)
                self.record('maximum_text', 'receipt', message_ids=[sent.id])
                await self.readback(client, sent, text=text)
            await self.check('maximum_4096_unit_text_and_readback', maximum_text)

    def finish(self):
        results = [item for item in self.records if item['status'] in ('passed', 'failed', 'not_tested')]
        report = ['# Saved Messages live acceptance', '', f'Run: `{self.tag}`', '',
                  'Only synthetic data and messages created by this run were modified or deleted.',
                  'No OTP, phone number, token, session contents, profile contents or pre-existing message contents are logged.',
                  '', '| Test | Result | Detail |', '| --- | --- | --- |']
        for item in results:
            detail = item.get('assertion', item.get('rpc_type', item.get('error_type', item.get('reason', ''))))
            report.append(f'| {item["test"]} | {item["status"]} | {detail} |')
        report.extend(['', f'Status counts: {dict(Counter(item["status"] for item in results))}',
                       '', 'Receipt and download evidence: `results.jsonl`. Samples remain in Saved Messages except dedicated deletion targets.'])
        (self.directory / 'REPORT.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
        print('REPORT: ' + str(self.directory / 'REPORT.md'), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--continue-run', action='store_true')
    parser.add_argument('--extra-checks', action='store_true')
    parser.add_argument('--session', type=Path, default=ROOT / 'account.session')
    parser.add_argument('--run-id', default=datetime.now(timezone.utc).strftime('saved-messages-%Y%m%dT%H%M%SZ'))
    args = parser.parse_args()
    if not args.live:
        print('Offline. Use --live only to test your own Saved Messages with an existing session.')
        return
    if not args.run_id or any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for character in args.run_id):
        parser.error('run-id must contain only letters, digits, hyphens and underscores')
    if args.extra_checks and not args.continue_run:
        parser.error('--extra-checks requires --continue-run')
    directory = ROOT / '.fnceitaa' / 'live-tests' / args.run_id
    if args.continue_run:
        run = AcceptanceRun.__new__(AcceptanceRun)
        run.directory, run.tag, run.created = directory, directory.name, set()
        run.log_path = directory / 'results.jsonl'
        run.records = [json.loads(line) for line in run.log_path.read_text(encoding='utf-8').splitlines()]
        run.created.update(identifier for record in run.records for identifier in record.get('message_ids', []))
    else:
        run = AcceptanceRun(directory)
    try:
        if args.extra_checks:
            asyncio.run(run.extra_checks(args.session))
        else:
            asyncio.run(run.continue_checks(args.session) if args.continue_run else run.execute(args.session))
    except Exception as error:
        run.record('run_stopped', 'failed', error_type=type(error).__name__)
    finally:
        run.finish()


if __name__ == '__main__':
    main()
