"""Automatically protected .eitaa credentials with atomic replacement."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .errors import SessionError
from .session_crypto import SessionProtection

MAX_SESSION_BYTES = 32768
MAX_FILE_BYTES = 65536


def session_path(path):
    path = Path(path).absolute()
    if path.suffix.lower() == '.session':
        return path.with_suffix('.eitaa')
    return path if path.suffix.lower() == '.eitaa' else Path(str(path) + '.eitaa')


def _parse(data):
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != set(AccountSession.__dataclass_fields__):
            raise ValueError
        return AccountSession(**value)
    except (TypeError, ValueError, UnicodeError):
        raise SessionError('Malformed or unsupported account session') from None


@dataclass(frozen=True)
class AccountSession:
    token: str = field(repr=False)
    device_id: str = field(repr=False)
    user_id: int
    endpoint: str
    api_id: int
    version: int = 1

    def __post_init__(self):
        if (type(self.version) is not int or self.version != 1
                or type(self.user_id) is not int or not 0 < self.user_id < 2**63
                or type(self.api_id) is not int or not 0 < self.api_id < 2**31
                or not isinstance(self.token, str) or not 0 < len(self.token) <= 16384
                or not isinstance(self.device_id, str) or not 0 < len(self.device_id) <= 128
                or not isinstance(self.endpoint, str) or len(self.endpoint) > 256):
            raise SessionError('Invalid account session fields')


class SessionStore:
    """One active writer per file. Windows files inherit the parent's ACL.

    Credentials use automatic OS protection, without a password prompt.
    Synchronous small writes keep
    cancellation from interrupting the replace/commit boundary. An observed file
    change is rejected, but this is not a multiprocess locking mechanism.
    """

    def __init__(self, path):
        self.path = session_path(path)
        self._protection = SessionProtection()
        self._digest = None
        self._loaded = False

    def _read(self):
        try:
            if self.path.is_symlink():
                raise SessionError('Session path must not be a symbolic link')
            with self.path.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise SessionError('Session path must be a regular file')
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise SessionError('Account session exceeds size limit')
            return data
        except FileNotFoundError:
            return None
        except OSError:
            raise SessionError('Cannot read account session') from None

    def load(self):
        self._loaded = False
        data = self._read()
        if data is None:
            if self.path.with_suffix('.session').exists():
                raise SessionError('Legacy plaintext session found; run '
                                   'python -m fnceitaa.session_migrate SOURCE.session')
            self._protection.prepare()
            self._digest = None
            self._loaded = True
            return None
        session = _parse(self._protection.open(data))
        self._digest = hashlib.sha256(data).digest()
        self._loaded = True
        return session

    def save(self, session: AccountSession):
        if not self._loaded:
            raise SessionError('Load the session before saving')
        current = self._read()
        digest = hashlib.sha256(current).digest() if current is not None else None
        if digest != self._digest:
            raise SessionError('Session changed outside this client; refusing overwrite')
        data = (json.dumps(asdict(session), ensure_ascii=True, sort_keys=True) + '\n').encode()
        if len(data) > MAX_SESSION_BYTES:
            raise SessionError('Account session exceeds size limit')
        data = self._protection.seal(data)
        if len(data) > MAX_FILE_BYTES:
            raise SessionError('Protected account session exceeds size limit')
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.' + self.path.name + '.', suffix='.tmp',
                                             dir=self.path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._digest = hashlib.sha256(data).digest()
        except OSError:
            raise SessionError('Cannot persist account session; login was not committed locally') from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


def migrate_legacy_session(source, destination=None):
    """Protect legacy plaintext offline, retaining the source and existing files."""
    source = Path(source).absolute()
    destination = session_path(destination if destination is not None else source.with_suffix('.eitaa'))
    if source.resolve() == destination.resolve():
        raise SessionError('Source and destination must differ')
    if destination.exists() or destination.is_symlink():
        raise SessionError('Destination already exists; refusing overwrite')
    # Reuse the bounded file reader without interpreting the old extension.
    reader = SessionStore(destination)
    reader.path = source
    data = reader._read()
    if data is None:
        raise SessionError('Legacy session file not found')
    if len(data) > MAX_SESSION_BYTES:
        raise SessionError('Legacy account session exceeds size limit')
    session = _parse(data)
    store = SessionStore(destination)
    store._protection.prepare()
    store._loaded = True
    store.save(session)
    return destination
