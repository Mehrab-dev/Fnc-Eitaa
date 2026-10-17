"""Extract literal protocol data without evaluating upstream JavaScript.

Run: python scripts/extract_protocol.py
Input bundles remain ignored; generated JSON records their exact SHA-256 hashes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
INTEGER = re.compile(r'-?\d+')
DECODER = json.JSONDecoder()


class LiteralReader:
    """Accept only objects, arrays, JSON strings, integers and bool/null literals."""

    def __init__(self, text, offset=0):
        self.text = text
        self.offset = offset

    def whitespace(self):
        while self.offset < len(self.text) and self.text[self.offset].isspace():
            self.offset += 1

    def peek(self):
        self.whitespace()
        if self.offset >= len(self.text):
            raise ValueError('Unexpected end of literal')
        return self.text[self.offset]

    def consume(self, value):
        self.whitespace()
        if not self.text.startswith(value, self.offset):
            raise ValueError(f'Expected {value!r} at character {self.offset}')
        self.offset += len(value)

    def read(self):
        char = self.peek()
        if char == '{':
            self.offset += 1
            result = {}
            if self.peek() == '}':
                self.offset += 1
                return result
            while True:
                if self.peek() == '"':
                    key = self.read()
                else:
                    match = IDENTIFIER.match(self.text, self.offset)
                    if match is None:
                        raise ValueError(f'Invalid object key at {self.offset}')
                    key = match.group()
                    self.offset += len(key)
                self.consume(':')
                if key in result:
                    raise ValueError(f'Duplicate literal key: {key}')
                result[key] = self.read()
                if self.peek() == '}':
                    self.offset += 1
                    return result
                self.consume(',')
        if char == '[':
            self.offset += 1
            result = []
            if self.peek() == ']':
                self.offset += 1
                return result
            while True:
                result.append(self.read())
                if self.peek() == ']':
                    self.offset += 1
                    return result
                self.consume(',')
        if char == '"':
            result, self.offset = DECODER.raw_decode(self.text, self.offset)
            return result
        for token, value in (('true', True), ('false', False), ('null', None)):
            if self.text.startswith(token, self.offset):
                self.offset += len(token)
                return value
        match = INTEGER.match(self.text, self.offset)
        if match:
            self.offset += len(match.group())
            return int(match.group())
        raise ValueError(f'Unsupported nonliteral syntax at character {self.offset}')


def extract(text):
    starts = list(re.finditer(r'\{MTProto:\{constructors:\[', text))
    if len(starts) != 1:
        raise ValueError('Expected exactly one MTProto/API schema root')
    schema = LiteralReader(text, starts[0].start()).read()
    if set(schema) != {'MTProto', 'API', 'layer'} or not isinstance(schema['layer'], int):
        raise ValueError('Unexpected schema namespaces')
    for name in ('MTProto', 'API'):
        namespace = schema[name]
        for collection, key in (('constructors', 'predicate'), ('methods', 'method')):
            entries = namespace[collection]
            if not all(isinstance(e.get('id'), int) and isinstance(e.get(key, e.get('method')), str)
                       and isinstance(e.get('params'), list) for e in entries):
                raise ValueError('Invalid schema entry')
    pools = {}
    for name in ('dcClients', 'dcDownloads', 'dcUploads'):
        marker = f'this.{name}='
        pools[name] = LiteralReader(text, text.index(marker) + len(marker)).read()
    marker = 'eitaaNoSend:'
    blocked = LiteralReader(text, text.index(marker) + len(marker)).read()
    return schema, pools, blocked


def generate(bundle, destination, verify=False):
    data = bundle.read_bytes()
    schema, pools, blocked = extract(data.decode('utf-8'))
    metadata = {
        'source': 'https://web.eitaa.com/' + bundle.name,
        'source_sha256': hashlib.sha256(data).hexdigest(),
        'source_bytes': len(data),
        'evidence': 'static public bundle; no authenticated wire capture',
    }
    schema_text = json.dumps({'metadata': metadata, 'schema': schema}, ensure_ascii=False,
                             separators=(',', ':')) + '\n'
    selected = []
    wanted = {'eitaaObject', 'auth.sendCode', 'auth.signIn', 'codeSettings',
              'auth.sentCode', 'auth.authorization', 'initConnection', 'invokeWithLayer',
              'eitaaAppInfo', 'eitaaRefreshToken', 'eitaa_updates_token',
              'eitaa_updates_expire_token', 'eitaa_token_updating', 'error', 'eitta_error',
              'pong', 'rpc_result', 'rpc_error', 'msg_container', 'gzip_packed',
              'users.getUsers', 'inputUserSelf', 'updates.getState', 'updates.getDifference',
              'messages.getHistory', 'contacts.resolveUsername', 'messages.sendMessage',
              'auth.checkPassword2', 'account.getPassword', 'auth.checkPassword'}
    counts = {}
    for namespace, content in schema.items():
        if namespace == 'layer':
            continue
        counts[namespace] = {key: len(value) for key, value in content.items()}
        for collection, entries in content.items():
            for entry in entries:
                if entry.get('method', entry.get('predicate')) in wanted:
                    selected.append({'namespace': namespace, 'collection': collection, **entry})
    anomalies = []
    for namespace in ('MTProto', 'API'):
        for collection, key in (('constructors', 'predicate'), ('methods', 'method')):
            seen = {}
            for entry in schema[namespace][collection]:
                name = entry.get(key, entry.get('method'))
                if key not in entry:
                    anomalies.append({'namespace': namespace, 'collection': collection,
                                      'name': name, 'issue': f'missing {key}; preserved as supplied'})
                if name in seen:
                    anomalies.append({'namespace': namespace, 'collection': collection,
                                      'name': name, 'issue': 'duplicate name; preserved in source order',
                                      'ids': [seen[name], entry['id']]})
                seen[name] = entry['id']
    findings = {'metadata': metadata, 'layer': schema['layer'], 'counts': counts, 'server_pools': pools,
                'schema_anomalies': anomalies,
                'not_sent_by_web_client': blocked, 'selected_entries': selected}
    anchors = {
        'http_post': ('class uo{', 1000),
        'request_envelope': ('async getEncryptedOutput(e,t){', 720),
        'response_decoding': ('async parseResponse(e,t){', 650),
        'api_wrapping': ('wrapApiCall(e,t={},s={}){', 2100),
        'response_correlation': ('handleSentEncryptedRequestHTTP(e,t,s){', 1400),
        'otp_ack': ('case"pong":', 850),
        'authorization_and_errors': ('case"auth.authorization":', 2400),
        'token_refresh': ('updateExpireToken(){', 1400),
        'device_identifier': ('const ff=20,', 1400),
        'two_step_password': ('check(e,t,s={},a){', 1200),
        'tl_strings': ('storeString(e,t){', 1200),
        'tl_flags': ('storeBody(e,t,s){', 850),
        'tl_objects': ('fetchObject(e,t){', 3000),
        'integer_layout': ('writeInt(e,t){', 1250),
        'blocked_methods': ('eitaaNoSend:[', 2700),
        'domain_api_config': ('re.isMainDomain&&(re.id=', 160),
    }
    evidence = []
    text = data.decode('utf-8')
    for name, (anchor, length) in anchors.items():
        if text.count(anchor) != 1:
            raise ValueError(f'Expected exactly one evidence anchor: {name}')
        offset = text.index(anchor)
        evidence.append({'name': name, 'character_offset': offset,
                         'excerpt': text[offset:offset + length]})
    outputs = {
        'schema.json': schema_text,
        'inventory.json': json.dumps(findings, ensure_ascii=False, indent=2) + '\n',
        'evidence.json': json.dumps({'metadata': metadata, 'offset_unit': 'Unicode code points',
                                     'evidence': evidence}, ensure_ascii=False, indent=2) + '\n',
    }
    if not verify:
        destination.mkdir(parents=True, exist_ok=True)
    for filename, content in outputs.items():
        path = destination / filename
        if verify:
            if path.read_bytes() != content.encode('utf-8'):
                raise ValueError(f'Snapshot differs: {path}')
        else:
            path.write_bytes(content.encode('utf-8'))
    print(json.dumps({'counts': counts, 'selected_entries': len(selected),
                      'not_sent': len(blocked), 'sha256': metadata['source_sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'research/mtproto.worker-Zk-UnVZ2.js')
    parser.add_argument('--output', type=Path, default=ROOT / 'research/protocol')
    parser.add_argument('--verify', action='store_true', help='Check snapshots without modifying files')
    args = parser.parse_args()
    generate(args.bundle, args.output, args.verify)
