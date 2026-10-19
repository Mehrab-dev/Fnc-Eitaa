"""Account message models and correlation of text-send acknowledgements."""
from dataclasses import dataclass, field, replace

from .account_peers import AccountPeer, peer_key
from .account_files import AccountMediaInfo, parse_media
from .protocol import TLCodecError


def message_id(value):
    return type(value) is int and 0 < value < 2**31


@dataclass(frozen=True)
class AccountMessage:
    id: int
    peer: AccountPeer
    date: int | None
    text: str = field(default='', repr=False)
    outgoing: bool = False
    reply_to_msg_id: int | None = None
    sender: AccountPeer | None = None
    is_service: bool = False
    has_media: bool = False
    random_id: int | None = field(default=None, repr=False)
    media: AccountMediaInfo | None = field(default=None, repr=False)
    grouped_id: int | None = None


@dataclass(frozen=True)
class HistoryPage:
    peer: AccountPeer
    messages: tuple[AccountMessage, ...]
    total_count: int | None
    next_offset_id: int | None


def parse_message(value, peer, peers) -> AccountMessage | None:
    if not isinstance(value, dict) or not message_id(value.get('id')):
        raise TLCodecError('Invalid account message identity')
    if value.get('_') == 'messageEmpty':
        return None
    if value.get('_') not in ('message', 'messageService'):
        raise TLCodecError('Unsupported account message constructor')
    if peer_key(value.get('peer_id')) != peer.key:
        raise TLCodecError('Message belongs to a different conversation')
    date = value.get('date')
    text = value.get('message', '')
    if type(date) is not int or not 0 <= date < 2**31 or not isinstance(text, str):
        raise TLCodecError('Invalid account message fields')
    sender = None
    if 'from_id' in value:
        key = peer_key(value['from_id'])
        sender = peers.get(key) or AccountPeer(key[1], key[0])
    reply_id = value.get('reply_to', {}).get('reply_to_msg_id')
    if reply_id is not None and not message_id(reply_id):
        raise TLCodecError('Invalid reply message identity')
    grouped = value.get('grouped_id')
    if grouped is not None and (type(grouped) is not int or not -2**63 <= grouped < 2**63 or grouped == 0):
        raise TLCodecError('Invalid media group identity')
    return AccountMessage(value['id'], peer, date, text,
                          bool(value.get('pFlags', {}).get('out')), reply_id,
                          sender, value['_'] == 'messageService', 'media' in value,
                          media=parse_media(value.get('media')), grouped_id=grouped)


def correlate_sent(value, peer, random_id, text, reply_to_msg_id, peers) -> AccountMessage:
    """Accept a short sent receipt or an exact random_id -> message_id mapping.

    An unrelated outgoing update, pong or a successfully decoded Updates object
    alone is not evidence that this particular message was accepted.
    """
    if not isinstance(value, dict):
        raise TLCodecError('Missing text-send acknowledgement')
    name = value.get('_')
    if name == 'updateShortSentMessage':
        identifier, date = value.get('id'), value.get('date')
        if not message_id(identifier) or type(date) is not int or not 0 <= date < 2**31:
            raise TLCodecError('Invalid short text-send acknowledgement')
        return AccountMessage(identifier, peer, date, text, True, reply_to_msg_id,
                              has_media='media' in value, random_id=random_id)
    if name in ('updates', 'updatesCombined'):
        updates = value.get('updates', [])
    elif name == 'updateShort':
        updates = [value.get('update', {})]
    else:
        raise TLCodecError('Unconfirmed text-send response')
    mappings = [update.get('id') for update in updates
                if update.get('_') == 'updateMessageID' and update.get('random_id') == random_id]
    if len(mappings) != 1 or not message_id(mappings[0]):
        raise TLCodecError('Text-send response has no unique matching random ID')
    identifier = mappings[0]
    candidates = [update['message'] for update in updates
                  if update.get('_') in ('updateNewMessage', 'updateNewChannelMessage')
                  and isinstance(update.get('message'), dict)
                  and update['message'].get('id') == identifier
                  and peer_key(update['message'].get('peer_id')) == peer.key]
    if len(candidates) > 1:
        raise TLCodecError('Ambiguous confirmed message update')
    if candidates:
        message = parse_message(candidates[0], peer, peers)
        if message is None:
            raise TLCodecError('Confirmed message update is empty')
        return replace(message, random_id=random_id)
    date = value.get('date')
    if type(date) is not int or not 0 <= date < 2**31:
        raise TLCodecError('Invalid text-send receipt date')
    # A matching server mapping confirms the ID even without the full message.
    return AccountMessage(identifier, peer, date, text, True, reply_to_msg_id,
                          random_id=random_id)
