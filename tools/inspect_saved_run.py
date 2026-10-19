"""Read-only structural verification of this run's Saved Messages artifacts."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fnceitaa import AccountClient
from fnceitaa.account_peers import peer_key


async def main():
    directory = ROOT / '.fnceitaa/live-tests/saved-messages-20261019-a'
    records = [json.loads(line) for line in (directory / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    identifiers = {identifier for record in records for identifier in record.get('message_ids', [])}
    async with AccountClient(ROOT / 'account.session', auto_login=False) as client:
        peer = await client.resolve_peer('self')
        history = await client.get_history('self', limit=100)
        artifacts = [message for message in history if directory.name in message.text]
        identifiers.update(message.id for message in artifacts)
        output = {'history': [dict(id=message.id, reply_to=message.reply_to_msg_id,
                                   media=message.media.kind.value if message.media else None,
                                   grouped_id=message.grouped_id) for message in artifacts], 'lookups': []}
        for identifier in sorted(identifiers):
            value = (await client._read_rpc('messages.getMessages', {'id': [{'_': 'inputMessageID', 'id': identifier}]})).result
            summaries = []
            for message in value.get('messages', []):
                summaries.append(dict(type=message.get('_'), id=message.get('id'),
                                      peer_matches_self=peer_key(message['peer_id']) == peer.key if 'peer_id' in message else None,
                                      forwarded=message.get('fwd_from') is not None,
                                      reply_to=message.get('reply_to', {}).get('reply_to_msg_id')))
            output['lookups'].append(dict(requested=identifier, response_type=value.get('_'), messages=summaries))
        (directory / 'readback-inspection.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
        print(json.dumps(output, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
