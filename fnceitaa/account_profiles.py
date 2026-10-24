"""Typed full profiles and read-only membership using the pinned Eitaa schema."""
from dataclasses import dataclass, field, replace
from typing import Literal, cast, overload

from .account_peers import AccountPeer, entity_peer, peer_key
from .enums import PeerKind
from .errors import AccountRPCError, PeerResolveError, UnsupportedOperationError
from .protocol import TLCodecError

BASIC_PARTICIPANTS = frozenset(('chatParticipant', 'chatParticipantAdmin', 'chatParticipantCreator'))
CHANNEL_PARTICIPANTS = frozenset(('channelParticipant', 'channelParticipantSelf', 'channelParticipantCreator',
                                 'channelParticipantAdmin', 'channelParticipantBanned', 'channelParticipantLeft'))


@dataclass(frozen=True)
class AccountUserFull:
    peer: AccountPeer = field(repr=False)
    about: str | None = field(default=None, repr=False)
    common_chats_count: int = 0
    blocked: bool = False
    phone_calls_available: bool = False
    phone_calls_private: bool = False
    video_calls_available: bool = False
    pinned_message_id: int | None = None


@dataclass(frozen=True)
class AccountChatFull:
    peer: AccountPeer = field(repr=False)
    about: str = field(repr=False)
    member_count: int | None = None
    admin_count: int | None = None
    banned_count: int | None = None
    kicked_count: int | None = None
    online_count: int | None = None
    can_view_participants: bool = False
    pinned_message_id: int | None = None
    linked_chat_id: int | None = None
    slowmode_seconds: int | None = None
    participants_version: int | None = None
    is_creator: bool = False
    admin_rights: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountMember:
    peer: AccountPeer = field(repr=False)
    role: str
    is_member: bool
    joined_at: int | None = None
    inviter_id: int | None = field(default=None, repr=False)
    promoted_by: int | None = field(default=None, repr=False)
    kicked_by: int | None = field(default=None, repr=False)
    rank: str | None = field(default=None, repr=False)
    admin_rights: tuple[str, ...] = ()
    banned_rights: tuple[str, ...] = ()
    restriction_until: int | None = None
    can_edit: bool = False
    role_source: str = 'participant'


@dataclass(frozen=True)
class MemberPage:
    peer: AccountPeer = field(repr=False)
    members: tuple[AccountMember, ...] = field(repr=False)
    total_count: int
    next_offset: int | None
    filter: str = 'recent'
    query: str = field(default='', repr=False)


