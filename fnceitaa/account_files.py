"""Immutable account media metadata; never derive local paths from remote names."""
from dataclasses import dataclass, field
from pathlib import Path

from .enums import MediaKind
from .protocol import TLCodecError


@dataclass(frozen=True)
class AccountMediaInfo:
    kind: MediaKind
    id: int
    access_hash: int = field(repr=False)
    file_reference: bytes = field(repr=False)
    dc_id: int
    size: int
    mime_type: str
    filename: str = field(default='', repr=False)
    thumb_size: str = ''
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    cached_bytes: bytes | None = field(default=None, repr=False)

    def to_location(self):
        return {'_': 'inputPhotoFileLocation' if self.kind == MediaKind.PHOTO else 'inputDocumentFileLocation',
                'id': self.id, 'access_hash': self.access_hash, 'file_reference': self.file_reference,
                'thumb_size': self.thumb_size}

    def to_input_media(self):
        photo = self.kind == MediaKind.PHOTO
        return {'_': 'inputMediaPhoto' if photo else 'inputMediaDocument', 'id': {
            '_': 'inputPhoto' if photo else 'inputDocument', 'id': self.id,
            'access_hash': self.access_hash, 'file_reference': self.file_reference}}


@dataclass(frozen=True)
class UploadedFile:
    id: int
    parts: int
    name: str = field(repr=False)
    size: int
    big: bool
    md5_checksum: str = field(repr=False)
    _owner: tuple = field(repr=False)
    header: bytes = field(default=b'', repr=False)

    def to_input_file(self):
        value = {'_': 'inputFileBig' if self.big else 'inputFile',
                 'id': self.id, 'parts': self.parts, 'name': self.name}
        if not self.big:
            value['md5_checksum'] = self.md5_checksum
        return value


@dataclass(frozen=True)
class MediaItem:
    """An album item. Missing video metadata is read from its local file."""
    file: str | Path | bytes = field(repr=False)
    kind: MediaKind = MediaKind.PHOTO
    caption: str = field(default='', repr=False)
    filename: str | None = None
    mime_type: str | None = None
    duration: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    size: int
    sha256: str


def _integer(value, *, positive=False, long=False):
    low = 1 if positive else 0
    if type(value) is not int or not low <= value < 2**(63 if long else 31):
        raise TLCodecError('Invalid media integer')
    return value


def parse_media(value) -> AccountMediaInfo | None:
    if not isinstance(value, dict):
        return None
    photo = value.get('_') == 'messageMediaPhoto'
    if value.get('_') not in ('messageMediaPhoto', 'messageMediaDocument'):
        return None
    raw = value.get('photo' if photo else 'document')
    if raw is None or (isinstance(raw, dict) and raw.get('_') in ('photoEmpty', 'documentEmpty')):
        return None
    if not isinstance(raw, dict) or raw.get('_') != ('photo' if photo else 'document'):
        raise TLCodecError('Invalid account media constructor')
    identifier = _integer(raw.get('id'), positive=True, long=True)
    access_hash, reference = raw.get('access_hash'), raw.get('file_reference')
    if (type(access_hash) is not int or not -2**63 <= access_hash < 2**63
            or not isinstance(reference, bytes)):
        raise TLCodecError('Invalid media access reference')
    dc_id = _integer(raw.get('dc_id'))
    if photo:
        choices = []
        sizes_list = raw.get('sizes')
        if not isinstance(sizes_list, list):
            raise TLCodecError('Invalid photo sizes')
        for size in sizes_list:
            if not isinstance(size, dict):
                raise TLCodecError('Invalid photo size constructor')
            name = size.get('_')
            cached = None
            if name == 'photoSize':
                length = _integer(size.get('size'))
                if length == 0:
                    # Eitaa send acknowledgements can include a placeholder
                    # thumbnail alongside a complete downloadable size.
                    continue
            elif name == 'photoSizeProgressive':
                sizes = size.get('sizes')
                if not isinstance(sizes, list) or not sizes:
                    raise TLCodecError('Invalid progressive photo sizes')
                length = max(_integer(number, positive=True) for number in sizes)
            elif name == 'photoCachedSize':
                cached = size.get('bytes')
                if not isinstance(cached, bytes):
                    raise TLCodecError('Invalid cached photo bytes')
                if not cached:
                    # Eitaa can return an empty cached preview alongside real sizes.
                    # It contains no downloadable image; inspect the other sizes.
                    continue
                length = len(cached)
            else:
                continue  # Stripped previews are not complete downloadable files.
            thumb = size.get('type')
            if not isinstance(thumb, str) or not 1 <= len(thumb) <= 8:
                raise TLCodecError('Invalid photo size selector')
            choices.append((length, _integer(size.get('w'), positive=True),
                            _integer(size.get('h'), positive=True), thumb, cached))
        if not choices:
            return None
        length, width, height, thumb, cached = max(choices, key=lambda item: item[0])
        return AccountMediaInfo(MediaKind.PHOTO, identifier, access_hash, reference, dc_id,
                                length, 'image/jpeg', thumb_size=thumb,
                                width=width, height=height, cached_bytes=cached)
    kind, duration, width, height, filename = MediaKind.DOCUMENT, None, None, None, ''
    mime = raw.get('mime_type')
    if not isinstance(mime, str) or not mime or len(mime) > 255:
        raise TLCodecError('Invalid document MIME type')
    attributes = raw.get('attributes')
    if not isinstance(attributes, list):
        raise TLCodecError('Invalid document attributes')
    for attribute in attributes:
        if not isinstance(attribute, dict):
            raise TLCodecError('Invalid document attribute')
        name = attribute.get('_')
        if name == 'documentAttributeFilename':
            filename = attribute.get('file_name')
            if not isinstance(filename, str):
                raise TLCodecError('Invalid document filename')
        elif name == 'documentAttributeAudio':
            kind = MediaKind.VOICE if attribute.get('pFlags', {}).get('voice') else MediaKind.AUDIO
            duration = _integer(attribute.get('duration'))
        elif name == 'documentAttributeVideo':
            kind = MediaKind.VIDEO
            duration = _integer(attribute.get('duration'))
            width, height = _integer(attribute.get('w'), positive=True), _integer(attribute.get('h'), positive=True)
    return AccountMediaInfo(kind, identifier, access_hash, reference, dc_id,
                            _integer(raw.get('size')), mime, filename=filename,
                            duration=duration, width=width, height=height)
