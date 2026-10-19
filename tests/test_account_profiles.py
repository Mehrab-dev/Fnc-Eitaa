import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fnceitaa import (AccountClient, AccountPeer, AccountUserFull, AccountChatFull, PeerKind,
                      PeerResolveError, SessionRevokedError, UnsupportedOperationError)
from fnceitaa.account import AccountHTTPTransport
from fnceitaa.account_profiles import parse_member
from fnceitaa.errors import AccountRPCError, AccountStateError
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore
import test_account_peers as fixtures
from test_account_peers import SELF, USER, CHANNEL, rpc


CHAT = dict(_='chat', id=50, title='Private fixture group', photo={'_': 'chatPhotoEmpty'},
            participants_count=2, date=1, version=1)
NOTIFY = {'_': 'peerNotifySettings'}


def full_user(user=None):
    return dict(_='userFull', user=SELF if user is None else user, about='Private fixture biography',
                settings={'_': 'peerSettings'}, notify_settings=NOTIFY, common_chats_count=3,
                pFlags={'blocked': True, 'phone_calls_available': True})


def basic_member(uid=42, role='chatParticipant'):
    return dict(_=role, user_id=uid, **({} if role == 'chatParticipantCreator' else dict(inviter_id=123, date=100)))


def full_chat(*, channel=False, hidden=False, count=5):
    if channel:
        full = dict(_='channelFull', id=42, about='Private fixture about',
                    read_inbox_max_id=0, read_outbox_max_id=0, unread_count=0,
                    chat_photo={'_': 'photoEmpty', 'id': 0}, notify_settings=NOTIFY,
                    bot_info=[], pts=20, pFlags={'can_view_participants': True})
        if count is not None:
            full['participants_count'] = count
        full['admins_count'] = 2
        chats = [CHANNEL]
    else:
        participants = dict(_='chatParticipantsForbidden', chat_id=50) if hidden else dict(
            _='chatParticipants', chat_id=50, version=2,
            participants=[basic_member(123, 'chatParticipantCreator'), basic_member()])
        full = dict(_='chatFull', id=50, about='Private fixture about', participants=participants, notify_settings=NOTIFY)
        chats = [CHAT]
    return dict(_='messages.chatFull', full_chat=full, chats=chats, users=[SELF, USER])


def channel_member(uid=42):
    return dict(_='channelParticipant', user_id=uid, date=100)


def member_page(ids=(42,), count=1):
    return dict(_='channels.channelParticipants', count=count,
        participants=[channel_member(uid) for uid in ids], chats=[],
        users=[dict(USER, id=uid) for uid in ids])


def one_member(value=None):
    return dict(_='channels.channelParticipant', participant=channel_member() if value is None else value,
                chats=[], users=[USER])


