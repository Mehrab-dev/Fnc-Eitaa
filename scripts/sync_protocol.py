"""Copy the reviewed research snapshot into package resources; --check is read-only."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync(check=False):
    research = ROOT / 'research/protocol'
    package = ROOT / 'fnceitaa/protocol'
    raw_schema = (research / 'schema.json').read_bytes()
    schema = json.loads(raw_schema)
    inventory = json.loads((research / 'inventory.json').read_text(encoding='utf-8'))
    digest = inventory['metadata']['source_sha256']
    if schema['metadata']['source_sha256'] != digest:
        raise ValueError('Research schema and inventory do not match')
    config = {'source_sha256': digest,
              'blocked_methods': sorted(set(inventory['not_sent_by_web_client'])),
              'server_pools': inventory['server_pools']}
    outputs = {'schema.json': raw_schema, 'config.json': (json.dumps(config, indent=2) + '\n').encode()}
    for name, data in outputs.items():
        path = package / name
        if check:
            if path.read_bytes() != data:
                raise ValueError(f'Package protocol resource differs: {name}')
        else:
            path.write_bytes(data)
    print('Package protocol resources verified' if check else 'Package protocol resources updated')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    sync(args.check)
