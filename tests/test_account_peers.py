from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fnceitaa import (AccountClient, AccountPeer, DialogCursor, PeerKind,
                      PeerResolveError, AuthenticationError, SessionRevokedError)
from fnceitaa.account import AccountHTTPTransport, AccountReply
from fnceitaa.account_peers import entity_peer, peer_key
from fnceitaa.errors import AccountStateError, AccountRPCError
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore


SELF = {'_': 'user', 'id': 123, 'pFlags': {'self': True}, 'first_name': 'Fixture'}
USER = {'_': 'user', 'id': 42, 'access_hash': -987, 'first_name': 'User'}
CHANNEL = {'_': 'channel', 'id': 42, 'access_hash': 654, 'title': 'Channel',
           'photo': {'_': 'chatPhotoEmpty'}, 'date': 1, 'pFlags': {'megagroup': True}}


def rpc(value, control=None):
    return AccountReply(123, value, control=control)


def reference(kind, identifier):
    return {'_': {'user': 'peerUser', 'chat': 'peerChat', 'channel': 'peerChannel'}[kind],
            kind + '_id': identifier}


def dialog(kind='user', identifier=42, top=10, pinned=False):
    return {'_': 'dialog', 'peer': reference(kind, identifier), 'top_message': top,
            'read_inbox_max_id': 0, 'read_outbox_max_id': 0, 'unread_count': 2,
            'unread_mentions_count': 0, 'notify_settings': {'_': 'peerNotifySettings'},
            'pinned': pinned}


def page(dialogs=None, *, sliced=False, messages=None, users=None, chats=None):
    return {'_': 'messages.dialogsSlice' if sliced else 'messages.dialogs',
            **({'count': 20} if sliced else {}),
            'dialogs': [dialog()] if dialogs is None else dialogs,
            'messages': messages or [], 'users': [USER] if users is None else users,
            'chats': chats or []}


def message(kind='user', identifier=42, mid=10, date=100):
    return {'_': 'message', 'id': mid, 'peer_id': reference(kind, identifier),
            'date': date, 'message': 'Fixture text'}


class AccountPeerTests(unittest.TestCase):
    def test_input_constructors_hashes_and_type_identity(self):
        user = entity_peer(USER)
        channel = entity_peer(CHANNEL)
        self.assertNotEqual(user.key, channel.key)
        self.assertEqual(user.to_input_peer(), {'_': 'inputPeerUser', 'user_id': 42, 'access_hash': -987})
        self.assertEqual(channel.to_input_peer()['_'], 'inputPeerChannel')
        self.assertTrue(channel.is_megagroup)
        self.assertEqual(AccountPeer(5, PeerKind.CHAT).to_input_peer(), {'_': 'inputPeerChat', 'chat_id': 5})
        self.assertEqual(AccountPeer(123, is_self=True).to_input_peer(), {'_': 'inputPeerSelf'})
        self.assertNotIn('-987', repr(user))
        self.assertIsNone(entity_peer({'_': 'userEmpty', 'id': 1}))

    def test_missing_hash_and_min_are_not_guessed_as_zero(self):
        for peer in (AccountPeer(42), AccountPeer(42, PeerKind.CHANNEL),
                     AccountPeer(42, access_hash=7, is_min=True)):
            with self.assertRaises(PeerResolveError):
                peer.to_input_peer()
        self.assertEqual(AccountPeer(42, access_hash=0).to_input_peer()['access_hash'], 0)

    def test_bad_ids_hashes_and_references(self):
        for identifier in (True, 0, -1, 2**63, '12'):
            with self.assertRaises(ValueError):
                AccountPeer(identifier)
        for value in (True, 2**63, '12'):
            with self.assertRaises(ValueError):
                AccountPeer(1, access_hash=value)
        for value in (None, {}, {'_': 'peerUser', 'user_id': True}):
            with self.assertRaises(TLCodecError):
                peer_key(value)
        with self.assertRaises(ValueError):
            DialogCursor(1, 0, None)


class AccountReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name, 'account.session')
        store = SessionStore(self.path)
        store.load()
        store.save(AccountSession('fixture-token', 'fixture-device', 123,
                                  'https://hasan.eitaa.ir/eitaa/', 2496))
        self.patcher = patch.object(AccountHTTPTransport, 'invoke', return_value=rpc([SELF]))
        self.invoke = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.client = await AccountClient(self.path).connect()
        self.invoke.reset_mock()
        self.addAsyncCleanup(self.client.disconnect)

    async def test_resolve_username_uses_real_rpc_and_matching_entity(self):
        self.invoke.return_value = rpc({'_': 'contacts.resolvedPeer',
                                       'peer': reference('channel', 42),
                                       'users': [USER], 'chats': [CHANNEL]})
        peer = await self.client.resolve_peer('@Example')
        self.assertEqual(peer.kind, PeerKind.CHANNEL)
        self.invoke.assert_awaited_once_with('contacts.resolveUsername', {'username': 'example'})
        self.assertEqual((await self.client.resolve_peer('channel:42')).access_hash, 654)
        self.assertEqual((await self.client.resolve_peer('user:42')).access_hash, -987)
        with self.assertRaises(PeerResolveError):
            await self.client.resolve_peer(42)
        self.assertEqual(self.invoke.await_count, 1)

    async def test_self_explicit_group_and_validation_need_no_rpc(self):
        self.assertTrue((await self.client.resolve_peer('me')).is_self)
        self.assertTrue((await self.client.resolve_peer(123)).is_self)
        self.assertEqual((await self.client.resolve_peer('chat:50')).kind, PeerKind.CHAT)
        with self.assertRaises(PeerResolveError):
            await self.client.resolve_peer(AccountPeer(999, is_self=True))
        for value in (True, -1, '', '@@bad', 'https://eitaa.com/foo', '123', 'user:9999999999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.client.resolve_peer(value)
        for value in (999, 'user:999', 'channel:999'):
            with self.assertRaises(PeerResolveError):
                await self.client.resolve_peer(value)
        self.invoke.assert_not_awaited()

    async def test_min_entity_does_not_replace_full_hash(self):
        self.client._cache_entities([USER])
        self.client._cache_entities([{**USER, 'access_hash': 1, 'pFlags': {'min': True}}])
        self.assertEqual((await self.client.resolve_peer(42)).access_hash, -987)

    async def test_own_numeric_id_collision_requires_explicit_kind(self):
        self.client._cache_entities([{**CHANNEL, 'id': 123}])
        with self.assertRaises(PeerResolveError):
            await self.client.resolve_peer(123)
        self.assertTrue((await self.client.resolve_peer('user:123')).is_self)
        self.assertEqual((await self.client.resolve_peer('channel:123')).kind, PeerKind.CHANNEL)

    async def test_username_response_must_include_its_identity_even_if_previously_cached(self):
        self.client._cache_entities([USER])
        self.invoke.return_value = rpc({'_': 'contacts.resolvedPeer', 'peer': reference('user', 42),
                                       'users': [], 'chats': []})
        with self.assertRaisesRegex(TLCodecError, 'omitted'):
            await self.client.resolve_peer('@example')

    async def test_cache_is_bounded_and_discarded_on_disconnect(self):
        self.client._cache_entities([{'_': 'user', 'id': i, 'access_hash': i} for i in range(1, 10003)])
        self.assertEqual(len(self.client._peers), 10000)
        self.assertNotIn((PeerKind.USER, 1), self.client._peers)
        await self.client.disconnect()
        self.assertEqual(len(self.client._peers), 0)

    async def test_dialog_page_matches_top_message_by_peer_and_id(self):
        self.invoke.return_value = rpc(page(sliced=True, messages=[message('channel', 42, date=999), message()]))
        result = await self.client.get_dialogs_page(limit=5, folder_id=0)
        self.assertEqual(result.next_cursor.offset_date, 100)
        self.assertEqual(result.total_count, 20)
        self.assertEqual(result.dialogs[0].unread_count, 2)
        self.invoke.assert_awaited_once_with('messages.getDialogs', {
            'offset_date': 0, 'offset_id': 0, 'offset_peer': {'_': 'inputPeerEmpty'},
            'limit': 5, 'hash': 0, 'folder_id': 0})
        self.invoke.return_value = rpc(page(dialogs=[]))
        next_page = await self.client.get_dialogs_page(limit=5, folder_id=0, cursor=result.next_cursor)
        self.assertIsNone(next_page.next_cursor)
        params = self.invoke.call_args.args[1]
        self.assertTrue(params['exclude_pinned'])
        self.assertEqual(params['offset_peer']['access_hash'], -987)
        self.assertEqual(params['offset_id'], 10)
        self.assertEqual(params['offset_date'], 100)

    async def test_pinned_only_first_page_continues_unpinned_stream(self):
        # Test decoded pFlags, which are distinct from encoder input booleans.
        item = dialog()
        item['pFlags'] = {'pinned': True}
        self.invoke.side_effect = [rpc(page([item], sliced=True)), rpc(page([dialog(top=11)]))]
        dialogs = await self.client.get_dialogs(limit=5)
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(dialogs[0].pinned)
        self.assertEqual(self.invoke.await_count, 2)
        params = self.invoke.call_args.args[1]
        self.assertTrue(params['exclude_pinned'])
        self.assertEqual(params['offset_peer'], {'_': 'inputPeerEmpty'})

    async def test_iteration_deduplicates_and_is_lazy(self):
        self.invoke.side_effect = [rpc(page(sliced=True, messages=[message()])),
                                   rpc(page([dialog(), dialog(identifier=43)], users=[USER, {**USER, 'id': 43}]))]
        iterator = self.client.iter_dialogs(limit=2, page_size=1)
        first = await anext(iterator)
        self.assertEqual(self.invoke.await_count, 1)
        second = await anext(iterator)
        self.assertNotEqual(first.peer.key, second.peer.key)
        self.assertEqual(self.invoke.await_count, 2)
        with self.assertRaises(StopAsyncIteration):
            await anext(iterator)

    async def test_folder_summaries_are_not_returned_as_dialogs(self):
        summary = {'_': 'dialogFolder'}
        self.invoke.return_value = rpc(page([summary, dialog()]))
        result = await self.client.get_dialogs_page()
        self.assertEqual(len(result.dialogs), 1)

    async def test_empty_slice_ends_without_retry(self):
        self.invoke.return_value = rpc(page([], sliced=True))
        self.assertEqual(await self.client.get_dialogs(), [])
        self.invoke.assert_awaited_once()

    async def test_bad_pagination_stops_instead_of_skipping_dialogs(self):
        self.invoke.return_value = rpc(page(sliced=True))
        with self.assertRaisesRegex(TLCodecError, 'matching top message'):
            await self.client.get_dialogs_page()
        cursor = DialogCursor(100, 10, entity_peer(USER))
        self.invoke.return_value = rpc(page(sliced=True, messages=[message()]))
        with self.assertRaisesRegex(TLCodecError, 'did not advance'):
            await self.client.get_dialogs_page(cursor=cursor)

    async def test_unexpected_notmodified_is_not_treated_as_empty_list(self):
        self.invoke.return_value = rpc({'_': 'messages.dialogsNotModified', 'count': 11})
        with self.assertRaises(TLCodecError):
            await self.client.get_dialogs_page()

    async def test_invalid_pagination_arguments_do_not_send(self):
        for kwargs in ({'limit': True}, {'limit': 0}, {'limit': 101}, {'folder_id': True},
                       {'folder_id': 2}, {'cursor': {}},
                       {'cursor': DialogCursor(1, 1, entity_peer(USER), 1)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.client.get_dialogs_page(**kwargs)
        self.assertEqual(await self.client.get_dialogs(limit=0), [])
        with self.assertRaises(ValueError):
            await self.client.get_dialogs(limit=-1)
        self.invoke.assert_not_awaited()

    async def test_read_renews_token_once_and_revocation_stops_authorization(self):
        self.invoke.side_effect = [AccountStateError('eitaa_updates_expire_token'),
                                   rpc({'_': 'eitaa_updates_token', 'token': 'replacement'}, 'eitaa_updates_token'),
                                   rpc(page())]
        result = await self.client.get_dialogs_page()
        self.assertEqual(len(result.dialogs), 1)
        self.assertEqual(self.invoke.await_count, 3)
        self.assertEqual(SessionStore(self.path).load().token, 'replacement')
        self.invoke.side_effect = AccountRPCError(401, 'INVALID_LOGIN')
        with self.assertRaises(SessionRevokedError):
            await self.client.get_dialogs_page()
        self.assertFalse(self.client.is_authorized)

    async def test_flood_wait_propagates_without_read_retry(self):
        self.invoke.side_effect = AccountRPCError(400, 'FLOOD_WAIT_20')
        with self.assertRaises(AccountRPCError):
            await self.client.get_dialogs_page()
        self.invoke.assert_awaited_once()

    async def test_missing_session_cannot_read_or_request_code_implicitly(self):
        async with AccountClient(Path(self.directory.name, 'missing.session'), auto_login=False) as client:
            with self.assertRaises(AuthenticationError):
                await client.resolve_peer('@example')
            with self.assertRaises(AuthenticationError):
                await client.get_dialogs_page()
        self.invoke.assert_not_awaited()


class AccountReadWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_dialogs_and_resolution_cross_binary_transport(self):
        from test_account_transport import response_for
        codec = TLCodec()
        first = page(sliced=True, messages=[message()])
        resolved = {'_': 'contacts.resolvedPeer', 'peer': reference('channel', 42),
                    'chats': [CHANNEL], 'users': []}
        packets = [codec.encode([SELF], 'Vector<User>'), codec.encode(first), codec.encode(resolved)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            store = SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123,
                                      'https://hasan.eitaa.ir/eitaa/', 2496))
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    result = await client.get_dialogs_page()
                    self.assertEqual(result.next_cursor.offset_date, 100)
                    peer = await client.resolve_peer('@example')
                    self.assertEqual(peer.to_input_peer()['access_hash'], 654)
                self.assertEqual(post.call_count, 3)


class DialogExampleTests(unittest.TestCase):
    def test_default_example_opens_no_session_and_sends_no_requests(self):
        from examples.account_dialogs import main
        with patch('sys.argv', ['account_dialogs']), patch('builtins.print'), \
                patch('examples.account_dialogs.AccountClient') as client, \
                patch('fnceitaa.account.requests.post') as post:
            main()
        client.assert_not_called()
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
