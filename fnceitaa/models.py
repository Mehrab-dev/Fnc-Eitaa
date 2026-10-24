from dataclasses import dataclass, field
from typing import Optional, Any

from fnceitaa.enums import PeerKind, MediaKind


@dataclass
class Peer:
    id: int
    access_token: Optional[int] = None
    kind: PeerKind = PeerKind.USER
    username: Optional[str] = None


@dataclass
class Media:
    kind: MediaKind
    file_path_or_id: str
    caption: Optional[str] = None


@dataclass
class Message:
    id: int
    peer: Peer
    text: Optional[str] = None
    reply_to_msg_id: Optional[int] = None
    media: Optional[Media] = None
    raw: dict = field(default_factory=dict)
    _client: Any = field(default=None, repr=False)

    async def reply(self, text: str) -> "Message":
        if not self._client:
            raise RuntimeError("Client is not bound to message instance")
        return await self._client.send_message(
            chat_id=self.peer,
            text=text,
            reply_to_msg_id=self.id
        )

    async def delete(self) -> bool:
        if not self._client:
            raise RuntimeError("Client is not bound to message instance")
        return await self._client.delete_messages(
            chat_id=self.peer,
            message_ids=[self.id]
        )


@dataclass
class Event:
    name: str
    message: Message
