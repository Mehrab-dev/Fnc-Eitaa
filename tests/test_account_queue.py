import argparse
import asyncio
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fnceitaa import (AccountClient, AccountEvent, AccountPeer, MediaItem, MediaKind,
                      QueueError, QueueConflictError, SendMessageUncertainError)
from fnceitaa.account import AccountHTTPTransport
from fnceitaa.account_files import AccountMediaInfo
from fnceitaa.account_messages import AccountMessage
from fnceitaa.account_updates import UpdateCheckpointStore
from fnceitaa.errors import (AccountRPCError, AccountTransportError, UploadInterruptedError,
                            UploadCancelledError, MessageOperationCancelledError)
from fnceitaa.queue_store import OutboxJob, SQLiteCheckpointStore, SQLiteQueueStore, encode_payload, decode_payload
from fnceitaa.session import AccountSession, SessionStore
from test_account_peers import rpc, SELF, USER, message
import test_account_peers as fixtures
from test_account_updates import state, difference, deletion
from test_account_messages import short_sent


OWNER = [123, 'https://hasan.eitaa.ir/eitaa/', 2496, 'fixture-device-fingerprint']


def event(identifier='a', *, kind='new'):
    peer = AccountPeer(42, access_hash=-987)
    media = AccountMediaInfo(MediaKind.DOCUMENT, 7, -8, b'private-reference', 1, 3, 'text/plain')
    value = AccountMessage(10, peer, 100, 'private fixture text', sender=peer, has_media=True, media=media)
    return AccountEvent(kind, identifier * 64, peer, value, (10,), raw={'_': 'fixture', 'bytes': b'abc', 'model': 'not executable'})


def request(text='private text', token=777):
    return dict(peer={'id': 123, 'kind': 'user', 'is_self': True}, text=text, random_id=token)


class QueueStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name, 'test.queue.sqlite3')
        self.store = SQLiteQueueStore(self.path, OWNER)
        self.addCleanup(self.store.close)

    def test_reopen_preserves_owner_job_random_id_and_receipt(self):
        job = self.store.enqueue('send_message', request(), key='one')
        claimed = self.store.claim_job(job.id)
        self.store.finish_job(claimed, 'confirmed', receipt={'message_ids': [100]})
        self.store.close()
        with SQLiteQueueStore(self.path, OWNER) as reopened:
            saved = reopened.get_job(job.id)
            self.assertEqual(saved.state, 'confirmed')
            self.assertEqual(saved.request['random_id'], 777)
            self.assertEqual(saved.receipt['message_ids'], [100])
            self.assertNotIn('private text', repr(saved))

    def test_idempotency_key_ignores_new_correlation_ids_but_rejects_new_intent(self):
        original = self.store.enqueue('send_message', request(), key='event-reply')
        repeated = self.store.enqueue('send_message', dict(reversed(list(request(token=888).items()))), key='event-reply')
        self.assertEqual(original.id, repeated.id)
        self.assertEqual(repeated.request['random_id'], 777)
        with self.assertRaises(QueueConflictError):
            self.store.enqueue('send_message', request('other text'), key='event-reply')
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_two_connections_cannot_claim_the_same_send(self):
        job = self.store.enqueue('send_message', request())
        with SQLiteQueueStore(self.path, OWNER) as second:
            first_claim = self.store.claim_job(job.id)
            self.assertIsNone(second.claim_job(job.id))
            second.finish_job(first_claim, 'confirmed', receipt={'message_ids': [100]})
            with self.assertRaises(QueueConflictError):
                self.store.finish_job(first_claim, 'failed')

    def test_expired_outbox_is_uncertain_and_never_automatically_requeued(self):
        job = self.store.enqueue('send_message', request())
        with patch('fnceitaa.queue_store.time.time', return_value=1000):
            # Existing jobs are due at zero.
            claimed = self.store.claim_job(job.id, lease_seconds=1)
        with patch('fnceitaa.queue_store.time.time', return_value=1002):
            self.assertEqual(self.store.recover_expired()['outbox_uncertain'], 1)
        self.assertEqual(self.store.get_job(job.id).state, 'uncertain')
        self.assertIsNone(self.store.claim_job(job.id))
        with self.assertRaises(QueueConflictError):
            self.store.retry_failed(job.id)
        with self.assertRaises(QueueConflictError):
            self.store.finish_job(claimed, 'confirmed')

    def test_unexpired_claim_is_not_recovered(self):
        job = self.store.enqueue('send_message', request())
        with patch('fnceitaa.queue_store.time.time', return_value=1000):
            self.store.claim_job(job.id, lease_seconds=10)
            self.assertEqual(self.store.recover_expired()['outbox_uncertain'], 0)

    def test_failed_retry_requires_explicit_call_and_respects_wait(self):
        job = self.store.enqueue('send_message', request())
        with patch('fnceitaa.queue_store.time.time', return_value=1000):
            claimed = self.store.claim_job(job.id)
            self.store.finish_job(claimed, 'failed', error='AccountRPCError', retry_after=30)
            with self.assertRaises(QueueConflictError):
                self.store.retry_failed(job.id)
        with patch('fnceitaa.queue_store.time.time', return_value=1031):
            pending = self.store.retry_failed(job.id)
            self.assertEqual(pending.state, 'pending')
            self.assertEqual(pending.request['random_id'], 777)
            self.assertEqual(self.store.claim_job(job.id).attempts, 2)

    def test_uncertain_requires_explicit_verification_and_has_audit_trail(self):
        job = self.store.enqueue('send_message', request())
        self.store.finish_job(self.store.claim_job(job.id), 'uncertain', error='timeout')
        with self.assertRaises(ValueError):
            self.store.resolve_uncertain(job.id, state='failed', note='')
        self.store.resolve_uncertain(job.id, state='confirmed', note='Independently read destination', receipt={'message_ids': [100]})
        self.assertEqual(self.store.get_job(job.id).state, 'confirmed')
        audit = self.store._db.execute('SELECT reason FROM audit WHERE job_id=? ORDER BY sequence DESC LIMIT 1', (job.id,)).fetchone()[0]
        self.assertIn('manual_verification', audit)

    def test_only_pending_jobs_can_be_cancelled(self):
        job = self.store.enqueue('send_message', request())
        self.store.cancel_job(job.id)
        self.assertIsNone(self.store.claim_job(job.id))
        with self.assertRaises(QueueConflictError):
            self.store.cancel_job(job.id)

    def test_atomic_inbox_checkpoint_and_unbounded_id_dedup(self):
        checkpoint = SQLiteCheckpointStore(self.store)
        self.assertIsNone(checkpoint.load())
        checkpoint.commit_batch({'position': 10}, [event()])
        item = self.store.claim_inbox()
        self.assertEqual(item.event, event())
        self.assertNotIn('private fixture text', repr(item))
        self.store.finish_inbox(item)
        checkpoint.commit_batch({'position': 11}, [event()])
        self.assertEqual(self.store.stats()['inbox'], {'done': 1})
        with SQLiteQueueStore(self.path, OWNER) as second:
            self.assertEqual(SQLiteCheckpointStore(second).load(), {'position': 11})

    def test_fifo_uses_insertion_order_when_clock_moves_backward(self):
        with patch('fnceitaa.queue_store.time.time',return_value=2000):
            first=self.store.enqueue('send_message',request('first'))
        with patch('fnceitaa.queue_store.time.time',return_value=1000):
            second=self.store.enqueue('send_message',request('second'))
            cp=SQLiteCheckpointStore(self.store)
            cp.load()
            cp.commit_batch({},[event('b'),event('a')])
        self.assertEqual([j.id for j in self.store.list_jobs()],[first.id,second.id])
        self.assertEqual(self.store.claim_job().id,first.id)
        self.assertEqual(self.store.claim_inbox().event.event_id,'b'*64)

    def test_checkpoint_compare_and_swap_rejects_stale_consumers(self):
        first, second = SQLiteCheckpointStore(self.store), SQLiteCheckpointStore(self.store)
        first.load()
        second.load()
        first.commit_batch({'position': 1}, [event()])
        with self.assertRaises(QueueConflictError):
            second.commit_batch({'position': 2}, [event('b')])
        self.assertEqual(len(self.store.list_inbox()), 1)
        self.assertEqual(first.load(), {'position': 1})

    def test_sql_failure_rolls_back_inbox_and_cursor_together(self):
        cp = SQLiteCheckpointStore(self.store)
        cp.load()
        cp.save({'position': 1})
        self.store._db.execute("CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON checkpoint BEGIN SELECT RAISE(ABORT,'disk failure fixture'); END")
        with self.assertRaises(QueueError):
            cp.commit_batch({'position': 2}, [event()])
        self.assertEqual(self.store.list_inbox(), ())
        self.assertEqual(SQLiteCheckpointStore(self.store).load(), {'position': 1})

    def test_inbox_claim_release_ack_and_stale_ack_protection(self):
        cp = SQLiteCheckpointStore(self.store)
        cp.load()
        cp.commit_batch({}, [event()])
        with patch('fnceitaa.queue_store.time.time', return_value=1000):
            old = self.store.claim_inbox(lease_seconds=1)
            self.assertIsNone(self.store.claim_inbox())
        with patch('fnceitaa.queue_store.time.time', return_value=1002):
            self.assertEqual(self.store.recover_expired()['inbox_pending'], 1)
        new = self.store.claim_inbox()
        with self.assertRaises(QueueConflictError):
            self.store.finish_inbox(old)
        self.store.finish_inbox(new, success=False)
        third = self.store.claim_inbox()
        self.assertEqual(third.attempts, 3)
        self.store.finish_inbox(third)
        self.assertEqual(self.store.stats()['inbox'], {'done': 1})

    def test_queue_rejects_other_owner_or_arbitrary_database(self):
        with self.assertRaises(QueueError):
            SQLiteQueueStore(self.path, [999])
        other = Path(self.directory.name, 'other.db')
        with closing(sqlite3.connect(other)) as db:
            db.execute('CREATE TABLE unrelated (id INTEGER)')
        with self.assertRaises(QueueError):
            SQLiteQueueStore(other, OWNER)
        with closing(sqlite3.connect(other)) as db:
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='unrelated'").fetchone())

    def test_queue_rejects_future_versions(self):
        self.store._db.execute('PRAGMA user_version=999')
        with self.assertRaises(QueueError):
            SQLiteQueueStore(self.path, OWNER)

    def test_payload_codec_is_lossless_for_media_bytes_and_reserved_remote_keys(self):
        value = event()
        self.assertEqual(decode_payload(encode_payload(value)), value)
        tricky = {'model': 'AccountPeer', 'fields': {'id': 9}, 'binary': 'not base64', 'map': [1]}
        self.assertEqual(decode_payload(encode_payload(tricky)), tricky)
        self.assertEqual(decode_payload(encode_payload({'latitude': 1.25})), {'latitude': 1.25})
        for value in ('bad JSON', '{"model":"os.system","fields":{}}'):
            with self.assertRaises(QueueError):
                decode_payload(value)

    def test_database_and_sidecars_reject_symbolic_links(self):
        alias=Path(self.directory.name,'alias.queue.sqlite3')
        try:
            alias.symlink_to(self.path)
        except OSError:
            self.skipTest('Symbolic-link creation is unavailable for this Windows user')
        with self.assertRaises(QueueError):
            SQLiteQueueStore(alias,OWNER)

    def test_payload_and_store_guards_fail_before_advancing_checkpoint(self):
        cp=SQLiteCheckpointStore(self.store)
        with self.assertRaises(QueueError):
            cp.save({})
        cp.load()
        with self.assertRaises(QueueError):
            cp.commit_batch({},[replace(event(),event_id='invalid')])
        self.assertIsNone(cp.load())
        with self.assertRaises(QueueError):
            encode_payload({'unserializable':object()})

    def test_validation_blocks_unknown_operations_bad_keys_and_limits(self):
        for operation, key in [('arbitrary_rpc', None), ('send_message', ''), ('send_message', 'x'*257)]:
            with self.assertRaises(ValueError):
                self.store.enqueue(operation, request(), key=key)
        for limit in (True, 0, 10001):
            with self.assertRaises(ValueError):
                self.store.list_jobs(limit=limit)
        with self.assertRaises(QueueError):
            self.store.get_job('missing')


class QueueClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AccountReadTests.asyncSetUp(self)
        self.queue = self.client.queue()
        self.addCleanup(self.queue.close)
        self.peer = fixtures.entity_peer(USER)

    async def test_enqueue_does_not_send_and_survives_reopen_with_same_id(self):
        first = await self.queue.enqueue_message(self.peer, 'private body', key='reply:one', reply_to_msg_id=5)
        repeated = await self.queue.enqueue_message(self.peer, 'private body', key='reply:one', reply_to_msg_id=5)
        self.assertEqual(first.id, repeated.id)
        self.invoke.assert_not_awaited()
        self.queue.close()
        with self.client.queue() as reopened:
            stored = reopened.store.get_job(first.id)
            self.assertEqual(stored.request['random_id'], first.request['random_id'])
            self.assertEqual(stored.request['reply_to_msg_id'], 5)
        self.assertNotIn(b'fixture-token', self.queue.store.path.read_bytes())
        self.assertNotIn(b'fixture-device', self.queue.store.path.read_bytes())

    async def test_dispatch_exactly_one_persisted_random_id_and_no_repeat_after_confirm(self):
        job = await self.queue.enqueue_message(self.peer, 'hello')
        self.invoke.return_value = rpc(short_sent())
        done = await self.queue.dispatch_one(job.id)
        self.assertEqual(done.state, 'confirmed')
        self.assertEqual(done.receipt, {'message_ids': [100]})
        self.assertEqual(self.invoke.call_args.args[1]['random_id'], job.request['random_id'])
        self.assertIsNone(await self.queue.dispatch_one(job.id))
        self.invoke.assert_awaited_once()

    async def test_transport_or_decode_failure_stays_uncertain_across_reopen(self):
        job = await self.queue.enqueue_message(self.peer, 'hello')
        self.invoke.side_effect = AccountTransportError('timeout')
        result = await self.queue.dispatch_one(job.id)
        self.assertEqual((result.state, result.error), ('uncertain', 'SendMessageUncertainError'))
        self.queue.close()
        with self.client.queue() as reopened:
            self.assertIsNone(await reopened.dispatch_one(job.id))
            with self.assertRaises(QueueConflictError):
                reopened.store.retry_failed(job.id)
        self.invoke.assert_awaited_once()

    async def test_explicit_flood_failure_waits_and_needs_manual_retry(self):
        job = await self.queue.enqueue_message(self.peer, 'hello')
        self.invoke.side_effect = AccountRPCError(400, 'FLOOD_WAIT_30')
        result = await self.queue.dispatch_one(job.id)
        self.assertEqual(result.state, 'failed')
        self.assertEqual(result.error,'AccountRPCError:FLOOD_WAIT_30')
        with self.assertRaises(QueueConflictError):
            self.queue.store.retry_failed(job.id)
        self.assertIsNone(await self.queue.dispatch_one(job.id))
        self.invoke.assert_awaited_once()

    async def test_cancellation_marks_uncertain_and_preserves_asyncio_semantics(self):
        job = await self.queue.enqueue_message(self.peer, 'hello')
        self.invoke.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.queue.dispatch_one(job.id)
        self.assertEqual(self.queue.store.get_job(job.id).state, 'uncertain')

    async def test_commit_failure_after_server_ack_never_becomes_pending(self):
        job = await self.queue.enqueue_message(self.peer, 'hello')
        self.invoke.return_value = rpc(short_sent())
        with patch.object(self.queue.store, 'finish_job', side_effect=QueueError('disk full fixture')):
            with self.assertRaises(QueueError):
                await self.queue.dispatch_one(job.id, lease_seconds=1)
        self.assertEqual(self.queue.store.get_job(job.id).state, 'in_flight')
        with patch('fnceitaa.queue_store.time.time', return_value=10**12):
            self.queue.store.recover_expired()
        self.assertEqual(self.queue.store.get_job(job.id).state, 'uncertain')
        self.assertIsNone(await self.queue.dispatch_one(job.id))
        self.invoke.assert_awaited_once()

    async def test_queued_edit_delete_forward_and_copy_dispatch_saved_options(self):
        cases = [
            (await self.queue.enqueue_edit(self.peer, 10, 'changed'), 'edit_message', AccountMessage(10, self.peer, 1)),
            (await self.queue.enqueue_delete(self.peer, [10]), 'delete_messages', type('Receipt', (), {'message_ids': (10,)})()),
            (await self.queue.enqueue_forward(self.peer, self.peer, [10, 11]), 'forward_messages', (AccountMessage(100,self.peer,1), AccountMessage(101,self.peer,1))),
            (await self.queue.enqueue_forward(self.peer, self.peer, [10], copy=True), 'copy_message', AccountMessage(100,self.peer,1)),
        ]
        for job, method, receipt in cases:
            with patch.object(self.client, method, new_callable=AsyncMock, return_value=receipt) as send:
                result = await self.queue.dispatch_one(job.id)
                self.assertEqual(result.state, 'confirmed')
                send.assert_awaited_once()
                if method == 'forward_messages':
                    self.assertEqual(tuple(send.call_args.kwargs['random_ids']), tuple(job.request['random_ids']))
                if method == 'copy_message':
                    self.assertEqual(send.call_args.kwargs['random_id'], job.request['random_ids'][0])
        self.invoke.assert_not_awaited()

    async def test_invalid_jobs_fail_before_enqueue(self):
        for call in (lambda: self.queue.enqueue_message(self.peer, ''),
                     lambda: self.queue.enqueue_message(self.peer, 'x', silent=1),
                     lambda: self.queue.enqueue_message(self.peer, 'x', reply_to_msg_id=0),
                     lambda: self.queue.enqueue_delete(fixtures.entity_peer(fixtures.CHANNEL), [10]),
                     lambda: self.queue.enqueue_forward(self.peer, self.peer, [10,11], copy=True)):
            with self.assertRaises(ValueError):
                await call()
        self.assertEqual(self.queue.store.list_jobs(), ())
        self.invoke.assert_not_awaited()

    async def test_media_keeps_path_hash_caption_and_persisted_random_id(self):
        file = Path(self.directory.name, 'fixture.txt')
        file.write_bytes(b'abc')
        job = await self.queue.enqueue_file(self.peer, file, caption='caption')
        self.assertEqual(job.request['file']['size'], 3)
        self.assertEqual(job.request['options']['caption'], 'caption')
        with patch.object(self.client, 'send_file', new_callable=AsyncMock, return_value=AccountMessage(100,self.peer,1)) as send:
            result = await self.queue.dispatch_one(job.id)
            self.assertEqual(result.state, 'confirmed')
            self.assertEqual(send.call_args.kwargs['random_id'], job.request['random_id'])
        self.invoke.assert_not_awaited()

    async def test_changed_or_missing_media_is_failed_before_upload(self):
        file = Path(self.directory.name, 'fixture.txt')
        file.write_bytes(b'abc')
        job = await self.queue.enqueue_file(self.peer, file)
        file.write_bytes(b'changed')
        with patch.object(self.client, 'send_file', new_callable=AsyncMock) as send:
            result = await self.queue.dispatch_one(job.id)
            self.assertEqual(result.state, 'failed')
            send.assert_not_awaited()
        file.unlink()
        self.queue.store.retry_failed(job.id)
        self.assertEqual((await self.queue.dispatch_one(job.id)).state, 'failed')

    async def test_media_upload_cancellation_and_interruption_are_failed_before_final_send(self):
        file = Path(self.directory.name, 'fixture.txt')
        file.write_bytes(b'abc')
        for exception in (UploadInterruptedError(777,0), UploadCancelledError(777,0)):
            job = await self.queue.enqueue_file(self.peer, file)
            with patch.object(self.client,'send_file',new_callable=AsyncMock, side_effect=exception):
                if isinstance(exception, asyncio.CancelledError):
                    with self.assertRaises(UploadCancelledError):
                        await self.queue.dispatch_one(job.id)
                else:
                    await self.queue.dispatch_one(job.id)
            self.assertEqual(self.queue.store.get_job(job.id).state, 'failed')

    async def test_album_order_and_single_final_correlation_survive_queue(self):
        files=[]
        for name in ('one','two'):
            path=Path(self.directory.name,name+'.txt')
            path.write_text(name)
            files.append(path)
        job = await self.queue.enqueue_album(self.peer,[MediaItem(files[0],kind=MediaKind.DOCUMENT,caption='first'),MediaItem(files[1],kind=MediaKind.DOCUMENT)])
        with patch.object(self.client,'send_album',new_callable=AsyncMock,return_value=(AccountMessage(100,self.peer,1),AccountMessage(101,self.peer,1))) as send:
            result=await self.queue.dispatch_one(job.id)
            self.assertEqual(result.receipt,{'message_ids':[100,101]})
            self.assertEqual([item.caption for item in send.call_args.args[1]],['first',''])
            self.assertEqual(send.call_args.kwargs['random_ids'],job.request['random_ids'])

    async def test_media_rejects_ephemeral_bytes_and_bad_album_before_enqueue(self):
        for call in (lambda:self.queue.enqueue_file(self.peer,b'abc'),lambda:self.queue.enqueue_album(self.peer,[])):
            with self.assertRaises(ValueError):
                await call()
        self.assertEqual(self.queue.store.list_jobs(),())

    async def test_queue_rejects_switched_account_and_session_paths(self):
        with self.assertRaises(ValueError):
            self.client.queue(self.client._store.path)
        self.client._session=replace(self.client._current_session(),user_id=999)
        for call in (lambda:self.queue.enqueue_message(self.peer,'x'),lambda:self.queue.dispatch_one(),lambda:self.queue.poll_once()):
            with self.assertRaises(QueueError):
                await call()

    async def test_poll_commits_event_and_cursor_before_returning(self):
        self.invoke.side_effect=[rpc(state()),rpc(difference(messages=[message()]))]
        batch=await self.queue.poll_once()
        self.assertEqual(len(batch.events),1)
        self.assertEqual(self.queue.store.stats()['inbox'],{'pending':1})
        self.queue.close()
        with self.client.queue() as reopened:
            self.assertEqual(reopened.updates.store.load()['state']['pts'],11)
            self.assertEqual(reopened.store.claim_inbox().event.event_id,batch.events[0].event_id)

    async def test_poll_commit_failure_keeps_pending_batch_and_no_cursor_loss(self):
        self.invoke.return_value=rpc(state())
        await self.queue.updates.initialize()
        self.invoke.return_value=rpc(difference(messages=[message()]))
        self.queue.store._db.execute("CREATE TRIGGER fail_cursor BEFORE UPDATE ON checkpoint BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(QueueError):
            await self.queue.poll_once()
        self.assertEqual(self.queue.store.list_inbox(),())
        self.assertEqual(SQLiteCheckpointStore(self.queue.store).load()['state']['pts'],10)
        self.queue.store._db.execute('DROP TRIGGER fail_cursor')
        calls=self.invoke.await_count
        await self.queue.poll_once()
        self.assertEqual(self.invoke.await_count,calls)
        self.assertEqual(len(self.queue.store.list_inbox()),1)

    async def test_new_edit_delete_events_and_raw_bytes_survive_restart(self):
        edit=dict(_='updateEditMessage',message={**message(),'message':'edit'},pts=12,pts_count=1)
        self.invoke.side_effect=[rpc(state()),rpc(difference(13,messages=[message()],updates=[edit,deletion(10,13)]))]
        await self.queue.poll_once()
        self.assertEqual(sorted(i.event.kind for i in self.queue.store.list_inbox()),['delete','edit','new'])

    async def test_explicit_import_preserves_legacy_file_and_refuses_existing_cursor(self):
        path=Path(self.directory.name,'old.updates')
        value=dict(version=1,owner=[123,'https://hasan.eitaa.ir/eitaa/',2496],state={'pts':10,'qts':0,'date':100,'seq':2},channels={},seen=[],known={})
        store=UpdateCheckpointStore(path)
        store.load()
        store.save(value)
        before=path.read_bytes()
        await self.queue.import_checkpoint(path)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.queue.updates.store.load(),value)
        with self.assertRaises(QueueError):
            await self.queue.import_checkpoint(path)
        self.invoke.assert_not_awaited()

    async def test_inbox_handler_reply_key_prevents_duplicate_enqueue_after_crash(self):
        cp=self.queue.updates.store
        cp.load()
        cp.commit_batch({},[event()])
        first=self.queue.store.claim_inbox()
        sent=await self.queue.enqueue_message(self.peer,'reply',key=first.event.event_id)
        # Handler failed after enqueue, before marking done.
        self.queue.store.finish_inbox(first,success=False)
        replay=self.queue.store.claim_inbox()
        again=await self.queue.enqueue_message(self.peer,'reply',key=replay.event.event_id)
        self.assertEqual(sent.id,again.id)
        self.queue.store.finish_inbox(replay)
        self.assertEqual(len(self.queue.store.list_jobs()),1)

    async def test_queue_checkpoint_conflicts_do_not_lose_a_pending_event(self):
        self.invoke.return_value=rpc(state())
        await self.queue.updates.initialize()
        second=self.client.queue()
        self.addCleanup(second.close)
        await second.updates.initialize()
        self.invoke.return_value=rpc(difference(messages=[message()]))
        await self.queue.poll_once()
        with self.assertRaises(QueueConflictError):
            await second.poll_once()
        self.assertEqual(len(self.queue.store.list_inbox()),1)

    async def test_claim_failure_prevents_any_network_send(self):
        job=await self.queue.enqueue_message(self.peer,'hello')
        with patch.object(self.queue.store,'claim_job',side_effect=QueueError('fixture disk error')):
            with self.assertRaises(QueueError):
                await self.queue.dispatch_one(job.id)
        self.invoke.assert_not_awaited()
        self.assertEqual(self.queue.store.get_job(job.id).state,'pending')


class QueueWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_binary_http_send_persists_receipt_and_inbox_without_credentials(self):
        from test_account_transport import response_for
        from fnceitaa.protocol import TLCodec
        codec=TLCodec()
        values=[short_sent(),state(),difference(messages=[message()])]
        packets=[codec.encode([SELF],'Vector<User>')]+[codec.encode(v) for v in values]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory,'fixture.session')
            store=SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token','fixture-device',123,'https://hasan.eitaa.ir/eitaa/',2496))
            with patch('fnceitaa.account.requests.post',side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    with client.queue() as queue:
                        job=await queue.enqueue_message(fixtures.entity_peer(USER),'hello',reply_to_msg_id=5)
                        self.assertEqual(post.call_count,1)
                        done=await queue.dispatch_one(job.id)
                        self.assertEqual(done.receipt,{'message_ids':[100]})
                        payload=codec.encode_method('messages.sendMessage',dict(peer={'_':'inputPeerUser','user_id':42,'access_hash':-987},message='hello',random_id=job.request['random_id'],silent=False,no_webpage=False,reply_to_msg_id=5))
                        self.assertIn(payload,post.call_args.kwargs['data'])
                        await queue.poll_once()
                        db_path=queue.store.path
                    with client.queue() as restarted:
                        self.assertIsNone(await restarted.dispatch_one(job.id))
                        self.assertEqual(len(restarted.store.list_inbox()),1)
                self.assertEqual(post.call_count,4)
                self.assertNotIn(b'fixture-token',db_path.read_bytes())


class QueueCrashTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name,'crash.queue.sqlite3')

    def child(self,code):
        result=subprocess.run([sys.executable,'-c',code,str(self.path)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,23,result.stderr)

    def test_process_death_after_claim_leaves_uncertain_not_sendable(self):
        with SQLiteQueueStore(self.path,OWNER) as store:
            job=store.enqueue('send_message',request())
        self.child("import sys,os; from fnceitaa.queue_store import SQLiteQueueStore; s=SQLiteQueueStore(sys.argv[1],"+repr(OWNER)+"); s.claim_job(lease_seconds=1); os._exit(23)")
        with SQLiteQueueStore(self.path,OWNER) as store:
            self.assertEqual(store.get_job(job.id).state,'in_flight')
            with patch('fnceitaa.queue_store.time.time',return_value=10**12):
                store.recover_expired()
            self.assertEqual(store.get_job(job.id).state,'uncertain')
            self.assertIsNone(store.claim_job())

    def test_process_death_inside_enqueue_rolls_back_entire_job(self):
        self.child("import sys,os; from fnceitaa.queue_store import SQLiteQueueStore; s=SQLiteQueueStore(sys.argv[1],"+repr(OWNER)+"); s._db.execute('BEGIN IMMEDIATE'); s._db.execute(\"INSERT INTO outbox(id,operation,payload,fingerprint,state,created) VALUES('crash','send_message','{}','x','pending',0)\"); os._exit(23)")
        with SQLiteQueueStore(self.path,OWNER) as store:
            self.assertEqual(store.list_jobs(),())

    def test_process_death_between_inbox_insert_and_cursor_commit_is_atomic(self):
        with SQLiteQueueStore(self.path,OWNER) as store:
            cp=SQLiteCheckpointStore(store)
            cp.load()
            cp.save({'position':1})
        self.child("import sys,os; from fnceitaa.queue_store import SQLiteQueueStore,SQLiteCheckpointStore; from fnceitaa import AccountEvent; s=SQLiteQueueStore(sys.argv[1],"+repr(OWNER)+"); c=SQLiteCheckpointStore(s); c.load(); s._db.set_trace_callback(lambda sql: os._exit(23) if sql.startswith('INSERT INTO checkpoint') else None); c.commit_batch({'position':2},[AccountEvent('raw','a'*64)])")
        with SQLiteQueueStore(self.path,OWNER) as store:
            self.assertEqual(store.list_inbox(),())
            self.assertEqual(SQLiteCheckpointStore(store).load(),{'position':1})


class QueueExampleTests(unittest.TestCase):
    def test_all_offline_commands_never_open_session_database_or_prompt(self):
        from examples.account_queue import main
        for command in ('enqueue','status','send','poll','inbox','ack','recover','retry'):
            with patch('sys.argv',['account_queue',command]),patch('builtins.print'),patch('examples.account_queue.AccountClient') as client,patch('sqlite3.connect') as db,patch('builtins.input') as prompt:
                main()
                client.assert_not_called()
                db.assert_not_called()
                prompt.assert_not_called()

    def test_send_requires_an_explicit_job_id_before_opening_session(self):
        from examples.account_queue import main
        with patch('sys.argv',['account_queue','send','--live']),patch('sys.stderr'),patch('examples.account_queue.AccountClient') as client:
            with self.assertRaises(SystemExit):
                main()
            client.assert_not_called()


class QueueExampleLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_job_dispatch_requires_confirmation(self):
        from examples.account_queue import run
        from unittest.mock import MagicMock
        args=argparse.Namespace(session='fixture',load_dialogs=False,database=None,command='send',job_id='fixed')
        for answer in ('no','yes'):
            with patch('examples.account_queue.AccountClient') as factory,patch('builtins.input',return_value=answer),patch('builtins.print') as output:
                client=factory.return_value.__aenter__.return_value
                client.queue=MagicMock()
                queue=client.queue.return_value.__enter__.return_value
                queue.store.get_job.return_value=OutboxJob('fixed','send_message','pending',request())
                queue.dispatch_one=AsyncMock(return_value=OutboxJob('fixed','send_message','confirmed',request(),receipt={'message_ids':[100]}))
                await run(args)
                self.assertEqual(queue.dispatch_one.await_count,1 if answer=='yes' else 0)
                if answer=='yes':
                    queue.dispatch_one.assert_awaited_once_with('fixed')
                self.assertNotIn('access_hash',str(output.call_args_list))

    async def test_enqueue_requires_confirmation_and_does_not_dispatch(self):
        from examples.account_queue import run
        peer=fixtures.entity_peer(USER)
        args=argparse.Namespace(session='fixture',load_dialogs=False,database=None,command='enqueue',peer='self',key='test')
        for answer in ('no','yes'):
            with patch('examples.account_queue.AccountClient') as factory,patch('builtins.input',side_effect=['private text',answer]),patch('builtins.print'):
                client=factory.return_value.__aenter__.return_value
                client.resolve_peer=AsyncMock(return_value=peer)
                # queue() is synchronous, even though the client is async.
                from unittest.mock import MagicMock
                client.queue=MagicMock()
                queue=client.queue.return_value.__enter__.return_value
                queue.enqueue_message=AsyncMock(return_value=OutboxJob('fixture-job','send_message','pending',request()))
                await run(args)
                self.assertEqual(queue.enqueue_message.await_count,1 if answer=='yes' else 0)
                queue.dispatch_one.assert_not_called()


if __name__=='__main__':
    unittest.main()
