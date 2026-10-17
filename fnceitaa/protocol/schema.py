"""Pinned schema indices with explicit upstream duplicate handling."""
import json
from importlib.resources import files

from ..errors import FncEitaaError


class TLCodecError(FncEitaaError):
    """Invalid, unsupported, truncated or excessive wire data."""


class Schema:
    def __init__(self, document=None):
        if document is None:
            document = json.loads(files(__package__).joinpath('schema.json').read_text(encoding='utf-8'))
        self.layer = document['schema']['layer']
        self.source_sha256 = document['metadata']['source_sha256']
        self.methods = {}
        self.constructors = {}
        self.names = {}
        self.types = {}
        for namespace in ('API', 'MTProto'):
            self.methods[namespace] = {}
            self.constructors[namespace] = {}
            self.names[namespace] = {}
            self.types[namespace] = {}
            for entry in document['schema'][namespace]['methods']:
                # The upstream encoder uses first matching method/name.
                self.methods[namespace].setdefault(entry['method'], entry)
            for entry in document['schema'][namespace]['constructors']:
                if 'predicate' not in entry:
                    continue  # Known malformed advertising entries, not guessed repairs.
                self.names[namespace].setdefault(entry['predicate'], entry)
                # The upstream decoder's ID index keeps the last entry.
                self.constructors[namespace][entry['id'] & 0xffffffff] = entry
                self.types[namespace].setdefault(entry['type'], []).append(entry)

    def method(self, name, namespace='API'):
        try:
            return self.methods[namespace][name]
        except KeyError:
            raise TLCodecError('Unknown TL method or namespace') from None

    def constructor(self, identifier, namespace):
        order = ('MTProto', 'API') if namespace == 'MTProto' else ('API',)
        if namespace not in self.constructors:
            raise TLCodecError('Unknown TL namespace')
        for candidate in order:
            entry = self.constructors[candidate].get(identifier)
            if entry is not None:
                return entry, candidate
        raise TLCodecError(f'Unknown TL constructor 0x{identifier:08x}')

    def bare(self, kind, namespace):
        entries = self.types.get(namespace, {}).get(kind, [])
        unique = {entry['id']: entry for entry in entries}
        if len(unique) != 1:
            raise TLCodecError('Bare TL type is unknown or ambiguous')
        return next(iter(unique.values()))
