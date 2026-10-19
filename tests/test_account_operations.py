import argparse
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fnceitaa import (AccountClient, AccountPeer, PeerKind, PeerResolveError,
                      MessageOperationCancelledError, MessageOperationUncertainError,
                      SessionRevokedError, UnsupportedOperationError)
from fnceitaa.account_operations import correlate_edit, correlate_forward, correlate_delete
from fnceitaa.errors import AccountRPCError, AccountStateError, AccountTransportError
from fnceitaa.protocol import TLCodec, TLCodecError
from fnceitaa.session import AccountSession, SessionStore
import test_account_peers as fixtures
from test_account_peers import USER, CHANNEL, message, rpc, SELF


def updates(items):
    return dict(_='updates', updates=items, users=[USER], chats=[CHANNEL], date=1000, seq=1)


def edited(text='changed', *, channel=False, identifier=42, mid=10):
    return updates([dict(_='updateEditChannelMessage' if channel else 'updateEditMessage',
                         message={**message('channel' if channel else 'user', identifier, mid), 'message': text},
                         pts=20, pts_count=1)])


def source_messages(messages=None):
    return dict(_='messages.messages', messages=[message()] if messages is None else messages,
                users=[USER], chats=[CHANNEL])


def forwarded(tokens=(777,), mids=(100,), *, channel=False, full=True):
    items = [dict(_='updateMessageID', random_id=token, id=mid) for token, mid in zip(tokens, mids)]
    if full:
        items += [dict(_='updateNewChannelMessage' if channel else 'updateNewMessage',
                       message=message('channel' if channel else 'user', mid=mid), pts=20, pts_count=1)
                  for mid in mids]
    return updates(items)


def affected():
    return dict(_='messages.affectedMessages', pts=20, pts_count=0)


class OperationModelTests(unittest.TestCase):
    def setUp(self):
        self.peer = AccountPeer(42, access_hash=-987)

    def test_edit_requires_exact_peer_id_and_requested_text(self):
        for value in (edited('old'), edited(identifier=99), edited(mid=99), forwarded(),
                      updates([]), {'_': 'updateShortSentMessage', 'id': 10, 'date': 1000}):
            with self.assertRaises(TLCodecError):
                correlate_edit(value, self.peer, 10, 'changed', {})
        self.assertEqual(correlate_edit(edited(), self.peer, 10, 'changed', {}).text, 'changed')

    def test_edit_short_combined_and_channel_binary_roundtrip(self):
        codec = TLCodec()
        channel = AccountPeer(42, PeerKind.CHANNEL, 654)
        value = edited(channel=True)
        for envelope in (value, {**value, '_': 'updatesCombined', 'seq_start': 1},
                         {'_': 'updateShort', 'update': value['updates'][0], 'date': 1000}):
            parsed = codec.decode(codec.encode(envelope), 'Updates')
            self.assertEqual(correlate_edit(parsed, channel, 10, 'changed', {}).id, 10)

    def test_forward_maps_out_of_order_random_ids_and_does_not_invent_content(self):
        result = correlate_forward(forwarded((888, 777), (101, 100), full=False),
            self.peer, self.peer, (10, 11), (777, 888), {})
        self.assertEqual([item.id for item in result], [100, 101])
        self.assertEqual([item.source_message_id for item in result], [10, 11])
        self.assertIsNone(result[0].message)
        self.assertNotIn('777', repr(result[0]))

    def test_forward_missing_duplicate_and_colliding_mappings_fail_entire_ack(self):
        for value in (forwarded((777,), full=False), forwarded((777, 888), (100, 100)),
                      forwarded((777, 777), (100, 101)), updates([]), {'_': 'pong'}):
            with self.assertRaises(TLCodecError):
                correlate_forward(value, self.peer, self.peer, (10, 11), (777, 888), {})

    def test_delete_receipt_accepts_zero_count_but_rejects_unrelated_ack(self):
        self.assertEqual(correlate_delete(affected(), self.peer, (10,), False).pts_count, 0)
        for value in ({'_': 'boolTrue'}, updates([]), {**affected(), 'pts_count': None},
                      {**affected(), 'pts': True}, {**affected(), 'pts_count': -1}):
            with self.assertRaises(TLCodecError):
                correlate_delete(value, self.peer, (10,), False)

    def test_copy_full_response_with_attribution_is_not_confirmed_as_a_copy(self):
        value = forwarded()
        value['updates'][1]['message']['fwd_from'] = {'_': 'messageFwdHeader', 'date': 100}
        with self.assertRaises(TLCodecError):
            correlate_forward(value, self.peer, self.peer, (10,), (777,), {}, is_copy=True)

    def test_duplicate_edit_and_full_forward_messages_are_ambiguous(self):
        edit = edited()
        edit['updates'] *= 2
        with self.assertRaises(TLCodecError):
            correlate_edit(edit, self.peer, 10, 'changed', {})
        forward = forwarded()
        forward['updates'].append(forward['updates'][-1])
        with self.assertRaises(TLCodecError):
            correlate_forward(forward, self.peer, self.peer, (10,), (777,), {})


class OperationClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.AccountReadTests.asyncSetUp(self)
        self.peer = AccountPeer(42, access_hash=-987)
        self.channel = AccountPeer(42, PeerKind.CHANNEL, 654)

    async def test_edit_uses_requested_id_text_and_clears_entities_once(self):
        self.invoke.return_value = rpc(edited())
        result = await self.client.edit_message(self.peer, 10, 'changed', no_webpage=True)
        self.assertEqual((result.id, result.text), (10, 'changed'))
        self.invoke.assert_awaited_once_with('messages.editMessage', {
            'peer': self.peer.to_input_peer(), 'id': 10, 'message': 'changed',
            'entities': [], 'no_webpage': True})

    async def test_channel_edit_uses_channel_input_peer(self):
        self.invoke.return_value = rpc(edited(channel=True))
        result = await self.client.edit_message(self.channel, 10, 'changed')
        self.assertEqual(result.peer.kind, PeerKind.CHANNEL)
        self.assertEqual(self.invoke.call_args.args[1]['peer'], self.channel.to_input_peer())

    async def test_common_delete_verifies_all_peers_then_one_delete_rpc(self):
        self.invoke.side_effect = [rpc(source_messages([message(mid=11), message()])), rpc(affected())]
        receipt = await self.client.delete_messages(self.peer, [10, 11], revoke=True)
        self.assertEqual(receipt.message_ids, (10, 11))
        self.assertTrue(receipt.revoke)
        self.assertEqual(self.invoke.await_args_list[0].args, ('messages.getMessages',
            {'id': [{'_': 'inputMessageID', 'id': 10}, {'_': 'inputMessageID', 'id': 11}]}))
        self.assertEqual(self.invoke.await_args_list[1].args,
                         ('messages.deleteMessages', {'id': [10, 11], 'revoke': True}))

    async def test_channel_delete_requires_shared_scope_and_correct_rpc(self):
        with self.assertRaises(ValueError):
            await self.client.delete_messages(self.channel, [10])
        self.invoke.assert_not_awaited()
        self.invoke.side_effect = [rpc(source_messages([message('channel')])), rpc(affected())]
        await self.client.delete_messages(self.channel, [10], revoke=True)
        self.assertEqual([call.args[0] for call in self.invoke.await_args_list],
                         ['channels.getMessages', 'channels.deleteMessages'])
        self.assertEqual(self.invoke.call_args.args[1], {
            'channel': {'_': 'inputChannel', 'channel_id': 42, 'access_hash': 654}, 'id': [10]})

    async def test_wrong_peer_missing_empty_or_duplicate_source_never_mutates(self):
        for values in ([message(identifier=99)], [], [{'_': 'messageEmpty', 'id': 10}],
                       [message(), message()], [message(mid=99)]):
            for operation in ('delete', 'forward'):
                self.invoke.reset_mock()
                self.invoke.return_value = rpc(source_messages(values))
                with self.assertRaises((PeerResolveError, TLCodecError)):
                    if operation == 'delete':
                        await self.client.delete_messages(self.peer, [10])
                    else:
                        await self.client.forward_message(self.peer, self.peer, 10, random_id=777)
                self.invoke.assert_awaited_once()
                self.assertEqual(self.invoke.call_args.args[0], 'messages.getMessages')

    async def test_forward_receipts_order_and_single_batch_rpc(self):
        self.invoke.side_effect = [rpc(source_messages([message(), message(mid=11)])),
                                  rpc(forwarded((888, 777), (101, 100)))]
        result = await self.client.forward_messages(self.peer, self.peer, [10, 11], random_ids=[777, 888], silent=True)
        self.assertEqual([r.id for r in result], [100, 101])
        self.assertFalse(result[0].is_copy)
        self.assertEqual(result[0].message.random_id, 777)
        self.assertEqual(self.invoke.call_args.args, ('messages.forwardMessages', {
            'from_peer': self.peer.to_input_peer(), 'id': [10, 11], 'random_id': [777, 888],
            'to_peer': self.peer.to_input_peer(), 'silent': True, 'drop_author': False}))

    async def test_copy_uses_server_drop_author_and_accepts_mapping_only(self):
        self.invoke.side_effect = [rpc(source_messages()), rpc(forwarded(full=False))]
        receipt = await self.client.copy_message(self.channel, self.peer, 10, random_id=777)
        self.assertTrue(receipt.is_copy)
        self.assertEqual(receipt.id, 100)
        self.assertIsNone(receipt.message)
        self.assertTrue(self.invoke.call_args.args[1]['drop_author'])
        self.assertEqual(self.invoke.call_args.args[1]['to_peer'], self.channel.to_input_peer())
        self.assertEqual([c.args[0] for c in self.invoke.await_args_list],
                         ['messages.getMessages', 'messages.forwardMessages'])

    async def test_protected_and_service_messages_never_copy_or_forward(self):
        for value in ({**message(), 'pFlags': {'noforwards': True}}, {**message(), '_': 'messageService'}):
            self.invoke.reset_mock()
            self.invoke.return_value = rpc(source_messages([value]))
            with self.assertRaises(UnsupportedOperationError):
                await self.client.copy_message(self.peer, self.peer, 10)
            self.invoke.assert_awaited_once()

    async def test_generated_random_id_is_returned_for_recovery(self):
        self.invoke.side_effect = [rpc(source_messages()), rpc(forwarded())]
        with patch('fnceitaa.account_operations.secrets.randbits', return_value=777):
            result = await self.client.forward_message(self.peer, self.peer, 10)
        self.assertEqual(result.random_id, 777)

    async def test_channel_source_forward_to_user_preserves_source_scope(self):
        self.invoke.side_effect = [rpc(source_messages([message('channel')])), rpc(forwarded())]
        result = await self.client.forward_message(self.peer, self.channel, 10, random_id=777)
        self.assertEqual(result.source_peer.kind, PeerKind.CHANNEL)
        self.assertEqual(self.invoke.await_args_list[0].args[0], 'channels.getMessages')
        self.assertEqual(self.invoke.call_args.args[1]['from_peer'], self.channel.to_input_peer())

    async def test_identity_change_between_resolutions_aborts_before_mutation(self):
        from dataclasses import replace

        async def resolve(peer):
            if peer == 'destination':
                self.client._session = replace(self.client._current_session(), user_id=999)
            return self.peer

        with patch.object(self.client, 'resolve_peer', side_effect=resolve):
            with self.assertRaises(PeerResolveError):
                await self.client.forward_message('destination', 'source', 10, random_id=777)
        self.invoke.assert_not_awaited()

    async def test_utf16_exact_boundary_and_single_boolean_id_rejected(self):
        text = '\U0001f600' * 2048
        self.invoke.return_value = rpc(edited(text))
        self.assertEqual((await self.client.edit_message(self.peer, 10, text)).text, text)
        self.invoke.reset_mock()
        with self.assertRaises(ValueError):
            await self.client.edit_message(self.peer, True, 'changed')
        self.invoke.assert_not_awaited()

    async def test_validation_before_any_rpc_and_no_automatic_splitting(self):
        for ids in ([], [0], [True], [2**31], [10, 10], list(range(1, 102)), '10'):
            with self.assertRaises(ValueError):
                await self.client.delete_messages(self.peer, ids)
            with self.assertRaises(ValueError):
                await self.client.forward_messages(self.peer, self.peer, ids)
        for text in ('', None, '\ud800', '\U0001f600' * 2049):
            with self.assertRaises(ValueError):
                await self.client.edit_message(self.peer, 10, text)
        for tokens in ([0], [True], [2**63], [], [777, 777]):
            with self.assertRaises(ValueError):
                await self.client.forward_messages(self.peer, self.peer, [10], random_ids=tokens)
        with self.assertRaises(ValueError):
            await self.client.delete_messages(self.peer, [10], revoke=1)
        with self.assertRaises(ValueError):
            await self.client.edit_message(self.peer, 10, 'changed', no_webpage=1)
        with self.assertRaises(ValueError):
            await self.client.forward_message(self.peer, self.peer, 10, silent=1)
        self.invoke.assert_not_awaited()

    async def test_all_mutation_failures_uncertain_without_any_write_retry(self):
        for operation in ('edit', 'delete', 'forward', 'copy'):
            for failure in (AccountTransportError('timeout'), TLCodecError('bad'),
                            AccountStateError('eitaa_updates_expire_token'), rpc({'_': 'pong'})):
                self.invoke.reset_mock()
                if operation == 'edit':
                    self.invoke.side_effect = failure if isinstance(failure, Exception) else None
                    if not isinstance(failure, Exception):
                        self.invoke.return_value = failure
                    call = self.client.edit_message(self.peer, 10, 'PRIVATE TEXT')
                else:
                    self.invoke.side_effect = [rpc(source_messages()), failure]
                    if operation == 'delete':
                        call = self.client.delete_messages(self.peer, [10])
                    elif operation == 'forward':
                        call = self.client.forward_message(self.peer, self.peer, 10, random_id=777)
                    else:
                        call = self.client.copy_message(self.peer, self.peer, 10, random_id=777)
                with self.assertRaises(MessageOperationUncertainError) as caught:
                    await call
                self.assertEqual(caught.exception.operation, operation)
                self.assertEqual(caught.exception.message_ids, (10,))
                self.assertNotIn('PRIVATE TEXT', str(caught.exception))
                self.assertEqual(self.invoke.await_count, 1 if operation == 'edit' else 2)

    async def test_explicit_rpc_errors_not_modified_permissions_and_flood_are_not_retried(self):
        for rpc_type in ('MESSAGE_NOT_MODIFIED', 'MESSAGE_EDIT_TIME_EXPIRED', 'CHAT_ADMIN_REQUIRED', 'FLOOD_WAIT_25'):
            self.invoke.reset_mock()
            self.invoke.side_effect = AccountRPCError(400, rpc_type)
            with self.assertRaises(AccountRPCError):
                await self.client.edit_message(self.peer, 10, 'changed')
            self.invoke.assert_awaited_once()

    async def test_revocation_during_mutation_clears_authorization(self):
        self.invoke.side_effect = AccountRPCError(401, 'SESSION_REVOKED')
        with self.assertRaises(SessionRevokedError):
            await self.client.edit_message(self.peer, 10, 'changed')
        self.assertFalse(self.client.is_authorized)

    async def test_preflight_read_failure_is_not_an_uncertain_write(self):
        self.invoke.side_effect = AccountTransportError('offline before mutation')
        with self.assertRaises(AccountTransportError):
            await self.client.delete_messages(self.peer, [10])
        self.invoke.assert_awaited_once()

    async def test_encoding_failure_before_write_is_not_uncertain(self):
        with patch.object(self.client._rpc_transport(), 'encode_request', side_effect=TLCodecError('preflight')):
            with self.assertRaises(TLCodecError):
                await self.client.edit_message(self.peer, 10, 'changed')
        self.invoke.assert_not_awaited()

    async def test_partial_forward_ack_is_uncertain_for_whole_operation(self):
        self.invoke.side_effect = [rpc(source_messages([message(), message(mid=11)])), rpc(forwarded())]
        with self.assertRaises(MessageOperationUncertainError) as caught:
            await self.client.forward_messages(self.peer, self.peer, [10, 11], random_ids=[777, 888])
        self.assertEqual(caught.exception.random_ids, (777, 888))
        self.assertEqual(self.invoke.await_count, 2)

    async def test_cancelled_mutations_retain_asyncio_semantics_and_ids(self):
        async def cancel(method, params):
            if method == 'messages.getMessages':
                return rpc(source_messages())
            raise asyncio.CancelledError

        self.invoke.side_effect = cancel
        for operation in ('edit', 'delete', 'forward', 'copy'):
            with self.assertRaises(MessageOperationCancelledError) as caught:
                if operation == 'edit':
                    await self.client.edit_message(self.peer, 10, 'changed')
                elif operation == 'delete':
                    await self.client.delete_messages(self.peer, [10])
                elif operation == 'forward':
                    await self.client.forward_message(self.peer, self.peer, 10, random_id=777)
                else:
                    await self.client.copy_message(self.peer, self.peer, 10, random_id=777)
            self.assertEqual(caught.exception.operation, operation)
            self.assertEqual(caught.exception.random_ids, (777,) if operation in ('forward', 'copy') else ())
            self.assertIsInstance(caught.exception, asyncio.CancelledError)


class OperationWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_operations_through_binary_http_and_pinned_method_flags(self):
        from test_account_transport import response_for
        codec = TLCodec()
        replies = [[SELF], edited(), source_messages(), affected(), source_messages(),
                   forwarded(), source_messages(), forwarded((888,), (101,), full=False)]
        packets = [codec.encode(value, 'Vector<User>') if isinstance(value, list) else codec.encode(value)
                   for value in replies]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'account.session')
            store = SessionStore(path)
            store.load()
            store.save(AccountSession('fixture-token', 'fixture-device', 123,
                                      'https://hasan.eitaa.ir/eitaa/', 2496))
            with patch('fnceitaa.account.requests.post', side_effect=[response_for(p) for p in packets]) as post:
                async with AccountClient(path) as client:
                    peer = AccountPeer(42, access_hash=-987)
                    self.assertEqual((await client.edit_message(peer, 10, 'changed')).id, 10)
                    self.assertEqual((await client.delete_messages(peer, [10])).message_ids, (10,))
                    self.assertEqual((await client.forward_message(peer, peer, 10, random_id=777)).id, 100)
                    self.assertEqual((await client.copy_message(peer, peer, 10, random_id=888)).id, 101)
                self.assertEqual(post.call_count, 8)
                forward_payload = codec.encode_method('messages.forwardMessages', dict(
                    from_peer=peer.to_input_peer(), id=[10], random_id=[888],
                    to_peer=peer.to_input_peer(), silent=False, drop_author=True))
                self.assertIn(forward_payload, post.call_args.kwargs['data'])
                self.assertEqual(int.from_bytes(forward_payload[4:8], 'little'), 1 << 11)


