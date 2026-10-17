"""Bounded TL serialization for the pinned Eitaa account protocol."""
from dataclasses import dataclass, field
import re
import struct
from typing import Any, Mapping
import zlib

from .schema import Schema, TLCodecError

VECTOR = 0x1cb5c415
GZIP = 0x3072cfa1
RPC_RESULT = 0xf35c6d01
CONTAINER = 0x73f1f8dc
BOOL_TRUE = 0x997275b5
BOOL_FALSE = 0xbc799737
ENVELOPES = {RPC_RESULT, CONTAINER, 0x2144ca19, 0xc4b9f9bb, 0xfffffe6c,
             0x347773c5, 0xdc252379, 0xdc252375, 0xa56c1a31}
VECTOR_TYPE = re.compile(r'(Vector|vector)<(.+)>\Z')
OPTIONAL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\.(\d+)\?(.+)\Z')


@dataclass(frozen=True)
class Limits:
    max_payload_bytes: int = 8 * 1024 * 1024
    max_blob_bytes: int = 4 * 1024 * 1024
    max_vector_items: int = 100_000
    max_depth: int = 64
    max_nodes: int = 200_000
    max_decompressed_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        for value in vars(self).values():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError('Codec limits must be positive integers')


@dataclass(frozen=True)
class TLCall:
    method: str
    params: Mapping[str, Any] = field(default_factory=dict, repr=False)


class _Budget:
    def __init__(self, limits):
        self.limits = limits
        self.nodes = 0
        self.expanded = 0
        # Structural diagnostics only: never retain field values or wire bytes.
        self.structures = []

    def visit(self, depth):
        self.nodes += 1
        if depth > self.limits.max_depth or self.nodes > self.limits.max_nodes:
            raise TLCodecError('TL nesting or node budget exceeded')