@overload
def _count(value: object, optional: Literal[False] = False) -> int: ...
@overload
def _count(value: object, optional: Literal[True]) -> int | None: ...
def _count(value: object, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or not 0 <= value < 2**31:
        raise TLCodecError('Invalid profile count/date')
    return value


@overload
def _id(value: object, optional: Literal[False] = False) -> int: ...
@overload
def _id(value: object, optional: Literal[True]) -> int | None: ...
def _id(value: object, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or not 0 < value < 2**63:
        raise TLCodecError('Invalid profile identity')
    return value


@overload
def _text(value: object, optional: Literal[False] = False) -> str: ...
@overload
def _text(value: object, optional: Literal[True]) -> str | None: ...
def _text(value: object, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise TLCodecError('Invalid profile text')
    return value


def _flags(value):
    result = value.get('pFlags', {})
    if not isinstance(result, dict) or any(type(flag) is not bool for flag in result.values()):
        raise TLCodecError('Invalid profile flags')
    return result


def _rights(value, constructor):
    if value is None:
        return (), None
    if not isinstance(value, dict) or value.get('_') != constructor:
        raise TLCodecError('Invalid member rights')
    names = tuple(sorted(key for key, enabled in _flags(value).items() if enabled))
    until = _count(value.get('until_date')) if constructor == 'chatBannedRights' else None
    return names, until


def parse_member(value, peers) -> AccountMember:
    if not isinstance(value, dict):
        raise TLCodecError('Invalid participant')
    name = value.get('_')
    roles = {'chatParticipant': 'member', 'chatParticipantAdmin': 'admin', 'chatParticipantCreator': 'owner',
             'channelParticipant': 'member', 'channelParticipantSelf': 'unknown',
             'channelParticipantCreator': 'owner', 'channelParticipantAdmin': 'admin',
             'channelParticipantBanned': 'restricted', 'channelParticipantLeft': 'left'}
    if not isinstance(name, str) or name not in roles:
        raise TLCodecError('Unsupported participant constructor')
    key = peer_key(value.get('peer')) if name in ('channelParticipantBanned', 'channelParticipantLeft') else (PeerKind.USER, _id(value.get('user_id')))
    peer = peers.get(key) or AccountPeer(key[1], key[0])
    flags = _flags(value)
    if name in ('channelParticipantAdmin', 'channelParticipantCreator') and value.get('admin_rights') is None:
        raise TLCodecError('Administrator participant omitted rights')
    if name == 'channelParticipantBanned' and value.get('banned_rights') is None:
        raise TLCodecError('Restricted participant omitted rights')
    admin, _ = _rights(value.get('admin_rights'), 'chatAdminRights')
    banned, until = _rights(value.get('banned_rights'), 'chatBannedRights')
    role = 'banned' if name == 'channelParticipantBanned' and 'view_messages' in banned else roles[name]
    member = role not in ('banned', 'left') and not flags.get('left', False)
    joined = _count(value.get('date'), optional=name in ('chatParticipantCreator', 'channelParticipantCreator', 'channelParticipantLeft'))
    if name in ('chatParticipant', 'chatParticipantAdmin', 'channelParticipantSelf'):
        _id(value.get('inviter_id'))
    if name == 'channelParticipantAdmin':
        _id(value.get('promoted_by'))
    if name == 'channelParticipantBanned':
        _id(value.get('kicked_by'))
    return AccountMember(peer, role, member, joined,
        _id(value.get('inviter_id'), optional=True), _id(value.get('promoted_by'), optional=True),
        _id(value.get('kicked_by'), optional=True), _text(value.get('rank'), optional=True),
        admin, banned, until, flags.get('can_edit', False))


class AccountProfiles:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _input_user(peer):
        peer.to_input_peer()
        return {'_': 'inputUserSelf'} if peer.is_self else {
            '_': 'inputUser', 'user_id': peer.id, 'access_hash': peer.access_hash}

    @staticmethod
    def _input_channel(peer):
        peer.to_input_peer()
        return {'_': 'inputChannel', 'channel_id': peer.id, 'access_hash': peer.access_hash}

    def _owner(self):
        self.client._require_authorized()
        session = self.client._current_session()
        return session.user_id, session.device_id

    def _check(self, owner):
        if self._owner() != owner:
            raise PeerResolveError('Profile request belongs to a different account session')

    @staticmethod
    def _entities(value):
        users, chats = value.get('users'), value.get('chats')
        if not isinstance(users, list) or not isinstance(chats, list):
            raise TLCodecError('Profile response omitted entity lists')
        entities = users + chats
        peers = {}
        for entity in entities:
            peer = entity_peer(entity)
            if peer is not None:
                if peer.key in peers:
                    raise TLCodecError('Duplicate profile entity')
                peers[peer.key] = peer
        return entities, peers

    async def _full(self, peer, *, kind=None, group=False):
        owner = self._owner()
        resolved = await self.client.resolve_peer(peer)
        if kind is not None and resolved.kind != kind:
            raise ValueError('Destination has the wrong kind for this full-profile method')
        if group and (resolved.kind == PeerKind.USER or
                      (resolved.kind == PeerKind.CHANNEL and not resolved.is_megagroup)):
            raise ValueError('get_full_group requires a basic group or supergroup')
        if resolved.kind == PeerKind.USER:
            method, params = 'users.getFullUser', {'id': self._input_user(resolved)}
        elif resolved.kind == PeerKind.CHAT:
            method, params = 'messages.getFullChat', {'chat_id': resolved.id}
        else:
            method, params = 'channels.getFullChannel', {'channel': self._input_channel(resolved)}
        async with self.client._lock:
            self._check(owner)
            value = (await self.client._read_rpc(method, params)).result
            if not isinstance(value, dict):
                raise TLCodecError('Invalid full-profile response')
            if resolved.kind == PeerKind.USER:
                # Eitaa's pinned layer returns legacy userFull with its nested user.
                if value.get('_') != 'userFull':
                    raise TLCodecError('Unexpected full-user constructor')
                identity = entity_peer(value.get('user'))
                if identity is None or identity.key != resolved.key:
                    raise TLCodecError('Full-user response does not match destination')
                flags = _flags(value)
                info = AccountUserFull(identity, _text(value.get('about'), optional=True),
                    _count(value.get('common_chats_count')), flags.get('blocked', False),
                    flags.get('phone_calls_available', False), flags.get('phone_calls_private', False),
                    flags.get('video_calls_available', False), _count(value.get('pinned_msg_id'), optional=True))
                self.client._cache_entities([value['user']])
                return info, None
            if value.get('_') != 'messages.chatFull':
                raise TLCodecError('Unexpected full-chat constructor')
            entities, peers = self._entities(value)
            raw = value.get('full_chat')
            expected = 'chatFull' if resolved.kind == PeerKind.CHAT else 'channelFull'
            if not isinstance(raw, dict) or raw.get('_') != expected or _id(raw.get('id')) != resolved.id:
                raise TLCodecError('Full-chat response does not match destination')
            identity = peers.get(resolved.key)
            if identity is None:
                raise TLCodecError('Full-chat response omitted destination identity')
            identity_kinds = ('chat', 'chatForbidden') if resolved.kind == PeerKind.CHAT else ('channel', 'channelForbidden')
            identity_raw = next(e for e in entities if e.get('_') in identity_kinds and e.get('id') == resolved.id)
            identity_flags = _flags(identity_raw)
            own_admin_rights, _ = _rights(identity_raw.get('admin_rights'), 'chatAdminRights')
            flags = _flags(raw)
            members, version = None, None
            if resolved.kind == PeerKind.CHAT:
                participants = raw.get('participants')
                if not isinstance(participants, dict) or _id(participants.get('chat_id')) != resolved.id:
                    raise TLCodecError('Group participants do not match destination')
                if participants.get('_') == 'chatParticipants':
                    rows = participants.get('participants')
                    if not isinstance(rows, list):
                        raise TLCodecError('Invalid group participant list')
                    members = tuple(parse_member(row, peers) for row in rows)
                    if any(row.get('_') not in BASIC_PARTICIPANTS for row in rows):
                        raise TLCodecError('Unexpected basic-group participant type')
                    self._unique(members)
                    version = _count(participants.get('version'))
                elif participants.get('_') != 'chatParticipantsForbidden':
                    raise TLCodecError('Unexpected group participants constructor')
                count = len(members) if members is not None else _count(
                    next(e for e in entities if e.get('id') == resolved.id and e.get('_') in ('chat', 'chatForbidden')).get('participants_count'), optional=True)
                admins = sum(m.role in ('owner', 'admin') for m in members) if members is not None else None
                can_view = members is not None
            else:
                count, admins = _count(raw.get('participants_count'), optional=True), _count(raw.get('admins_count'), optional=True)
                can_view = flags.get('can_view_participants', False)
            info = AccountChatFull(identity, _text(raw.get('about')), count, admins,
                _count(raw.get('banned_count'), optional=True), _count(raw.get('kicked_count'), optional=True),
                _count(raw.get('online_count'), optional=True), can_view,
                _count(raw.get('pinned_msg_id'), optional=True), _id(raw.get('linked_chat_id'), optional=True),
                _count(raw.get('slowmode_seconds'), optional=True), version,
                identity_flags.get('creator', False), own_admin_rights)
            self.client._cache_entities(entities)
            return info, members

    @staticmethod
    def _unique(members):
        if len({member.peer.key for member in members}) != len(members):
            raise TLCodecError('Duplicate participants in response')

    async def get_chat_info(self, chat_id) -> AccountUserFull | AccountChatFull:
        return (await self._full(chat_id))[0]

    async def get_full_user(self, chat_id='self') -> AccountUserFull:
        return cast(AccountUserFull, (await self._full(chat_id, kind=PeerKind.USER))[0])

    async def get_full_group(self, chat_id) -> AccountChatFull:
        return cast(AccountChatFull, (await self._full(chat_id, group=True))[0])

    async def get_full_channel(self, chat_id) -> AccountChatFull:
        return cast(AccountChatFull, (await self._full(chat_id, kind=PeerKind.CHANNEL))[0])

    async def get_member_count(self, chat_id) -> int | None:
        info = await self.get_chat_info(chat_id)
        if isinstance(info, AccountUserFull):
            raise ValueError('A user has no member count')
        return info.member_count

    @staticmethod
    def _page_options(offset, limit, filter, query):
        filters = {'recent': 'channelParticipantsRecent', 'admins': 'channelParticipantsAdmins',
                   'bots': 'channelParticipantsBots', 'search': 'channelParticipantsSearch',
                   'banned': 'channelParticipantsBanned', 'kicked': 'channelParticipantsKicked'}
        if type(offset) is not int or not 0 <= offset < 2**31 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Use offset 0..2**31-1 and limit 1..200')
        if not isinstance(filter, str) or filter not in filters:
            raise ValueError('Unsupported member filter')
        if not isinstance(query, str) or len(query) > 256 or '\0' in query:
            raise ValueError('Invalid member query')
        if query and filter not in ('search', 'banned', 'kicked'):
            raise ValueError('query is available only for search, banned and kicked filters')
        result = {'_': filters[filter]}
        if filter in ('search', 'banned', 'kicked'):
            result['q'] = query
        return result

    async def get_members_page(self, chat_id, *, offset=0, limit=100, filter='recent', query='') -> MemberPage:
        selector = self._page_options(offset, limit, filter, query)
        owner = self._owner()
        resolved = await self.client.resolve_peer(chat_id)
        if resolved.kind == PeerKind.USER:
            raise ValueError('A user has no member list')
        if resolved.kind == PeerKind.CHAT:
            if filter not in ('recent', 'admins'):
                raise UnsupportedOperationError('Basic groups support recent/admins filters only')
            info, members = await self._full(resolved)
            self._check(owner)
            if members is None:
                raise UnsupportedOperationError('Group member list is unavailable to this account')
            if filter == 'admins':
                members = tuple(m for m in members if m.role in ('owner', 'admin'))
            selected, total = members[offset:offset + limit], len(members)
        else:
            async with self.client._lock:
                self._check(owner)
                value = (await self.client._read_rpc('channels.getParticipants', dict(
                    channel=self._input_channel(resolved), filter=selector, offset=offset, limit=limit, hash=0))).result
                if not isinstance(value, dict) or value.get('_') != 'channels.channelParticipants':
                    raise TLCodecError('Unexpected channel participant list constructor')
                entities, peers = self._entities(value)
                rows = value.get('participants')
                if not isinstance(rows, list) or len(rows) > limit:
                    raise TLCodecError('Invalid channel participant page size')
                if any(not isinstance(row, dict) or not isinstance(row.get('_'), str) or row['_'] not in CHANNEL_PARTICIPANTS for row in rows):
                    raise TLCodecError('Unexpected channel participant type')
                selected = tuple(parse_member(row, peers) for row in rows)
                self._unique(selected)
                total = _count(value.get('count'))
                if len(selected) > total:
                    raise TLCodecError('Participant count is smaller than the page')
                self.client._cache_entities(entities)
        following = offset + len(selected)
        next_offset = following if selected and following < total else None
        return MemberPage(resolved, selected, total, next_offset, filter, query)

    async def iter_members(self, chat_id, *, limit=100, page_size=100, filter='recent', query=''):
        self._page_options(0, page_size, filter, query)
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Use a total member limit of 1..10000')
        owner = self._owner()
        resolved = await self.client.resolve_peer(chat_id)
        seen, offset = set(), 0
        while len(seen) < limit:
            self._check(owner)
            page = await self.get_members_page(resolved, offset=offset, limit=min(page_size, limit-len(seen)), filter=filter, query=query)
            self._check(owner)
            fresh = [m for m in page.members if m.peer.key not in seen]
            if page.members and not fresh:
                raise TLCodecError('Member pagination repeated a page without progress')
            for member in fresh:
                seen.add(member.peer.key)
                yield member
            if page.next_offset is None:
                break
            if page.next_offset <= offset or page.next_offset >= 2**31:
                raise TLCodecError('Member pagination failed to advance')
            offset = page.next_offset

    async def get_member(self, chat_id, participant='self') -> AccountMember | None:
        owner = self._owner()
        resolved = await self.client.resolve_peer(chat_id)
        target = await self.client.resolve_peer(participant)
        if resolved.kind == PeerKind.USER:
            raise ValueError('A user has no members')
        if resolved.kind == PeerKind.CHAT:
            _, members = await self._full(resolved)
            self._check(owner)
            if members is None:
                raise UnsupportedOperationError('Group member list is unavailable to this account')
            return next((m for m in members if m.peer.key == target.key), None)
        async with self.client._lock:
            self._check(owner)
            try:
                value = (await self.client._read_rpc('channels.getParticipant', dict(
                    channel=self._input_channel(resolved), participant=target.to_input_peer()))).result
            except AccountRPCError as exc:
                if exc.rpc_type == 'USER_NOT_PARTICIPANT':
                    return None
                raise
            if not isinstance(value, dict) or value.get('_') != 'channels.channelParticipant':
                raise TLCodecError('Unexpected channel participant constructor')
            entities, peers = self._entities(value)
            raw = value.get('participant')
            if not isinstance(raw, dict) or not isinstance(raw.get('_'), str) or raw['_'] not in CHANNEL_PARTICIPANTS:
                raise TLCodecError('Unexpected channel participant type')
            member = parse_member(raw, peers)
            if member.peer.key != target.key:
                raise TLCodecError('Participant response does not match requested identity')
            self_membership = raw['_'] == 'channelParticipantSelf'
            if self_membership and member.peer.key != (PeerKind.USER, owner[0]):
                raise TLCodecError('Self participant response does not match the active account')
            self.client._cache_entities(entities)
        if not self_membership:
            return member
        # Eitaa returns channelParticipantSelf even for the channel's creator.
        # That constructor proves membership, not an ordinary-member role.
        # Use authoritative current-account flags from the same channel profile.
        info = await self.get_full_channel(resolved)
        self._check(owner)
        if info.is_creator:
            return replace(member, role='owner', admin_rights=info.admin_rights, role_source='channel_profile')
        if info.admin_rights:
            return replace(member, role='admin', admin_rights=info.admin_rights, role_source='channel_profile')
        return member
