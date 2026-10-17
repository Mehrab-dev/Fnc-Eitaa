"""Versioned account credentials. Atomic replacement, never OTP/password storage."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .errors import SessionError

MAX_SESSION_BYTES = 32768


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

    Credentials are plaintext JSON, not encrypted. Synchronous small writes keep
    cancellation from interrupting the replace/commit boundary. An observed file
    change is rejected, but this is not a multiprocess locking mechanism.
    """

    def __init__(self, path):
        self.path = Path(path).absolute()
        self._digest = None
        self._loaded = False

    def _read(self):
        try:
            if self.path.is_symlink():
                raise SessionError('Session path must not be a symbolic link')
            with self.path.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise SessionError('Session path must be a regular file')
                data = stream.read(MAX_SESSION_BYTES + 1)
            if len(data) > MAX_SESSION_BYTES:
                raise SessionError('Account session exceeds size limit')
            return data
        except FileNotFoundError:
            return None
        except OSError:
            raise SessionError('Cannot read account session') from None

    def load(self):
        data = self._read()
        if data is None:
            self._digest = None
            self._loaded = True
            return None
        try:
            value = json.loads(data)
            if not isinstance(value, dict) or set(value) != set(AccountSession.__dataclass_fields__):
                raise ValueError
            session = AccountSession(**value)
        except (TypeError, ValueError, UnicodeError):
            raise SessionError('Malformed or unsupported account session') from None
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
