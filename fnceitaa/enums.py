from enum import Enum


class PeerKind(str, Enum):
    USER = "user"
    CHAT = "chat"
    CHANNEL = "channel"


class MediaKind(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"