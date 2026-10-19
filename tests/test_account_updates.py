import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fnceitaa import AccountPeer, PeerKind, Router, SessionError, UpdateGapError
from fnceitaa.account_updates import UpdateCheckpointStore
from fnceitaa.errors import AccountRPCError, AccountTransportError
from fnceitaa.protocol import TLCodec
import test_account_peers as fixtures
from test_account_peers import USER, CHANNEL, message, rpc


def state(pts=10, seq=2):
    return dict(_='updates.state', pts=pts, qts=0, date=100, seq=seq, unread_count=0)


def difference(pts=11, *, messages=(), updates=(), sliced=False):
    return dict(_='updates.differenceSlice' if sliced else 'updates.difference',
                new_messages=list(messages), new_encrypted_messages=[],
                other_updates=list(updates), users=[USER], chats=[CHANNEL],
                **{'intermediate_state' if sliced else 'state': state(pts)})


def deletion(mid=10, pts=12, channel=False):
    return dict(_='updateDeleteChannelMessages' if channel else 'updateDeleteMessages',
                messages=[mid], pts=pts, pts_count=1, **({'channel_id': 42} if channel else {}))


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AccountReadTests.asyncSetUp(self)
        self.consumer = self.client.updates()
        self.invoke.return_value = rpc(state())
        await self.consumer.initialize()
        self.invoke.reset_mock()

    def reply(self, value):
        # Exercise the actual pinned TL encoder/decoder, not just mocked dicts.
        codec = TLCodec()
        self.invoke.return_value = rpc(codec.decode(codec.encode(value)))

    async def test_new_edit_delete_unknown_peer_and_pending_replay(self):
        edit = dict(_='updateEditMessage', message={**message(), 'message': 'edited'}, pts=12, pts_count=1)
        self.reply(difference(14, messages=[message()], updates=[edit, deletion(), deletion(99, 14)]))
        batch = await self.consumer.poll()
        self.assertEqual([event.kind for event in batch.events], ['new', 'edit', 'delete', 'delete'])
        self.assertEqual(batch.events[2].peer.id, 42)
        self.assertIsNone(batch.events[3].peer)
        self.assertNotIn('edited', repr(batch))
        self.assertIs(await self.consumer.poll(), batch)
        self.assertEqual(self.invoke.await_count, 1)
        self.assertEqual(json.loads(self.consumer.store.path.read_text())['state']['pts'], 10)
        await self.consumer.ack(batch)
        checkpoint = self.consumer.store.path.read_text()
        self.assertNotIn('edited', checkpoint)
        self.assertNotIn('fixture-token', checkpoint)
        self.assertEqual(json.loads(checkpoint)['state']['pts'], 14)
        with self.assertRaises(ValueError):
            await self.consumer.ack(batch)

    async def test_restart_without_ack_replays_same_identity(self):
        self.reply(difference(messages=[message()]))
        original = await self.consumer.poll()
        restarted = self.client.updates()
        await restarted.initialize()
        replayed = await restarted.poll()
        self.assertEqual(original.events[0].event_id, replayed.events[0].event_id)
        await restarted.ack(replayed)
        with self.assertRaises(SessionError):
            await self.consumer.ack(original)

    async def test_acknowledged_duplicates_across_restart_and_within_batch(self):
        update = dict(_='updateNewMessage', message=message(), pts=11, pts_count=1)
        self.reply(difference(messages=[message()], updates=[update]))
        batch = await self.consumer.poll()
        self.assertEqual(len(batch.events), 1)
        await self.consumer.ack(batch)
        restarted = self.client.updates()
        self.reply(difference(12, messages=[message()]))
        self.assertEqual((await restarted.poll()).events, ())

    async def test_two_edits_are_distinct_and_slices_continue(self):
        edits = [dict(_='updateEditMessage', message=message(), pts=p, pts_count=1) for p in (11, 12)]
        self.reply(difference(12, updates=edits, sliced=True))
        batch = await self.consumer.poll()
        self.assertTrue(batch.has_more)
        self.assertEqual(len(set(e.event_id for e in batch.events)), 2)
        await self.consumer.ack(batch)
        self.reply(difference(13))
        self.assertFalse((await self.consumer.poll()).has_more)
        self.assertEqual(self.invoke.call_args.args[1]['pts'], 12)
        self.assertEqual(self.invoke.call_args.args[1]['qts'], -1)

    async def test_too_long_and_bad_states_do_not_advance(self):
        for result in ({'_': 'updates.differenceTooLong', 'pts': 1000},
                       difference(9), difference(10, sliced=True)):
            self.reply(result)
            with self.assertRaises(UpdateGapError):
                await self.consumer.poll()
            self.assertEqual(json.loads(self.consumer.store.path.read_text())['state']['pts'], 10)

    async def test_channel_baseline_and_separate_pts_and_timeout(self):
        peer = AccountPeer(42, PeerKind.CHANNEL, 654)
        self.invoke.return_value = rpc(dict(_='messages.channelMessages', pts=20, users=[], chats=[], messages=[]))
        await self.consumer.watch_channel(peer)
        self.invoke.reset_mock()
        self.invoke.side_effect = [rpc(difference()), rpc(dict(_='updates.channelDifference',
            pts=22, pFlags={'final': True}, timeout=60, new_messages=[message('channel')],
            other_updates=[deletion(channel=True)], chats=[CHANNEL], users=[]))]
        batch = await self.consumer.poll()
        self.assertEqual([e.kind for e in batch.events], ['new', 'delete'])
        self.assertEqual(self.invoke.call_args.args[1]['pts'], 20)
        await self.consumer.ack(batch)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 11)
        self.assertEqual(self.consumer._checkpoint['channels']['42']['pts'], 22)
        self.invoke.side_effect = None
        self.reply(difference(12))
        self.invoke.reset_mock()
        await self.consumer.poll()
        self.assertEqual(self.invoke.await_count, 1)  # channel timeout respected

    async def test_existing_channel_baseline_is_never_reset(self):
        peer = AccountPeer(42, PeerKind.CHANNEL, 654)
        self.invoke.return_value = rpc(dict(_='messages.channelMessages', pts=20, users=[], chats=[]))
        await self.consumer.watch_channel(peer)
        self.invoke.reset_mock()
        await self.consumer.watch_channel(peer)
        self.invoke.assert_not_awaited()

    async def test_channel_failure_keeps_common_events_uncommitted(self):
        peer = AccountPeer(42, PeerKind.CHANNEL, 654)
        self.invoke.return_value = rpc(dict(_='messages.channelMessages', pts=20, users=[], chats=[]))
        await self.consumer.watch_channel(peer)
        self.invoke.side_effect = [rpc(difference(messages=[message()])),
                                  rpc({'_': 'updates.channelDifferenceTooLong'})]
        with self.assertRaises(UpdateGapError):
            await self.consumer.poll()
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 10)
        self.assertIsNone(self.consumer._pending)

    async def test_handler_failure_replays_and_stop_commits_success(self):
        self.reply(difference(messages=[message()]))
        router = Router()

        @router.on('new')
        def fail(event):
            raise RuntimeError('handler failed')

        with self.assertRaises(RuntimeError):
            await self.consumer.run_polling(router)
        batch = await self.consumer.poll()
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 10)
        good = Router()
        good.on('new')(lambda event: self.consumer.stop())
        await self.consumer.run_polling(good)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 11)
        self.assertIsNone(self.consumer._pending)

    async def test_failed_atomic_ack_can_be_retried_and_batch_cannot_change_cursor(self):
        self.reply(difference())
        batch = await self.consumer.poll()
        batch._checkpoint['state']['pts'] = 999
        with patch('fnceitaa.account_updates.os.replace', side_effect=OSError):
            with self.assertRaises(SessionError):
                await self.consumer.ack(batch)
        self.assertIs(await self.consumer.poll(), batch)
        self.assertEqual(len(list(self.consumer.store.path.parent.glob('.account.session.updates.*'))), 0)
        await self.consumer.ack(batch)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 11)

    async def test_bad_checkpoint_owner_or_structure_rejected(self):
        baseline = json.loads(self.consumer.store.path.read_text())
        for change in ({'owner': [999, 'different', 1]}, {'seen': ['bad']}, {'state': {'pts': None}},
                       {'channels': {'42': {'pts': -1, 'access_hash': 1}}}, {'known': {'1': ['channel', 42]}}):
            self.consumer.store.path.write_text(json.dumps({**baseline, **change}))
            with self.assertRaises(SessionError):
                await self.client.updates().initialize()

    async def test_cancelled_handler_never_acknowledges(self):
        self.reply(difference(messages=[message()]))
        router = Router()

        @router.on('new')
        async def cancel(event):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await self.consumer.run_polling(router)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 10)

    async def test_read_failures_retry_with_backoff_and_flood_wait(self):
        self.invoke.side_effect = [AccountTransportError('offline'), AccountRPCError(400, 'FLOOD_WAIT_20'),
                                  rpc(difference(messages=[message()]))]
        delays = []

        async def immediate(awaitable, timeout):
            awaitable.close()
            delays.append(timeout)
            raise asyncio.TimeoutError

        router = Router()
        router.on('new')(lambda e: self.consumer.stop())
        with patch('fnceitaa.account_updates.asyncio.wait_for', side_effect=immediate):
            await self.consumer.run_polling(router)
        self.assertEqual(delays[:2], [5, 20])
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 11)

    async def test_checkpoint_cannot_overwrite_session(self):
        with self.assertRaises(ValueError):
            self.client.updates(self.path)

    async def test_explicit_overlap_recovers_server_empty_at_saved_pts(self):
        original = self.consumer.store.path.read_bytes()

        async def eitaa(method, params):
            if params['pts'] == 10:
                return rpc(dict(_='updates.differenceEmpty', date=100, seq=2))
            self.assertEqual(params, dict(pts=9, date=100, qts=-1))
            return rpc(difference(11, messages=[message(mid=212)]))

        self.invoke.side_effect = eitaa
        empty = await self.consumer.poll()
        self.assertEqual(empty.events, ())
        await self.consumer.ack(empty)
        recovered = await self.consumer.poll(replay_pts=1)
        self.assertEqual(recovered.events[0].message.id, 212)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 10)
        self.assertEqual(self.consumer.store.path.read_bytes(), original)
        await self.consumer.ack(recovered)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 11)

    async def test_overlap_deduplicates_seen_events_and_runs_once_after_ack(self):
        self.reply(difference(messages=[message()]))
        first = await self.consumer.poll()
        await self.consumer.ack(first)
        calls = []

        async def eitaa(method, params):
            calls.append(params['pts'])
            if len(calls) == 1:
                return rpc(difference(12, messages=[message(), message(mid=212)]))
            self.consumer.stop()
            return rpc(difference(12))

        self.invoke.side_effect = eitaa
        received = []
        router = Router()
        router.on('new')(lambda event: received.append(event.message.id))

        async def immediate(awaitable, timeout):
            awaitable.close()
            raise asyncio.TimeoutError

        with patch('fnceitaa.account_updates.asyncio.wait_for', side_effect=immediate):
            await self.consumer.run_polling(router, replay_pts=1)
        self.assertEqual(received, [212])
        self.assertEqual(calls, [10, 12])

    async def test_overlap_does_not_allow_regressing_commit_or_bad_bounds(self):
        self.reply(difference(9))
        with self.assertRaises(UpdateGapError):
            await self.consumer.poll(replay_pts=1)
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 10)
        for invalid in (True, -1, 1001, None):
            with self.assertRaises(ValueError):
                await self.consumer.poll(replay_pts=invalid)

    async def test_resume_diagnostics_are_structural_and_do_not_reset_cursor(self):
        self.reply(difference(messages=[{**message(), 'message': 'PRIVATE BODY'}]))
        restarted = self.client.updates()
        with self.assertLogs('fnceitaa.account_updates', level='INFO') as logs:
            batch = await restarted.poll()
        output = '\n'.join(logs.output)
        self.assertIn('existing file loaded', output)
        self.assertIn('messages=1', output)
        self.assertIn('delivered_events=1', output)
        self.assertNotIn('PRIVATE BODY', output)
        self.assertNotIn('fixture-token', output)
        self.assertEqual(self.invoke.call_args.args[1]['pts'], 10)
        self.assertEqual(restarted._checkpoint['state']['pts'], 10)
        self.assertEqual(len(batch.events), 1)

    async def test_empty_difference_and_raw_updates(self):
        self.reply({'_': 'updates.differenceEmpty', 'date': 101, 'seq': 3})
        batch = await self.consumer.poll()
        self.assertEqual(batch.events, ())
        await self.consumer.ack(batch)
        self.assertEqual(self.consumer._checkpoint['state']['date'], 101)
        raw = {'_': 'updateChannel', 'channel_id': 42}
        value = difference(11, updates=[raw])
        value['state'] = {**state(11, 3), 'date': 101}
        self.reply(value)
        event = (await self.consumer.poll()).events[0]
        self.assertEqual((event.kind, event.raw_type, event.raw), ('raw', 'updateChannel', raw))

    async def test_cancelled_read_never_advances_checkpoint(self):
        self.invoke.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.consumer.poll()
        self.assertEqual(self.consumer._checkpoint['state']['pts'], 10)
        self.assertIsNone(self.consumer._pending)

    async def test_nonfinal_channel_slice_and_restart_resume_independently(self):
        peer = AccountPeer(42, PeerKind.CHANNEL, 654)
        self.invoke.return_value = rpc(dict(_='messages.channelMessages', pts=20, users=[], chats=[]))
        await self.consumer.watch_channel(peer)
        self.invoke.side_effect = [rpc(difference()), rpc(dict(_='updates.channelDifference',
            pts=21, pFlags={}, new_messages=[], other_updates=[], chats=[], users=[]))]
        batch = await self.consumer.poll()
        self.assertTrue(batch.has_more)
        await self.consumer.ack(batch)
        restarted = self.client.updates()
        self.invoke.side_effect = [rpc(difference(12)), rpc(dict(_='updates.channelDifferenceEmpty',
            pts=21, pFlags={'final': True}))]
        resumed = await restarted.poll()
        self.assertEqual(self.invoke.call_args.args[1]['pts'], 21)
        self.assertFalse(resumed.has_more)

    async def test_wrong_channel_payload_is_not_committed(self):
        self.invoke.return_value = rpc(dict(_='messages.channelMessages', pts=20, users=[], chats=[]))
        await self.consumer.watch_channel(AccountPeer(42, PeerKind.CHANNEL, 654))
        self.invoke.side_effect = [rpc(difference()), rpc(dict(_='updates.channelDifference',
            pts=21, pFlags={'final': True}, new_messages=[message('channel', identifier=99)],
            other_updates=[], chats=[], users=[]))]
        from fnceitaa.protocol import TLCodecError
        with self.assertRaises(TLCodecError):
            await self.consumer.poll()
        self.assertEqual(self.consumer._checkpoint['channels']['42']['pts'], 20)


class StoreTests(unittest.TestCase):
    def test_missing_corrupt_oversize_and_conflicting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'checkpoint')
            store = UpdateCheckpointStore(path)
            with self.assertRaises(SessionError):
                store.save({})
            self.assertIsNone(store.load())
            store.save({'state': 'fixture'})
            second = UpdateCheckpointStore(path)
            second.load()
            store.save({'state': 'next'})
            with self.assertRaises(SessionError):
                second.save({})
            path.write_bytes(b'{')
            with self.assertRaises(SessionError):
                store.load()
            with patch.object(store, 'MAX_BYTES', 10):
                path.write_bytes(b'x' * 11)
                with self.assertRaises(SessionError):
                    store.load()
            path.write_text('null')
            with self.assertRaises(SessionError):
                store.load()


if __name__ == '__main__':
    unittest.main()
