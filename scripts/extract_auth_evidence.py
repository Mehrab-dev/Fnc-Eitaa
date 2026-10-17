"""Record auth source offsets/hashes without executing upstream JavaScript."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TERMS = {
    'mtproto.worker-Zk-UnVZ2.js': {
        'main_domain_application': 're.isMainDomain&&(re.id=2496',
        'device_identifier': 'async function gf(',
        'legacy_password_hash': 'async function ks(',
        'login_password_state': 'getLoginState(){',
        'password_branch': 'check(e,t,s={},a){',
        'send_code_pong': 'c==="auth.sendCode"',
        'token_refresh': 'e.storeMethod("eitaaRefreshToken"',
        'http_response_request_id': 'this.parseResponse(a,t.msg_id)',
        'direct_http_response_parser': 'const b=new Vt(e,{mtproto:!0});let D;',
        'direct_http_single_object': 'D=b.fetchObject("","INPUT")',
    },
    'pagePassword-DnS-jVDj.js': {'fetch_password_state': 'passwordManager.getLoginState()',
                                  'submit_password': 'passwordManager.check('},
    'crypto.worker-BvnnVP9w.js': {'srp_kdf': 'async function Or(', 'srp_proof': 'async function Lr('},
}


def extract():
    manifest = json.loads((ROOT / 'research/manifest.json').read_text(encoding='utf-8'))
    assets = {asset['file']: asset for asset in manifest['assets']}
    output = {'observed_at': manifest['observed_at'], 'offset_unit': 'Unicode code points',
              'sources': [], 'notes': [
                  'Public static source only; no JavaScript executed and no account credentials accessed.',
                  'auth.checkPassword2 source supplies flags=2 while including phone_code (bit 0) and phone_number (bit 1). SDK computes flags=3 from both present fields.',
                  'SRP uses local CSPRNG entropy instead of relying solely on server secure_random.',
                  'A sendCode pong is only an ACK; synthetic hash uses the local HTTP request id as in the web source.',
              ]}
    for filename, terms in TERMS.items():
        asset = assets[filename]
        raw = (ROOT / 'research' / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset['sha256'] or len(raw) != asset['bytes']:
            raise ValueError('Auth source does not match manifest')
        source = raw.decode('utf-8')
        anchors = []
        for label, term in terms.items():
            offset = source.find(term)
            if offset < 0:
                raise ValueError(f'Auth source anchor missing: {label}')
            anchors.append({'name': label, 'offset': offset, 'selector': term})
        output['sources'].append({**asset, 'anchors': anchors})
    return (json.dumps(output, ensure_ascii=False, indent=2) + '\n').encode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'research/protocol/auth_evidence.json'
    data = extract()
    if args.check:
        if path.read_bytes() != data:
            raise ValueError('Auth evidence differs from sources')
    else:
        path.write_bytes(data)
    print('Auth source evidence verified' if args.check else 'Auth source evidence recorded')