class _Writer:
    def __init__(self, budget):
        self.budget = budget
        self.data = bytearray()

    def put(self, data):
        if len(self.data) + len(data) > self.budget.limits.max_payload_bytes:
            raise TLCodecError('Encoded payload exceeds limit')
        self.data.extend(data)

    def integer(self, value, bits=32, unsigned=False):
        low, high = (0, 2 ** bits - 1) if unsigned else (-2 ** (bits - 1), 2 ** (bits - 1) - 1)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise TLCodecError('TL integer is outside its wire range')
        self.put(value.to_bytes(bits // 8, 'little', signed=not unsigned))

    def blob(self, value):
        size = len(value)
        if size > min(0xffffff, self.budget.limits.max_blob_bytes):
            raise TLCodecError('TL bytes/string exceeds limit')
        prefix = bytes([size]) if size <= 253 else b'\xfe' + size.to_bytes(3, 'little')
        self.put(prefix)
        self.put(value)
        self.put(b'\0' * (-(len(prefix) + size) % 4))


class _Reader:
    def __init__(self, data, budget):
        if len(data) > budget.limits.max_payload_bytes:
            raise TLCodecError('Response payload exceeds limit')
        self.data = memoryview(data)
        self.offset = 0
        self.budget = budget

    def take(self, count):
        if count < 0 or self.offset + count > len(self.data):
            raise TLCodecError('Truncated TL data')
        value = self.data[self.offset:self.offset + count].tobytes()
        self.offset += count
        return value

    def integer(self, bits=32, unsigned=False):
        return int.from_bytes(self.take(bits // 8), 'little', signed=not unsigned)

    def peek_id(self):
        if len(self.data) - self.offset < 4:
            raise TLCodecError('Truncated TL constructor')
        return struct.unpack_from('<I', self.data, self.offset)[0]

    def blob(self):
        size = self.take(1)[0]
        header = 1
        if size == 254:
            size, header = int.from_bytes(self.take(3), 'little'), 4
            if size <= 253:
                raise TLCodecError('Noncanonical TL length')
        elif size == 255:
            raise TLCodecError('Invalid TL length prefix')
        if size > self.budget.limits.max_blob_bytes:
            raise TLCodecError('TL bytes/string exceeds limit')
        value = self.take(size)
        if any(self.take(-(header + size) % 4)):
            raise TLCodecError('Invalid TL padding')
        return value

    def finish(self):
        if self.offset != len(self.data):
            structures = '; '.join(
                name + '@' + str(offset) + ''.join(
                    f' {mask}=0x{value:08x}' for mask, value in masks.items())
                for name, offset, masks in self.budget.structures)
            raise TLCodecError(
                f'Unexpected trailing TL data (read={self.offset}, '
                f'total={len(self.data)}, remaining={len(self.data) - self.offset}; '
                f'constructors: {structures or "none"})')


class TLCodec:
    def __init__(self, schema=None, limits=None):
        self.schema = schema if schema is not None else Schema()
        self.limits = limits if limits is not None else Limits()

    def encode_method(self, method, params=None, *, namespace='API') -> bytes:
        writer = _Writer(_Budget(self.limits))
        self._call(writer, TLCall(method, params if params is not None else {}), namespace, 0)
        return bytes(writer.data)

    def result_type(self, method, params=None, *, namespace='API') -> str:
        """Resolve a generic wrapper's return type from its nested query."""
        for _ in range(self.limits.max_depth):
            entry = self.schema.method(method, namespace)
            if entry['type'] != 'X':
                return entry['type']
            query = params.get('query') if isinstance(params, Mapping) else None
            if not isinstance(query, TLCall):
                raise TLCodecError('Generic result requires a nested TLCall')
            method, params = query.method, query.params
        raise TLCodecError('Generic query nesting exceeds limit')

    def encode(self, value, kind='Object', *, namespace='API') -> bytes:
        writer = _Writer(_Budget(self.limits))
        self._write(writer, value, kind, namespace, 0)
        return bytes(writer.data)

    def decode(self, data: bytes, kind='Object', *, namespace='MTProto', result_types=None):
        if not isinstance(data, bytes):
            raise TLCodecError('TL input must be bytes')
        reader = _Reader(data, _Budget(self.limits))
        value = self._read(reader, kind, namespace, 0, result_types or {})
        reader.finish()
        return value

    def decode_authorization_prefix(self, data: bytes):
        """Read a direct Eitaa login authorization as the web client does.

        Returns (candidate, unparsed_byte_count). The suffix is opaque, not
        presumed padding. Callers MUST verify the token with users.getUsers
        before reporting an authenticated account. General decode stays strict.
        Envelopes, gzip, errors and other constructors do not use this exception.
        """
        if not isinstance(data, bytes):
            raise TLCodecError('TL input must be bytes')
        reader = _Reader(data, _Budget(self.limits))
        entry = self.schema.names['API']['auth.authorization']
        if reader.peek_id() != entry['id'] & 0xffffffff:
            raise TLCodecError('Expected direct Eitaa login authorization')
        value = self._read(reader, 'auth.Authorization', 'API', 0, {})
        user, token = value.get('user'), value.get('token')
        if (not isinstance(token, str) or not 0 < len(token) <= 16384
                or not isinstance(user, dict) or user.get('_') != 'user'
                or type(user.get('id')) is not int or user['id'] <= 0
                or not user.get('pFlags', {}).get('self')):
            raise TLCodecError('Invalid direct Eitaa login authorization')
        return value, len(data) - reader.offset

    def _call(self, writer, call, namespace, depth):
        writer.budget.visit(depth)
        entry = self.schema.method(call.method, namespace)
        writer.integer(entry['id'] & 0xffffffff, unsigned=True)
        self._body(writer, call.params, entry, namespace, depth + 1)

    def _body(self, writer, value, entry, namespace, depth):
        if not isinstance(value, Mapping):
            raise TLCodecError('TL fields must be a mapping')
        names = {p['name'] for p in entry['params']}
        if set(value) - names - {'_', 'pFlags'}:
            raise TLCodecError('Unknown TL field')
        boolean_names = {p['name'] for p in entry['params'] if p['type'].endswith('?true')}
        pflags = value.get('pFlags', {})
        if not isinstance(pflags, Mapping) or set(pflags) - boolean_names:
            raise TLCodecError('Invalid optional boolean flags')
        masks = {p['name']: 0 for p in entry['params'] if p['type'] == '#'}
        groups = {}
        enabled = {}
        for param in entry['params']:
            match = OPTIONAL.fullmatch(param['type'])
            if not match:
                continue
            mask, bit, base = match.groups()
            bit = int(bit)
            if mask not in masks or not 0 <= bit < 32:
                raise TLCodecError('Invalid optional flag schema')
            name = param['name']
            if base == 'true':
                present = value.get(name, pflags.get(name, False))
                if not isinstance(present, bool):
                    raise TLCodecError('Optional boolean must be bool')
                if name in value and name in pflags and value[name] != pflags[name]:
                    raise TLCodecError('Conflicting optional boolean flags')
            else:
                present = name in value and value[name] is not None
            enabled[name] = present
            groups.setdefault((mask, bit), []).append(present)
            if present:
                masks[mask] |= 1 << bit
        if any(any(group) and not all(group) for group in groups.values()):
            raise TLCodecError('Fields sharing a flag bit must be supplied together')
        for mask, computed in masks.items():
            if mask in value and (isinstance(value[mask], bool) or value[mask] != computed):
                raise TLCodecError('Explicit flags disagree with optional fields')
        for param in entry['params']:
            name, kind = param['name'], param['type']
            if kind == '#':
                writer.integer(masks[name], unsigned=True)
                continue
            optional = OPTIONAL.fullmatch(kind)
            if optional:
                if not enabled[name]:
                    continue
                kind = optional.group(3)
                if kind == 'true':
                    continue
            if name not in value:
                raise TLCodecError('Missing required TL field')
            self._write(writer, value[name], kind, namespace, depth)

    def _write(self, writer, value, kind, namespace, depth):
        writer.budget.visit(depth)
        if kind in ('int', '#', 'long'):
            writer.integer(value, 64 if kind == 'long' else 32, unsigned=kind == '#')
        elif kind == 'double':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TLCodecError('TL double must be numeric')
            try:
                writer.put(struct.pack('<d', value))
            except (OverflowError, struct.error):
                raise TLCodecError('Invalid TL double') from None
        elif kind in ('int128', 'int256', 'int512'):
            if not isinstance(value, bytes) or len(value) != int(kind[3:]) // 8:
                raise TLCodecError('Fixed-width TL integer must have exact byte length')
            writer.put(value)
        elif kind in ('string', 'bytes'):
            if kind == 'string':
                if not isinstance(value, str):
                    raise TLCodecError('TL string must be text')
                if len(value) > self.limits.max_blob_bytes:
                    raise TLCodecError('TL string exceeds limit')
                try:
                    value = value.encode('utf-8')
                except UnicodeEncodeError:
                    raise TLCodecError('Invalid UTF-8 text') from None
            if not isinstance(value, bytes):
                raise TLCodecError('TL bytes must be bytes')
            writer.blob(value)
        elif kind == 'Bool':
            if not isinstance(value, bool):
                raise TLCodecError('TL Bool must be bool')
            writer.integer(BOOL_TRUE if value else BOOL_FALSE, unsigned=True)
        elif kind == 'true':
            if value is not True:
                raise TLCodecError('TL true must be True')
        elif kind == '!X':
            if not isinstance(value, TLCall):
                raise TLCodecError('Generic query requires a TLCall')
            self._call(writer, value, namespace, depth + 1)
        elif (vector := VECTOR_TYPE.fullmatch(kind)):
            if not isinstance(value, (list, tuple)) or len(value) > self.limits.max_vector_items:
                raise TLCodecError('Invalid or excessive TL vector')
            if vector.group(1) == 'Vector':
                writer.integer(VECTOR, unsigned=True)
            writer.integer(len(value))
            for item in value:
                self._write(writer, item, vector.group(2), namespace, depth + 1)
        else:
            if not isinstance(value, Mapping) or not isinstance(value.get('_'), str):
                raise TLCodecError('TL object needs a constructor name')
            entry = self.schema.names.get(namespace, {}).get(value['_'])
            if entry is None and namespace == 'MTProto':
                entry = self.schema.names['API'].get(value['_'])
                if entry is not None:
                    namespace = 'API'
            expected = kind.removeprefix('%')
            if entry is None or (expected != 'Object' and expected not in (entry['type'], entry['predicate'])):
                raise TLCodecError('Constructor does not match expected TL type')
            if not kind.startswith('%'):
                writer.integer(entry['id'] & 0xffffffff, unsigned=True)
            if namespace == 'MTProto' and entry['predicate'] == 'message':
                body_writer = _Writer(writer.budget)
                self._write(body_writer, value.get('body'), 'Object', namespace, depth + 1)
                size = len(body_writer.data)
                if 'bytes' in value and value['bytes'] != size:
                    raise TLCodecError('Container message byte length mismatch')
                if set(value) - {'_', 'msg_id', 'seqno', 'bytes', 'body'}:
                    raise TLCodecError('Unknown container message field')
                self._write(writer, value.get('msg_id'), 'long', namespace, depth + 1)
                self._write(writer, value.get('seqno'), 'int', namespace, depth + 1)
                writer.integer(size)
                writer.put(body_writer.data)
            else:
                self._body(writer, value, entry, namespace, depth + 1)

    def _unpack(self, reader, kind, namespace, depth, result_types):
        reader.integer(unsigned=True)  # gzip constructor already identified
        packed = reader.blob()
        remaining = self.limits.max_decompressed_bytes - reader.budget.expanded
        try:
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = inflater.decompress(packed, remaining + 1)
            if len(data) > remaining or not inflater.eof or inflater.unused_data:
                raise TLCodecError('Excessive or invalid gzip TL payload')
        except zlib.error:
            raise TLCodecError('Invalid gzip TL payload') from None
        reader.budget.expanded += len(data)
        inner = _Reader(data, reader.budget)
        value = self._read(inner, kind, namespace, depth + 1, result_types)
        inner.finish()
        return value

    def _read(self, reader, kind, namespace, depth, result_types):
        reader.budget.visit(depth)
        if kind in ('int', '#', 'long'):
            return reader.integer(64 if kind == 'long' else 32, unsigned=kind == '#')
        if kind == 'double':
            return struct.unpack('<d', reader.take(8))[0]
        if kind in ('int128', 'int256', 'int512'):
            return reader.take(int(kind[3:]) // 8)
        if kind in ('string', 'bytes'):
            data = reader.blob()
            if kind == 'bytes':
                return data
            try:
                return data.decode('utf-8')
            except UnicodeDecodeError:
                raise TLCodecError('Invalid TL UTF-8') from None
        if kind == 'true':
            return True
        if kind == '!X':
            raise TLCodecError('Generic query decoding is not a response operation')
        bare = kind.startswith('%') or kind.startswith('vector<')
        identifier = None if bare else reader.peek_id()
        if identifier == GZIP:
            return self._unpack(reader, kind, namespace, depth, result_types)
        # Outer protocol control/error wrappers are legal around a typed result.
        if identifier in ENVELOPES:
            return self._object(reader, 'Object', 'MTProto', depth, result_types)
        if (vector := VECTOR_TYPE.fullmatch(kind)):
            if vector.group(1) == 'Vector' and reader.integer(unsigned=True) != VECTOR:
                raise TLCodecError('Invalid vector constructor')
            count = reader.integer()
            if not 0 <= count <= self.limits.max_vector_items:
                raise TLCodecError('Invalid or excessive TL vector count')
            return [self._read(reader, vector.group(2), namespace, depth + 1, result_types)
                    for _ in range(count)]
        if kind == 'Bool':
            identifier = reader.integer(unsigned=True)
            if identifier not in (BOOL_TRUE, BOOL_FALSE):
                raise TLCodecError('Invalid TL Bool')
            return identifier == BOOL_TRUE
        if identifier == VECTOR:
            raise TLCodecError('Vector response requires an expected element type')
        return self._object(reader, kind, namespace, depth, result_types)

    def _object(self, reader, kind, namespace, depth, result_types):
        start = reader.offset
        if kind.startswith('%'):
            entry = self.schema.bare(kind[1:], namespace)
        else:
            entry, namespace = self.schema.constructor(reader.integer(unsigned=True), namespace)
        name = entry['predicate']
        masks = {}
        reader.budget.structures.append((name, start, masks))
        del reader.budget.structures[:-6]
        # Eitaa's login page calls account.getPassword (declared account.Password)
        # but explicitly handles the legacy account.password2 constructor too.
        legacy_password = kind == 'account.Password' and name == 'account.password2'
        if kind != 'Object' and not legacy_password and kind.removeprefix('%') not in (entry['type'], name):
            raise TLCodecError('Unexpected TL response type')
        result = {'_': name}
        if namespace == 'MTProto' and name == 'message':
            result['msg_id'] = reader.integer(64)
            result['seqno'] = reader.integer()
            size = reader.integer()
            if size < 0 or size % 4:
                raise TLCodecError('Invalid container body size')
            child = _Reader(reader.take(size), reader.budget)
            result['bytes'] = size
            result['body'] = self._read(child, 'Object', namespace, depth + 1, result_types)
            child.finish()
            return result
        for param in entry['params']:
            field_name, field_type = param['name'], param['type']
            optional = OPTIONAL.fullmatch(field_type)
            if optional:
                mask, bit, field_type = optional.groups()
                if not result.get(mask, 0) & (1 << int(bit)):
                    continue
                if field_type == 'true':
                    result.setdefault('pFlags', {})[field_name] = True
                    continue
            if name == 'rpc_result' and field_name == 'result' and namespace == 'MTProto':
                field_type = result_types.get(result['req_msg_id'], 'Object')
            result[field_name] = self._read(reader, field_type, namespace, depth + 1, result_types)
            if field_type == '#':
                masks[field_name] = result[field_name]
        return result
