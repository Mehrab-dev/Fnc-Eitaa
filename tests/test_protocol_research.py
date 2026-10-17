"""Offline checks for extracted public protocol data; no login or network calls."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.extract_protocol import LiteralReader, extract, generate

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / 'research'


class LiteralReaderTests(unittest.TestCase):
    def test_reads_data_without_javascript_evaluation(self):
        value = LiteralReader('{name:"test",id:-12,params:[],meta:{ok:true,none:null}}').read()
        self.assertEqual(value, {'name': 'test', 'id': -12, 'params': [],
                                 'meta': {'ok': True, 'none': None}})

    def test_rejects_executable_expressions_and_duplicate_keys(self):
        for value in ('{x:run()}', '{x:1+2}', '{x:1,x:2}', '{x:[', '{x:'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                LiteralReader(value).read()

    def test_handles_delimiters_inside_strings(self):
        self.assertEqual(LiteralReader('{value:"} ] \\""}').read(), {'value': '} ] "'})

    def test_rejects_missing_or_ambiguous_schema_root(self):
        for text in ('no schema', '{MTProto:{constructors:[]}},{MTProto:{constructors:[]} }'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                extract(text)


class ProtocolSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads((RESEARCH / 'protocol/schema.json').read_text(encoding='utf-8'))
        cls.inventory = json.loads((RESEARCH / 'protocol/inventory.json').read_text(encoding='utf-8'))

    def test_snapshot_contains_eitaa_specific_envelope(self):
        schema = self.snapshot['schema']
        self.assertEqual(schema['layer'], 135)
        envelope = next(e for e in schema['API']['methods'] if e['method'] == 'eitaaObject')
        self.assertEqual(envelope['id'] & 0xffffffff, 0x7abe77ed)
        self.assertEqual([(p['name'], p['type']) for p in envelope['params']],
                         [('token', 'string'), ('imei', 'string'), ('packed_data', 'bytes'),
                          ('layer', 'int'), ('flags', 'int')])

    def test_inventory_counts_and_anomalies_match_schema(self):
        for namespace in ('MTProto', 'API'):
            for collection in ('constructors', 'methods'):
                self.assertEqual(self.inventory['counts'][namespace][collection],
                                 len(self.snapshot['schema'][namespace][collection]))
        self.assertTrue(any(a['name'] == 'chatPhoto' for a in self.inventory['schema_anomalies']))
        self.assertIn('auth.exportLoginToken', self.inventory['not_sent_by_web_client'])
        self.assertNotIn('auth.signIn', self.inventory['not_sent_by_web_client'])

    def test_manifest_hashes_and_evidence_reproduce(self):
        manifest = json.loads((RESEARCH / 'manifest.json').read_text(encoding='utf-8'))
        if any(not (RESEARCH / item['file']).exists() for item in manifest['assets']):
            self.skipTest('Ignored upstream bundles are not present in this checkout')
        for item in manifest['assets']:
            data = (RESEARCH / item['file']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
            self.assertEqual(len(data), item['bytes'])
        bundle = RESEARCH / 'mtproto.worker-Zk-UnVZ2.js'
        with tempfile.TemporaryDirectory() as directory:
            generate(bundle, Path(directory))
            for filename in ('schema.json', 'inventory.json', 'evidence.json'):
                self.assertEqual((Path(directory) / filename).read_bytes(),
                                 (RESEARCH / 'protocol' / filename).read_bytes())

    def test_auth_evidence_reproduces_from_pinned_sources(self):
        from scripts.extract_auth_evidence import TERMS, extract as extract_auth
        if any(not (RESEARCH / name).exists() for name in TERMS):
            self.skipTest('Ignored upstream auth bundles are not present in this checkout')
        self.assertEqual(extract_auth(), (RESEARCH / 'protocol/auth_evidence.json').read_bytes())

    def test_recorded_public_response_matches_error_schema(self):
        probe = json.loads((RESEARCH / 'protocol/public_probe.json').read_text(encoding='utf-8'))
        response = bytes.fromhex(probe['response_prefix_hex'])
        self.assertEqual(len(response), probe['response_bytes'])
        constructor, code = struct.unpack_from('<Ii', response)
        error = next(e for e in self.snapshot['schema']['API']['constructors']
                     if e['predicate'] == 'error')
        self.assertEqual(constructor, error['id'] & 0xffffffff)
        self.assertEqual(code, 401)
        length = response[8]
        self.assertEqual(response[9:9 + length].decode('utf-8'), 'INVALID_LOGIN')

    def test_recorded_probe_contains_no_token_and_only_read_rpc(self):
        probe = json.loads((RESEARCH / 'protocol/public_probe.json').read_text(encoding='utf-8'))
        packet = bytes.fromhex(probe['request_hex'])
        self.assertEqual(struct.unpack_from('<I', packet)[0], 0x7abe77ed)
        self.assertEqual(packet[4:8], b'\x00\x00\x00\x00')  # Empty token + padding.
        imei_size = packet[8]
        packed_offset = 8 + ((1 + imei_size + 3) // 4) * 4
        self.assertEqual(packet[packed_offset], 4)
        self.assertEqual(packet[packed_offset + 1:packed_offset + 5], bytes.fromhex('2a88d4ed'))
        self.assertEqual(struct.unpack_from('<ii', packet, len(packet) - 8), (135, 128))
