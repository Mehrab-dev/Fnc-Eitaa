import gzip
import json
from pathlib import Path
import struct
import unittest

from fnceitaa.protocol import Limits, Schema, TLCall, TLCodec, TLCodecError


class CodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = Schema()

    def setUp(self):
        self.codec = TLCodec(self.schema)

    def test_integer_golden_bytes_and_precision(self):
        fixtures = [('int', -1, 'ffffffff'), ('int', -2147483648, '00000080'),
                    ('#', 4294967295, 'ffffffff'),
                    ('long', 9007199254740993, '0100000000002000'),
                    ('long', -9223372036854775808, '0000000000000080')]
        for kind, value, hex_value in fixtures:
            with self.subTest(kind=kind, value=value):
                data = bytes.fromhex(hex_value)
                self.assertEqual(self.codec.encode(value, kind), data)
                self.assertEqual(self.codec.decode(data, kind), value)

    def test_trailing_login_data_reports_structure_without_credentials(self):
        authorization = {'_': 'auth.authorization', 'token': 'private-token-fixture',
                         'user': {'_': 'user', 'id': 123456789,
                                  'phone': '989120000000', 'first_name': 'private-name'}}
        packet = self.codec.encode(authorization)
        with self.assertRaises(TLCodecError) as caught:
            self.codec.decode(packet + b'\0' * 4, 'auth.Authorization')
        message = str(caught.exception)
        self.assertIn(f'read={len(packet)}', message)
        self.assertIn('remaining=4', message)
        self.assertIn('auth.authorization@0', message)
        self.assertIn('user@', message)
        self.assertIn('flags=0x00000012', message)
        for secret in ('private-token-fixture', '989120000000', 'private-name', '123456789'):
            self.assertNotIn(secret, message)

    def test_trailing_diagnostics_are_bounded_and_reset_between_responses(self):
        packet = self.codec.encode([{'_': 'user', 'id': i} for i in range(20)], 'Vector<User>')
        with self.assertRaises(TLCodecError) as caught:
            self.codec.decode(packet + b'\0' * 4, 'Vector<User>')
        self.assertEqual(str(caught.exception).count('user@'), 6)
        with self.assertRaises(TLCodecError) as caught:
            self.codec.decode(self.codec.encode(True, 'Bool') + b'\0' * 4, 'Bool')
        self.assertIn('constructors: none', str(caught.exception))

    def test_authorization_prefix_rejects_errors_truncation_and_nonself_users(self):
        good = {'_': 'auth.authorization', 'token': 'fixture-token',
                'user': {'_': 'user', 'id': 123, 'self': True}}
        candidates = [self.codec.encode(good)[:-4],
                      self.codec.encode({'_': 'error', 'code': 401, 'text': 'INVALID_LOGIN'}) + b'\0' * 60]
        for user, token in [({'_': 'user', 'id': 123}, 'fixture-token'),
                            ({'_': 'user', 'id': 0, 'self': True}, 'fixture-token'),
                            ({'_': 'user', 'id': 123, 'self': True}, '')]:
            candidates.append(self.codec.encode({'_': 'auth.authorization', 'token': token, 'user': user}))
        for packet in candidates:
            with self.subTest(size=len(packet)), self.assertRaises(TLCodecError):
                self.codec.decode_authorization_prefix(packet)

    def test_integer_ranges_and_bool_rejected(self):
        for kind, value in [('int', True), ('int', 2 ** 31), ('long', 2 ** 63),
                            ('#', -1), ('int', 1.5), ('int', '12')]:
            with self.subTest(kind=kind, value=value), self.assertRaises(TLCodecError):
                self.codec.encode(value, kind)

    def test_utf8_and_length_boundaries_have_independent_golden_prefixes(self):
        self.assertEqual(self.codec.encode('سلام', 'string').hex(), '08d8b3d984d8a7d985000000')
        for length, prefix in [(0, b'\0'), (253, b'\xfd'), (254, b'\xfe\xfe\0\0'),
                               (256, b'\xfe\0\x01\0')]:
            with self.subTest(length=length):
                data = self.codec.encode(b'x' * length, 'bytes')
                self.assertTrue(data.startswith(prefix + b'x' * length))
                self.assertEqual(len(data) % 4, 0)
                self.assertEqual(self.codec.decode(data, 'bytes'), b'x' * length)

    def test_bad_utf8_padding_prefix_and_truncation_rejected(self):
        for kind, data in [('string', b'\x01\xff\0\0'), ('bytes', b'\xff\0\0\0'),
                           ('bytes', b'\x01a\x01\0'), ('bytes', b'\xfe\x01\0\0a\0\0\0'),
                           ('long', b'\0' * 4)]:
            with self.subTest(kind=kind, data=data), self.assertRaises(TLCodecError):
                self.codec.decode(data, kind)

    def test_double_fixed_width_and_bool(self):
        for kind, value in [('double', 3.25), ('int128', bytes(range(16))),
                            ('int256', b'a' * 32), ('Bool', True), ('Bool', False)]:
            with self.subTest(kind=kind):
                self.assertEqual(self.codec.decode(self.codec.encode(value, kind), kind), value)
        with self.assertRaises(TLCodecError):
            self.codec.encode(b'x', 'int128')
        with self.assertRaises(TLCodecError):
            self.codec.decode(b'\0' * 4, 'Bool')

    def test_boxed_and_bare_vectors(self):
        boxed = bytes.fromhex('15c4b51c0200000001000000feffffff')
        self.assertEqual(self.codec.encode([1, -2], 'Vector<int>'), boxed)
        self.assertEqual(self.codec.encode([1, -2], 'vector<int>'), boxed[4:])
        self.assertEqual(self.codec.decode(boxed, 'Vector<int>'), [1, -2])
        self.assertEqual(self.codec.decode(boxed[4:], 'vector<int>'), [1, -2])
        with self.assertRaises(TLCodecError):
            self.codec.decode(boxed)  # Cannot guess vector element type.

    def test_flags_computed_and_optional_true_has_no_payload(self):
        value = {'_': 'codeSettings', 'pFlags': {'allow_flashcall': True, 'allow_app_hash': True}}
        encoded = self.codec.encode(value)
        self.assertEqual(encoded.hex(), '83bebede11000000')
        decoded = self.codec.decode(encoded)
        self.assertEqual(decoded['flags'], 17)
        self.assertEqual(decoded['pFlags'], value['pFlags'])
        self.assertEqual(self.codec.encode(decoded), encoded)
        with self.assertRaises(TLCodecError):
            self.codec.encode({'_': 'codeSettings', 'flags': 1})
        with self.assertRaises(TLCodecError):
            self.codec.encode({'_': 'codeSettings', 'pFlags': {'typo': True}})

    def test_optional_fields_and_shared_bits(self):
        params = {'pts': 1, 'pts_total_limit': 0, 'date': 2, 'qts': 3}
        data = self.codec.encode_method('updates.getDifference', params)
        self.assertEqual(data.hex(), '519693250100000001000000000000000200000003000000')
        # message.views and message.forwards share bit 10.
        with self.assertRaises(TLCodecError):
            self.codec.encode({'_': 'message', 'views': 4})

    def test_nested_generic_method_bytes(self):
        inner = TLCall('updates.getState')
        data = self.codec.encode_method('invokeWithLayer', {'layer': 135, 'query': inner})
        self.assertEqual(data.hex(), '0d0d9bda870000002a88d4ed')
        self.assertEqual(self.codec.result_type('invokeWithLayer', {'layer': 135, 'query': inner}),
                         'updates.State')
        with self.assertRaises(TLCodecError):
            self.codec.encode_method('invokeWithLayer', {'layer': 135, 'query': b'raw'})

    def test_method_validation_does_not_silently_drop_inputs(self):
        for method, params in [('unknown', {}), ('updates.getState', {'typo': 1}),
                               ('auth.signIn', {'phone_number': 'fixture'})]:
            with self.subTest(method=method), self.assertRaises(TLCodecError):
                self.codec.encode_method(method, params)

    def test_expected_type_mismatch_and_unknown_constructor(self):
        user = self.codec.encode({'_': 'inputUserSelf'})
        with self.assertRaises(TLCodecError):
            self.codec.decode(user, 'Peer')
        with self.assertRaises(TLCodecError):
            self.codec.decode(bytes.fromhex('78563412'))
        with self.assertRaises(TLCodecError):
            self.codec.decode(user + b'\0' * 4)

    def test_bare_constructor_and_typed_nested_vector(self):
        value = {'_': 'inputUserSelf'}
        self.assertEqual(self.codec.encode(value, '%InputUser'), b'')
        # InputUser has multiple constructors; no guessing on a bare decode.
        with self.assertRaises(TLCodecError):
            self.codec.decode(b'', '%InputUser', namespace='API')
        values = [value, {'_': 'inputUser', 'user_id': 7, 'access_hash': -9}]
        data = self.codec.encode(values, 'Vector<InputUser>')
        self.assertEqual(self.codec.decode(data, 'Vector<InputUser>'), values)

    def test_rpc_result_uses_request_specific_vector_type(self):
        request_id = 123
        encoded = (bytes.fromhex('016d5cf3') + struct.pack('<q', request_id)
                   + self.codec.encode([{'_': 'inputUserSelf'}], 'Vector<InputUser>'))
        value = self.codec.decode(encoded, 'Vector<InputUser>', result_types={123: 'Vector<InputUser>'})
        self.assertEqual(value, {'_': 'rpc_result', 'req_msg_id': 123,
                                 'result': [{'_': 'inputUserSelf'}]})

    def test_container_has_bounded_bare_messages(self):
        value = {'_': 'msg_container', 'messages': [
            {'_': 'message', 'msg_id': 3, 'seqno': 1,
             'body': {'_': 'rpc_result', 'req_msg_id': 123,
                      'result': {'_': 'rpc_error', 'error_code': 401, 'error_message': 'INVALID_LOGIN'}}}]}
        data = self.codec.encode(value, namespace='MTProto')
        decoded = self.codec.decode(data)
        self.assertEqual(decoded['messages'][0]['body'], value['messages'][0]['body'])
        # Header: container ID + count + msg_id + seqno; then body length.
        for size in (-1, 3, 4, 999999):
            broken = bytearray(data)
            struct.pack_into('<i', broken, 20, size)
            with self.subTest(size=size), self.assertRaises(TLCodecError):
                self.codec.decode(bytes(broken))

    def test_gzip_is_transparent_and_expected_type_is_preserved(self):
        body = self.codec.encode([1, 2, 3], 'Vector<int>')
        packed = self.codec.encode({'_': 'gzip_packed', 'packed_data': gzip.compress(body)}, namespace='MTProto')
        self.assertEqual(self.codec.decode(packed, 'Vector<int>'), [1, 2, 3])
        for bad in (b'not gzip', gzip.compress(body)[:-3], gzip.compress(body) + b'junk'):
            wire = self.codec.encode({'_': 'gzip_packed', 'packed_data': bad}, namespace='MTProto')
            with self.subTest(bad=bad), self.assertRaises(TLCodecError):
                self.codec.decode(wire, 'Vector<int>')

    def test_resource_limits(self):
        codec = TLCodec(self.schema, Limits(max_payload_bytes=64, max_blob_bytes=16,
                                           max_vector_items=2, max_nodes=4, max_depth=3,
                                           max_decompressed_bytes=16))
        invalid = [lambda: codec.encode(b'x' * 17, 'bytes'),
                   lambda: codec.encode([1, 2, 3], 'Vector<int>'),
                   lambda: codec.decode(b'x' * 65),
                   lambda: codec.decode(bytes.fromhex('15c4b51cffffffff'), 'Vector<int>'),
                   lambda: codec.decode(bytes.fromhex('15c4b51c03000000'), 'Vector<int>')]
        for operation in invalid:
            with self.assertRaises(TLCodecError):
                operation()
        packed = self.codec.encode({'_': 'gzip_packed', 'packed_data': gzip.compress(b'x' * 1000)}, namespace='MTProto')
        small = TLCodec(self.schema, Limits(max_decompressed_bytes=16))
        with self.assertRaises(TLCodecError):
            small.decode(packed)

    def test_depth_and_node_limits_include_nested_gzip(self):
        body = self.codec.encode({'_': 'inputUserSelf'})
        for _ in range(6):
            body = self.codec.encode({'_': 'gzip_packed', 'packed_data': gzip.compress(body)}, namespace='MTProto')
        with self.assertRaises(TLCodecError):
            TLCodec(self.schema, Limits(max_depth=3)).decode(body)
        with self.assertRaises(TLCodecError):
            TLCodec(self.schema, Limits(max_nodes=3)).decode(self.codec.encode([1, 2, 3], 'Vector<int>'), 'Vector<int>')

    def test_packet_matches_real_probe_and_error_decodes(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / 'research/protocol/public_probe.json').read_text())
        body = self.codec.encode_method('updates.getState')
        data = self.codec.encode_method('eitaaObject', {
            'token': '', 'imei': '000000000000001__web', 'packed_data': body,
            'layer': 135, 'flags': 128})
        self.assertEqual(data.hex(), fixture['request_hex'])
        self.assertEqual(self.codec.decode(bytes.fromhex(fixture['response_prefix_hex'])),
                         {'_': 'error', 'code': 401, 'text': 'INVALID_LOGIN'})