class OperationExampleTests(unittest.TestCase):
    def test_offline_commands_never_open_session_prompt_or_network(self):
        from examples.account_actions import main
        for verb in ('edit', 'delete', 'forward', 'copy'):
            with patch('sys.argv', ['account_actions', verb]), patch('builtins.print'), \
                    patch('examples.account_actions.AccountClient') as client, patch('builtins.input') as prompt, \
                    patch('fnceitaa.account.requests.post') as network:
                main()
                client.assert_not_called()
                prompt.assert_not_called()
                network.assert_not_called()


class OperationExampleLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_delete_without_shared_scope_never_prompts_or_writes(self):
        from examples.account_actions import run
        args = argparse.Namespace(command='delete', session='fixture', peer='self', from_peer='self',
                                  load_dialogs=False, message_id=[10], revoke=False, show_text=False)
        with patch('examples.account_actions.AccountClient') as factory, patch('builtins.input') as prompt:
            client = factory.return_value.__aenter__.return_value
            client.is_authorized = True
            client.resolve_peer = AsyncMock(return_value=AccountPeer(42, PeerKind.CHANNEL, 654))
            client.delete_messages = AsyncMock()
            with self.assertRaises(ValueError):
                await run(args)
            prompt.assert_not_called()
            client.delete_messages.assert_not_awaited()

    async def test_every_command_requires_exact_confirmation_after_preview(self):
        from examples.account_actions import run
        peer = AccountPeer(42, access_hash=-987)
        from fnceitaa.account_messages import parse_message
        original = parse_message(message(), peer, {})
        for verb in ('edit', 'delete', 'forward', 'copy'):
            for confirmation in ('', 'no', 'y', 'yes'):
                args = argparse.Namespace(command=verb, session='fixture', peer='self', from_peer='self',
                                          load_dialogs=False, message_id=[10], revoke=False, show_text=False)
                with patch('examples.account_actions.AccountClient') as factory, \
                        patch('builtins.input', side_effect=['changed', confirmation] if verb == 'edit' else [confirmation]), \
                        patch('builtins.print') as output, \
                        patch('examples.account_actions.secrets.randbits', return_value=777):
                    client = factory.return_value.__aenter__.return_value
                    client.is_authorized = True
                    client.resolve_peer = AsyncMock(return_value=peer)
                    client.get_message = AsyncMock(return_value=original)
                    receipt = correlate_forward(forwarded(), peer, peer, (10,), (777,), {})[0]
                    client.edit_message = AsyncMock(return_value=original)
                    client.delete_messages = AsyncMock(return_value=correlate_delete(affected(), peer, (10,), False))
                    client.forward_messages = AsyncMock(return_value=(receipt,))
                    client.copy_message = AsyncMock(return_value=receipt)
                    await run(args)
                    self.assertIn('Destination:', str(output.call_args_list))
                    method = getattr(client, {'edit': 'edit_message', 'delete': 'delete_messages',
                                             'forward': 'forward_messages', 'copy': 'copy_message'}[verb])
                    self.assertEqual(method.await_count, 1 if confirmation == 'yes' else 0)


if __name__ == '__main__':
    unittest.main()