class MemberParsingTests(unittest.TestCase):
    def test_roles_dates_rights_and_immutable_metadata(self):
        roles = [('chatParticipant', 'member'), ('chatParticipantAdmin', 'admin'), ('chatParticipantCreator', 'owner'),
                 ('channelParticipant', 'member'), ('channelParticipantSelf', 'unknown'),
                 ('channelParticipantAdmin', 'admin'), ('channelParticipantCreator', 'owner')]
        for constructor, role in roles:
            raw = dict(_=constructor, user_id=42, date=100, inviter_id=123, promoted_by=123)
            if role in ('admin', 'owner') and constructor.startswith('channel'):
                raw['admin_rights'] = {'_': 'chatAdminRights', 'pFlags': {'delete_messages': True}}
                raw['rank'] = 'private rank'
            result = parse_member(raw, {})
            self.assertEqual(result.role, role)
            self.assertTrue(result.is_member)
            self.assertEqual(result.peer.id, 42)
            self.assertNotIn('private rank', repr(result))
            self.assertIsInstance(result.admin_rights, tuple)

    def test_banned_restricted_left_and_nonuser_peers(self):
        for rights, left, role, member in [({'send_messages': True}, False, 'restricted', True),
                                          ({'send_messages': True}, True, 'restricted', False),
                                          ({'view_messages': True}, False, 'banned', False)]:
            raw = dict(_='channelParticipantBanned', peer={'_': 'peerChannel', 'channel_id': 99},
                kicked_by=123, date=100, pFlags={'left': left},
                banned_rights={'_': 'chatBannedRights', 'until_date': 0, 'pFlags': rights})
            result = parse_member(raw, {})
            self.assertEqual((result.role, result.is_member, result.restriction_until), (role, member, 0))
            self.assertEqual(result.peer.kind, PeerKind.CHANNEL)
        result = parse_member(dict(_='channelParticipantLeft', peer={'_': 'peerUser', 'user_id': 42}), {})
        self.assertEqual((result.role, result.is_member), ('left', False))

    def test_invalid_participant_data_is_rejected(self):
        for value in (None, {}, dict(_='channelParticipant', user_id=True),
                      dict(_='channelParticipant', user_id=42, date=-1),
                      dict(_='channelParticipant', user_id=42, pFlags={'left': 1}),
                      dict(_='channelParticipantAdmin', user_id=42, admin_rights={'_': 'wrong'})):
            with self.assertRaises(TLCodecError):
                parse_member(value, {})

    def test_incomplete_roles_and_invalid_tags_do_not_invent_membership(self):
        for value in (dict(_='channelParticipantBanned', peer={'_': 'peerUser', 'user_id': 42}, date=100),
                      dict(_='channelParticipantCreator', user_id=42),
                      dict(_='channelParticipant', user_id=42), dict(_=[], user_id=42),
                      dict(_='chatParticipant', user_id=42, date=100)):
            with self.assertRaises(TLCodecError):
                parse_member(value, {})


class ProfileClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AccountReadTests.asyncSetUp(self)
        self.user = fixtures.entity_peer(USER)
        self.channel = fixtures.entity_peer(CHANNEL)
        self.group = fixtures.entity_peer(CHAT)

    async def test_self_full_user_uses_legacy_eitaa_response(self):
        self.invoke.return_value = rpc(full_user())
        info = await self.client.get_full_user()
        self.assertIsInstance(info, AccountUserFull)
        self.assertEqual(info.common_chats_count, 3)
        self.assertTrue(info.blocked)
        self.assertTrue(info.phone_calls_available)
        self.assertNotIn('Private fixture', repr(info))
        self.invoke.assert_awaited_once_with('users.getFullUser', {'id': {'_': 'inputUserSelf'}})

    async def test_other_full_user_uses_actual_access_hash(self):
        self.invoke.return_value = rpc(full_user(USER))
        info = await self.client.get_chat_info(self.user)
        self.assertEqual(info.peer.key, self.user.key)
        self.invoke.assert_awaited_once_with('users.getFullUser', {'id': {'_': 'inputUser', 'user_id': 42, 'access_hash': -987}})

    async def test_full_user_mismatch_never_enters_cache(self):
        self.invoke.return_value = rpc(full_user(dict(USER, id=999)))
        with self.assertRaises(TLCodecError):
            await self.client.get_full_user(self.user)
        self.assertNotIn((PeerKind.USER, 999), self.client._peers)

    async def test_full_group_members_and_admin_count(self):
        self.invoke.return_value = rpc(full_chat())
        info = await self.client.get_full_group(self.group)
        self.assertIsInstance(info, AccountChatFull)
        self.assertEqual((info.member_count, info.admin_count, info.participants_version), (2, 1, 2))
        self.assertTrue(info.can_view_participants)
        self.invoke.assert_awaited_once_with('messages.getFullChat', {'chat_id': 50})

    async def test_channel_optional_counts_and_zero_are_not_guessed(self):
        for count in (0, None, 5):
            self.invoke.return_value = rpc(full_chat(channel=True, count=count))
            info = await self.client.get_full_channel(self.channel)
            self.assertEqual(info.member_count, count)
            self.assertEqual(await self.client.get_member_count(self.channel), count)
            self.assertTrue(info.can_view_participants)

    async def test_supergroup_uses_channel_full_rpc(self):
        self.invoke.return_value = rpc(full_chat(channel=True))
        info = await self.client.get_full_group(self.channel)
        self.assertTrue(info.peer.is_megagroup)
        self.invoke.assert_awaited_once_with('channels.getFullChannel', {'channel': {'_': 'inputChannel', 'channel_id': 42, 'access_hash': 654}})

    async def test_wrong_profile_method_rejected_before_rpc(self):
        for call in (lambda: self.client.get_full_user(self.channel), lambda: self.client.get_full_channel(self.user),
                     lambda: self.client.get_full_group(self.user),
                     lambda: self.client.get_full_group(replace(self.channel, is_megagroup=False))):
            with self.assertRaises(ValueError):
                await call()
        self.invoke.assert_not_awaited()

    async def test_missing_and_wrong_full_chat_identity_is_rejected(self):
        for change in ('id', 'kind', 'entity'):
            value = full_chat(channel=True)
            if change == 'id':
                value['full_chat']['id'] = 999
            elif change == 'kind':
                value['full_chat']['_'] = 'chatFull'
            else:
                value['chats'] = []
            self.invoke.return_value = rpc(value)
            with self.assertRaises(TLCodecError):
                await self.client.get_full_channel(self.channel)

    async def test_negative_count_and_malformed_entity_lists_fail(self):
        for change in ('count', 'users', 'participants', 'duplicate'):
            value = full_chat(channel=change != 'participants')
            if change == 'count':
                value['full_chat']['participants_count'] = -1
            elif change == 'users':
                value['users'] = None
            elif change == 'participants':
                value['full_chat']['participants']['chat_id'] = 999
            else:
                value['users'] *= 2
            self.invoke.return_value = rpc(value)
            with self.assertRaises(TLCodecError):
                await self.client.get_chat_info(self.group if change == 'participants' else self.channel)

    async def test_forbidden_basic_members_are_not_an_empty_list(self):
        value = full_chat(hidden=True)
        self.invoke.return_value = rpc(value)
        info = await self.client.get_chat_info(self.group)
        self.assertEqual(info.member_count, 2)
        self.assertFalse(info.can_view_participants)
        for call in (lambda: self.client.get_members_page(self.group), lambda: self.client.get_member(self.group)):
            with self.assertRaises(UnsupportedOperationError):
                await call()
        value['chats'][0] = {k: v for k, v in CHAT.items() if k != 'participants_count'}
        self.assertIsNone((await self.client.get_chat_info(self.group)).member_count)

    async def test_basic_group_member_filters_pagination_and_lookup(self):
        self.invoke.return_value = rpc(full_chat())
        page = await self.client.get_members_page(self.group, limit=1)
        self.assertEqual((page.total_count, page.next_offset, page.members[0].role), (2, 1, 'owner'))
        page2 = await self.client.get_members_page(self.group, offset=1, limit=1)
        self.assertIsNone(page2.next_offset)
        self.assertEqual(page2.members[0].peer.id, 42)
        admins = await self.client.get_members_page(self.group, filter='admins')
        self.assertEqual(admins.total_count, 1)
        self.assertEqual((await self.client.get_member(self.group)).role, 'owner')
        self.assertIsNone(await self.client.get_member(self.group, AccountPeer(999, access_hash=1)))
        with self.assertRaises(UnsupportedOperationError):
            await self.client.get_members_page(self.group, filter='bots')

    async def test_channel_member_page_query_filters_and_exact_offset(self):
        self.invoke.return_value = rpc(member_page(count=9))
        for filter, constructor in [('recent', 'Recent'), ('admins', 'Admins'), ('bots', 'Bots'),
                                     ('search', 'Search'), ('banned', 'Banned'), ('kicked', 'Kicked')]:
            query = 'name' if filter in ('search', 'banned', 'kicked') else ''
            page = await self.client.get_members_page(self.channel, offset=2, limit=3, filter=filter, query=query)
            params = self.invoke.call_args.args[1]
            self.assertEqual(params['hash'], 0)
            self.assertEqual(params['offset'], 2)
            self.assertEqual(params['filter'], {'_': 'channelParticipants'+constructor, **({'q': query} if query else {})})
            self.assertEqual(page.next_offset, 3)
            self.assertEqual(page.members[0].peer.access_hash, -987)

    async def test_malformed_participant_pages_fail(self):
        for value in [dict(_='channels.channelParticipantsNotModified'), member_page((42, 42), 2),
                      member_page(count=0), {**member_page(), 'users': None},
                      {**member_page(), 'participants': [basic_member()]},
                      {**member_page(), 'participants': [{'_': None}]}]:
            self.invoke.return_value = rpc(value)
            with self.assertRaises(TLCodecError):
                await self.client.get_members_page(self.channel)

    async def test_participant_pages_larger_than_requested_fail(self):
        self.invoke.return_value = rpc(member_page((42, 43), 2))
        with self.assertRaises(TLCodecError):
            await self.client.get_members_page(self.channel, limit=1)

    async def test_user_membership_apis_and_invalid_iterator_limits_fail(self):
        for call in (lambda: self.client.get_members_page(self.user), lambda: self.client.get_member(self.user)):
            with self.assertRaises(ValueError):
                await call()
        for limit in (False, 0, 10001):
            with self.assertRaises(ValueError):
                _ = [m async for m in self.client.iter_members(self.channel, limit=limit)]
        self.invoke.assert_not_awaited()

    async def test_bad_page_arguments_never_call_rpc(self):
        for kwargs in ({'offset': -1}, {'offset': True}, {'offset': 2**31}, {'limit': 201}, {'limit': False},
                       {'filter': 'invalid'}, {'filter': []}, {'query': 'name'}, {'query': None},
                       {'filter': 'search', 'query': 'x'*257}):
            with self.assertRaises(ValueError):
                await self.client.get_members_page(self.channel, **kwargs)
        self.invoke.assert_not_awaited()

    async def test_iterator_uses_offsets_and_stops_at_user_limit(self):
        self.invoke.side_effect = [rpc(member_page((42, 43), 20)), rpc(member_page((44,), 20))]
        members = [m async for m in self.client.iter_members(self.channel, limit=3, page_size=2)]
        self.assertEqual([m.peer.id for m in members], [42, 43, 44])
        self.assertEqual([c.args[1]['offset'] for c in self.invoke.await_args_list], [0, 2])
        self.assertEqual([c.args[1]['limit'] for c in self.invoke.await_args_list], [2, 1])

    async def test_iterator_empty_page_and_repeated_page_are_bounded(self):
        self.invoke.return_value = rpc(member_page((), 20))
        self.assertEqual([m async for m in self.client.iter_members(self.channel)], [])
        self.invoke.return_value = rpc(member_page((42,), 20))
        with self.assertRaises(TLCodecError):
            _ = [m async for m in self.client.iter_members(self.channel, limit=3, page_size=1)]

    async def test_iterator_deduplicates_partial_overlap(self):
        self.invoke.side_effect = [rpc(member_page((42, 43), 4)), rpc(member_page((43, 44), 4))]
        self.assertEqual([m.peer.id async for m in self.client.iter_members(self.channel, limit=5, page_size=2)], [42, 43, 44])

    async def test_iterator_detects_identity_switch(self):
        self.invoke.return_value = rpc(member_page((42,), 3))
        stream = self.client.iter_members(self.channel, page_size=1)
        await anext(stream)
        self.client._session = replace(self.client._current_session(), user_id=999)
        with self.assertRaises(PeerResolveError):
            await anext(stream)

    async def test_member_lookup_matches_identity_and_not_participant(self):
        self.invoke.return_value = rpc(one_member())
        member = await self.client.get_member(self.channel, self.user)
        self.assertEqual(member.peer.key, self.user.key)
        self.assertEqual(self.invoke.call_args.args[1]['participant']['_'], 'inputPeerUser')
        self.invoke.return_value = rpc(one_member(channel_member(999)))
        with self.assertRaises(TLCodecError):
            await self.client.get_member(self.channel, self.user)
        self.invoke.side_effect = AccountRPCError(400, 'USER_NOT_PARTICIPANT')
        self.assertIsNone(await self.client.get_member(self.channel, self.user))

    async def test_self_participant_role_is_enriched_from_exact_channel_profile(self):
        for flags, rights, expected in [({'creator': True}, None, 'owner'),
                                       ({}, {'_': 'chatAdminRights', 'pFlags': {'delete_messages': True}}, 'admin'),
                                       ({}, None, 'unknown'),
                                       ({}, {'_': 'chatAdminRights'}, 'unknown')]:
            profile = full_chat(channel=True)
            profile['chats'] = [{**CHANNEL, 'pFlags': flags}]
            if rights is not None:
                profile['chats'][0]['admin_rights'] = rights
            self.invoke.reset_mock()
            self.invoke.side_effect = [rpc(one_member(dict(_='channelParticipantSelf', user_id=123, inviter_id=42, date=100))), rpc(profile)]
            member = await self.client.get_member(self.channel)
            self.assertEqual(member.role, expected)
            self.assertTrue(member.is_member)
            self.assertEqual(member.role_source, 'participant' if expected == 'unknown' else 'channel_profile')
            self.assertEqual([c.args[0] for c in self.invoke.await_args_list], ['channels.getParticipant', 'channels.getFullChannel'])

    async def test_self_role_reconciliation_rejects_unrelated_profile_and_other_account(self):
        profile = full_chat(channel=True)
        profile['full_chat']['id'] = 999
        self.invoke.side_effect = [rpc(one_member(dict(_='channelParticipantSelf', user_id=123, inviter_id=42, date=100))), rpc(profile)]
        with self.assertRaises(TLCodecError):
            await self.client.get_member(self.channel)
        self.invoke.side_effect = [rpc(one_member(dict(_='channelParticipantSelf', user_id=42, inviter_id=123, date=100)))]
        with self.assertRaises(TLCodecError):
            await self.client.get_member(self.channel, self.user)

    async def test_self_role_lookup_permissions_are_not_silently_converted_to_member(self):
        self.invoke.side_effect = [rpc(one_member(dict(_='channelParticipantSelf', user_id=123, inviter_id=42, date=100))), AccountRPCError(400, 'FLOOD_WAIT_30')]
        with self.assertRaises(AccountRPCError):
            await self.client.get_member(self.channel)
        self.assertEqual(self.invoke.await_count, 2)

    async def test_permission_and_flood_errors_propagate_without_retry(self):
        for error in ('CHAT_ADMIN_REQUIRED', 'CHANNEL_PRIVATE', 'FLOOD_WAIT_30'):
            self.invoke.reset_mock()
            self.invoke.side_effect = AccountRPCError(403, error)
            with self.assertRaises(AccountRPCError):
                await self.client.get_members_page(self.channel)
            self.invoke.assert_awaited_once()

    async def test_read_can_renew_once_and_revocation_invalidates_session(self):
        self.invoke.side_effect = [AccountStateError('eitaa_updates_expire_token'), rpc(full_user())]
        with patch.object(self.client, '_refresh_token', new_callable=AsyncMock) as renew:
            await self.client.get_full_user()
            renew.assert_awaited_once()
        self.invoke.side_effect = AccountRPCError(401, 'SESSION_REVOKED')
        with self.assertRaises(SessionRevokedError):
            await self.client.get_full_user()
        self.assertFalse(self.client.is_authorized)

    async def test_profile_cancellation_remains_cancellation(self):
        self.invoke.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.client.get_full_user()
        self.invoke.assert_awaited_once()


class ProfileWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_self_owner_reconciliation_through_pinned_binary_http(self):
        from test_account_transport import response_for
        codec = TLCodec()
        profile = full_chat(channel=True)
        profile['chats'] = [{**CHANNEL, 'pFlags': {'creator': True}}]
        participant = one_member(dict(_='channelParticipantSelf', user_id=123, inviter_id=42, date=100))
        packets = [codec.encode([SELF], 'Vector<User>'), codec.encode(participant), codec.encode(profile)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'fixture.session')
            store = SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123, 'https://hasan.eitaa.ir/eitaa/', 2496))
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    member = await client.get_member(fixtures.entity_peer(CHANNEL))
                    self.assertEqual((member.role, member.role_source, member.is_member), ('owner', 'channel_profile', True))
                self.assertEqual(post.call_count, 3)

    async def test_pinned_binary_http_full_profiles_and_members(self):
        from test_account_transport import response_for
        codec = TLCodec()
        values = [full_user(), full_chat(), full_chat(channel=True), member_page(), one_member()]
        packets = [codec.encode([SELF], 'Vector<User>')] + [codec.encode(value) for value in values]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'fixture.session')
            store = SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123, 'https://hasan.eitaa.ir/eitaa/', 2496))
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    self.assertEqual((await client.get_full_user()).common_chats_count, 3)
                    self.assertEqual((await client.get_full_group(fixtures.entity_peer(CHAT))).member_count, 2)
                    self.assertEqual((await client.get_full_channel(fixtures.entity_peer(CHANNEL))).member_count, 5)
                    self.assertEqual((await client.get_members_page(fixtures.entity_peer(CHANNEL))).total_count, 1)
                    self.assertEqual((await client.get_member(fixtures.entity_peer(CHANNEL), fixtures.entity_peer(USER))).role, 'member')
                self.assertEqual(post.call_count, 6)
                payload = codec.encode_method('channels.getParticipant', dict(channel={'_': 'inputChannel', 'channel_id': 42, 'access_hash': 654}, participant={'_': 'inputPeerUser', 'user_id': 42, 'access_hash': -987}))
                self.assertIn(payload, post.call_args.kwargs['data'])


