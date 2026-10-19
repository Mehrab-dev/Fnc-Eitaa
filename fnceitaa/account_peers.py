"""Account-scoped identities and dialog pagination; no public-page scraping."""
from dataclasses import dataclass, field

from .enums import PeerKind
from .errors import PeerResolveError
from .protocol import TLCodecError


def positive_id(value):
    return type(value) is int and 0 < value < 2**63


@dataclass(frozen=True)
class AccountPeer:
    id: int
    kind: PeerKind = PeerKind.USER
    access_hash: int | None = field(default=None, repr=False)
    title: str = ''
    username: str | None = None
    is_self: bool = False
    is_min: bool = False
    is_megagroup: bool = False

    def __post_init__(self):
        if not positive_id(self.id) or not isinstance(self.kind, PeerKind):
            raise ValueError('Account peer requires a positive 64-bit ID and PeerKind')
        if self.access_hash is not None and (
                type(self.access_hash) is not int or not -2**63 <= self.access_hash < 2**63):
            raise ValueError('Invalid peer access hash')
        if (not isinstance(self.title, str) or
                (self.username is not None and not isinstance(self.username, str)) or
                any(type(v) is not bool for v in (self.is_self, self.is_min, self.is_megagroup)) or
                (self.is_self and self.kind != PeerKind.USER)):
            raise ValueError('Invalid account peer metadata')

    @property
    def key(self) -> tuple[PeerKind, int]:
        return self.kind, self.id

    def to_input_peer(self) -> dict:
        if self.is_self:
            return {'_': 'inputPeerSelf'}
        if self.kind == PeerKind.CHAT:
            return {'_': 'inputPeerChat', 'chat_id': self.id}
        if self.is_min or self.access_hash is None:
            raise PeerResolveError('Peer needs a full account access hash; resolve its username or load dialogs')
        if self.kind == PeerKind.USER:
            return {'_': 'inputPeerUser', 'user_id': self.id, 'access_hash': self.access_hash}
        return {'_': 'inputPeerChannel', 'channel_id': self.id, 'access_hash': self.access_hash}


def peer_key(value) -> tuple[PeerKind, int]:
    if not isinstance(value, dict):
        raise TLCodecError('Invalid account peer reference')
    for name, kind, key in (('peerUser', PeerKind.USER, 'user_id'),
                            ('peerChat', PeerKind.CHAT, 'chat_id'),
                            ('peerChannel', PeerKind.CHANNEL, 'channel_id')):
        if value.get('_') == name and positive_id(value.get(key)):
            return kind, value[key]
    raise TLCodecError('Unsupported account peer reference')


def entity_peer(entity) -> AccountPeer | None:
    if not isinstance(entity, dict):
        raise TLCodecError('Invalid account entity')
    name = entity.get('_')
    if name in ('userEmpty', 'chatEmpty'):
        return None
    kinds = {'user': PeerKind.USER, 'chat': PeerKind.CHAT,
             'chatForbidden': PeerKind.CHAT, 'channel': PeerKind.CHANNEL,
             'channelForbidden': PeerKind.CHANNEL}
    kind = kinds.get(name)
    if kind is None:
        raise TLCodecError('Unsupported account entity constructor')
    flags = entity.get('pFlags', {})
    title = ((' '.join(entity.get(n, '') for n in ('first_name', 'last_name'))).strip()
             if kind == PeerKind.USER else entity.get('title', ''))
    try:
        return AccountPeer(entity['id'], kind, entity.get('access_hash'), title,
                           entity.get('username'), bool(flags.get('self')),
                           bool(flags.get('min')), bool(flags.get('megagroup')))
    except (KeyError, ValueError, TypeError):
        raise TLCodecError('Invalid account entity fields') from None


@dataclass(frozen=True)
class DialogCursor:
    offset_date: int
    offset_id: int
    peer: AccountPeer | None
    folder_id: int | None = None

    def __post_init__(self):
        if any(type(v) is not int or not 0 <= v < 2**31
               for v in (self.offset_date, self.offset_id)):
            raise ValueError('Invalid dialog offset')
        if (self.peer is not None and not isinstance(self.peer, AccountPeer)) or (
                self.folder_id is not None and (type(self.folder_id) is not int or self.folder_id not in (0, 1))):
            raise ValueError('Invalid dialog cursor')
        if self.peer is None:
            if self.offset_date or self.offset_id:
                raise ValueError('Empty cursor peer requires zero offsets')
        else:
            self.peer.to_input_peer()


@dataclass(frozen=True)
class AccountDialog:
    peer: AccountPeer
    top_message_id: int
    unread_count: int
    pinned: bool = False
    folder_id: int = 0


@dataclass(frozen=True)
class DialogPage:
    dialogs: tuple[AccountDialog, ...]
    total_count: int
    next_cursor: DialogCursor | None
