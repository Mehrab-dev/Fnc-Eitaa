import asyncio
import logging
import math
from pathlib import Path
from typing import List, Optional, Union
from .transport import EitaaBackend as Eitaa
from .models import Peer, Message, Media
from .enums import PeerKind, MediaKind
from .router import Router
from .errors import APIRequestError, PeerResolveError, UnsupportedOperationError

logger = logging.getLogger(__name__)
PeerLike = Union[Peer, int, str]


class Client:
    """Eitaayar sending and public-page reading; account login is not supported yet."""

    def __init__(self, token: Optional[str] = None, session_name: str = 'default',
                 router: Optional[Router] = None):
        self.token = token
        self.session_name = session_name
        self.router = router if router is not None else Router()
        self.is_running = False
        self._engine: Optional[Eitaa] = None

    async def connect(self):
        self._engine = Eitaa(self.token) if self.token else None
        return self

    async def disconnect(self):
        self.stop_polling()
        self._engine = None

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, exc_type, exc, traceback):
        await self.disconnect()

    def stop_polling(self):
        self.is_running = False

    def _resolve_peer(self, peer: PeerLike) -> Peer:
        if isinstance(peer, Peer):
            if not peer.username and not peer.id:
                raise PeerResolveError('Peer needs an ID or username')
            if peer.username:
                return Peer(id=peer.id, username=self._resolve_peer(peer.username).username,
                            kind=peer.kind, access_token=peer.access_token)
            return peer
        if isinstance(peer, int) and not isinstance(peer, bool) and peer != 0:
            return Peer(id=peer)
        if isinstance(peer, str):
            username = peer.strip().lstrip('@')
            if username and all(c.isascii() and (c.isalnum() or c == '_') for c in username):
                return Peer(id=0, username=username, kind=PeerKind.CHANNEL)
        raise PeerResolveError('Use a nonzero numeric ID, username, or Peer')

    def _public_peer(self, peer):
        resolved = self._resolve_peer(peer)
        if not resolved.username:
            raise PeerResolveError('Public-page reading requires a username, not a numeric chat ID')
        return resolved

    def _require_engine(self):
        if self._engine is None:
            raise APIRequestError('Sending requires an Eitaayar token and await client.connect()')
        return self._engine

    @staticmethod
    def _validate_reply_id(reply_to_msg_id: Optional[int]) -> None:
        if reply_to_msg_id is not None and (
            isinstance(reply_to_msg_id, bool)
            or not isinstance(reply_to_msg_id, int)
            or reply_to_msg_id <= 0
        ):
            raise ValueError('reply_to_msg_id must be a positive integer or None')

    @staticmethod
    async def _call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except Exception as exc:
            # Request exceptions can contain token-bearing URLs.
            raise APIRequestError(f'Eitaa request failed ({type(exc).__name__})') from None

    def _sent_message(self, res, peer, text, reply_to=None, media=None):
        if not isinstance(res, dict) or res.get('ok') is not True:
            raise APIRequestError('Eitaa did not confirm the send')
        result = res.get('result')
        msg_id = result.get('message_id') if isinstance(result, dict) else None
        if isinstance(msg_id, bool) or not isinstance(msg_id, int) or msg_id <= 0:
            raise APIRequestError('Send has no valid message_id; outcome uncertain, do not blindly retry')
        return Message(id=msg_id, peer=peer, text=text, media=media,
                       reply_to_msg_id=reply_to, raw=res, _client=self)

    def _public_message(self, res, peer: Peer, message_id: Optional[int] = None) -> Message:
        if not isinstance(res, dict):
            raise APIRequestError('Invalid public message response')
        msg_id = message_id if message_id is not None else res.get('message_number')
        if isinstance(msg_id, bool) or not isinstance(msg_id, (int, str)):
            raise APIRequestError('Invalid message ID')
        try:
            msg_id = int(msg_id)
        except (TypeError, ValueError):
            raise APIRequestError('Public message has no valid ID') from None
        if msg_id <= 0:
            raise APIRequestError('Invalid message ID')
        media = Media(MediaKind.PHOTO, res['image_link']) if res.get('image_link') else None
        return Message(id=msg_id, peer=peer, text=res.get('message_text', res.get('text', '')),
                       media=media, raw=res, _client=self)

    async def get_chat_info(self, peer: PeerLike) -> dict:
        resolved = self._public_peer(peer)
        res = await self._call(Eitaa.get_info, resolved.username)
        if not isinstance(res, dict) or not res or res.get('ok') is False:
            raise APIRequestError('No public chat information returned')
        return res

    async def get_info(self, peer: PeerLike) -> dict:
        return await self.get_chat_info(peer)

    async def get_message(self, peer: PeerLike, message_id: int) -> Optional[Message]:
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise ValueError('message_id must be a positive integer')
        resolved = self._public_peer(peer)
        res = await self._call(Eitaa.get_message, resolved.username, message_id)
        if res is None:
            return None
        if not isinstance(res, dict) or not res:
            raise APIRequestError('Invalid public message response')
        return self._public_message(res, resolved, message_id)

    async def send_message(self, peer: PeerLike, text: str,
                           reply_to_msg_id: Optional[int] = None) -> Message:
        engine = self._require_engine()
        resolved = self._resolve_peer(peer)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('text must be a nonempty string')
        self._validate_reply_id(reply_to_msg_id)
        res = await self._call(engine.send_message, chat_id=resolved.username or resolved.id,
                               text=text, reply_to_message_id=reply_to_msg_id)
        return self._sent_message(res, resolved, text, reply_to_msg_id)

    async def send_file(self, peer: PeerLike, file_path: str, caption: Optional[str] = None,
                        reply_to_msg_id: Optional[int] = None) -> Message:
        engine = self._require_engine()
        resolved = self._resolve_peer(peer)
        self._validate_reply_id(reply_to_msg_id)
        if caption is not None and not isinstance(caption, str):
            raise ValueError('caption must be a string or None')
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        res = await self._call(engine.send_file, chat_id=resolved.username or resolved.id,
                               file=str(path), caption=caption or '',
                               reply_to_message_id=reply_to_msg_id)
        return self._sent_message(res, resolved, caption or '', reply_to_msg_id,
                                  Media(MediaKind.DOCUMENT, str(path), caption=caption))

    async def delete_messages(self, peer: PeerLike, message_ids: List[int]) -> bool:
        raise UnsupportedOperationError('Delete requires the future account transport')

    async def run_polling(self, targets: Union[str, List[str]], interval: float = 2.0):
        """Emit unseen messages on public pages, oldest first, starting after the initial snapshot.

        Failed handlers retain the checkpoint; handlers must tolerate retries.
        This is not private history and cannot report edit/delete events.
        """
        if self.is_running:
            raise RuntimeError('Polling is already running')
        if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('interval must be finite and positive')
        targets = [targets] if isinstance(targets, str) else list(targets)
        peers = {}
        for target in targets:
            peer = self._public_peer(target)
            peers[peer.username] = peer
        if not peers:
            raise ValueError('At least one target is required')
        last_seen = {}
        self.is_running = True
        try:
            while self.is_running:
                for username, peer in peers.items():
                    if not self.is_running:
                        break
                    try:
                        raw = await self._call(Eitaa.get_latest_messages, username)
                        if not isinstance(raw, list):
                            raise APIRequestError('Invalid public history response')
                        messages = sorted((self._public_message(item, peer) for item in raw), key=lambda m: m.id)
                        if not messages:
                            continue
                        if username not in last_seen:
                            last_seen[username] = messages[-1].id
                            continue
                        for msg in messages:
                            if not self.is_running:
                                break
                            if msg.id > last_seen[username]:
                                await self.router.emit('message', msg)
                                last_seen[username] = msg.id
                    except Exception as exc:
                        logger.warning('Public polling failed for %s (%s)', username, type(exc).__name__)
                if self.is_running:
                    await asyncio.sleep(interval)
        finally:
            self.is_running = False