class ProfileExampleTests(unittest.TestCase):
    def test_offline_verbs_never_open_session_network_or_prompt(self):
        from examples.account_profiles import main
        for verb in ('info', 'members', 'member'):
            with patch('sys.argv', ['account_profiles', verb]), patch('builtins.print'), \
                    patch('examples.account_profiles.AccountClient') as client, \
                    patch('builtins.input') as prompt, patch('fnceitaa.account.requests.post') as network:
                main()
                client.assert_not_called()
                prompt.assert_not_called()
                network.assert_not_called()

    def test_invalid_cli_bounds_fail_before_session(self):
        from examples.account_profiles import main
        with patch('sys.argv', ['account_profiles', '--live', '--page-size', '201']), \
                patch('examples.account_profiles.AccountClient') as client, patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                main()
            client.assert_not_called()


class ProfileExampleLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_output_omits_names_and_biography(self):
        from examples.account_profiles import run
        args = argparse.Namespace(session='fixture', command='info', peer='self', load_dialogs=False,
                                  show_names=False, show_about=False)
        with patch('examples.account_profiles.AccountClient') as factory, patch('builtins.print') as output:
            client = factory.return_value.__aenter__.return_value
            client.get_chat_info = AsyncMock(return_value=AccountUserFull(fixtures.entity_peer(SELF), 'private biography'))
            await run(args)
            self.assertNotIn('private biography', str(output.call_args_list))
            self.assertNotIn('Fixture', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
