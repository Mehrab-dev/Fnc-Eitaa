"""Validation and exact acknowledgement matching for account message operations."""
from dataclasses import dataclass, field, replace
import secrets

from .account_messages import AccountMessage, message_id, parse_message
from .account_peers import AccountPeer, peer_key
from .enums import PeerKind
from .protocol import TLCodecError


def validate_text(text):
    if not isinstance(text, str) or not text:
        raise ValueError('Message text must be a nonempty string')
    try:
        units = len(text.encode('utf-16-le')) // 2 if len(text) <= 4096 else 4097
    except UnicodeEncodeError:
        raise ValueError('Message text contains invalid Unicode') from None
    if units > 4096:
        raise ValueError('This SDK accepts at most 4096 UTF-16 units in one message')


def validate_ids(values):
    if (not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 100
            or any(not message_id(value) for value in values) or len(set(values)) != len(values)):
        raise ValueError('message_ids must contain 1..100 unique positive signed 32-bit integers')
    return tuple(values)


def operation_random_ids(count, values):
    if values is None:
        values = [secrets.randbits(63) or 1 for _ in range(count)]
    if (not isinstance(values, (list, tuple)) or len(values) != count
            or any(type(value) is not int or not -2**63 <= value < 2**63 or value == 0 for value in values)
            or len(set(values)) != count):
        raise ValueError('random_ids must contain one unique nonzero signed 64-bit integer per message')
    return tuple(values)


@dataclass(frozen=True)
class DeletedMessages:
    """An RPC acknowledgement, not proof of each message's post-delete state."""
    peer: AccountPeer
    message_ids: tuple[int, ...]
    revoke: bool
    pts: int
    pts_count: int


@dataclass(frozen=True)
class ForwardedMessage:
    """A source/random ID -> destination ID receipt; full message is optional."""
    id: int
    peer: AccountPeer
    source_peer: AccountPeer
    source_message_id: int
    random_id: int = field(repr=False)
    is_copy: bool = False
    message: AccountMessage | None = field(default=None, repr=False)


def _updates(value):
    if not isinstance(value, dict):
        raise TLCodecError('Missing operation acknowledgement')
    if value.get('_') in ('updates', 'updatesCombined'):
        updates = value.get('updates')
    elif value.get('_') == 'updateShort':
        updates = [value.get('update')]
    else:
        raise TLCodecError('Unconfirmed operation acknowledgement')
    if not isinstance(updates, list) or any(not isinstance(item, dict) for item in updates):
        raise TLCodecError('Invalid operation update list')
    return updates


def correlate_edit(value, peer, identifier, text, peers):
    name = 'updateEditChannelMessage' if peer.kind == PeerKind.CHANNEL else 'updateEditMessage'
    candidates = [update['message'] for update in _updates(value)
                  if update.get('_') == name and isinstance(update.get('message'), dict)
                  and update['message'].get('id') == identifier
                  and peer_key(update['message'].get('peer_id')) == peer.key]
    if len(candidates) != 1:
        raise TLCodecError('Edit response has no unique matching message')
    result = parse_message(candidates[0], peer, peers)
    if result is None or result.is_service or result.text != text:
        raise TLCodecError('Edit acknowledgement does not contain the requested text')
    return result


def correlate_delete(value, peer, identifiers, revoke):
    if (not isinstance(value, dict) or value.get('_') != 'messages.affectedMessages'
            or any(type(value.get(key)) is not int or not 0 <= value[key] < 2**31
                   for key in ('pts', 'pts_count'))):
        raise TLCodecError('Invalid deletion acknowledgement')
    return DeletedMessages(peer, identifiers, revoke, value['pts'], value['pts_count'])


def correlate_forward(value, source, destination, identifiers, random_ids, peers, *, is_copy=False):
    updates = _updates(value)
    results, used = [], set()
    for identifier, random_id in zip(identifiers, random_ids):
        matches = [update.get('id') for update in updates
                   if update.get('_') == 'updateMessageID' and update.get('random_id') == random_id]
        if len(matches) != 1 or not message_id(matches[0]) or matches[0] in used:
            raise TLCodecError('Forward response has no unique destination mapping for every random ID')
        mid = matches[0]
        used.add(mid)
        name = 'updateNewChannelMessage' if destination.kind == PeerKind.CHANNEL else 'updateNewMessage'
        candidates = [update['message'] for update in updates
                      if update.get('_') == name and isinstance(update.get('message'), dict)
                      and update['message'].get('id') == mid
                      and peer_key(update['message'].get('peer_id')) == destination.key]
        if len(candidates) > 1:
            raise TLCodecError('Forward response has ambiguous destination messages')
        message = None
        if candidates:
            if is_copy and 'fwd_from' in candidates[0]:
                raise TLCodecError('Copy response still contains forward attribution')
            parsed = parse_message(candidates[0], destination, peers)
            if parsed is None or parsed.is_service:
                raise TLCodecError('Invalid forwarded message')
            message = replace(parsed, random_id=random_id)
        results.append(ForwardedMessage(mid, destination, source, identifier, random_id, is_copy, message))
    return tuple(results)
