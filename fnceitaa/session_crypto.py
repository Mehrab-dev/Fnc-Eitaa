"""Automatic OS-backed session protection, with no application password prompt."""
import base64
import ctypes
import os
import sys

from .errors import SessionError

MAGIC = b'FNCEITAA\x02'
WINDOWS = sys.platform == 'win32'
MAX_PLAINTEXT = 32768
SERVICE = 'fnceitaa.session.v2'


def _dpapi(data, *, decrypt=False):
    """Use current-user DPAPI; never request machine scope or interactive UI."""
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_buffer = ctypes.create_string_buffer(MAGIC)
    entropy = Blob(len(MAGIC), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                        0x01, ctypes.byref(output)):  # CRYPTPROTECT_UI_FORBIDDEN
            raise SessionError('Windows could not unlock/protect this session; '
                               'use the original Windows account and device, or log in with a new file')
        if output.size > (MAX_PLAINTEXT if decrypt else 65536):
            raise SessionError('Protected session exceeds size limit')
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            if decrypt:
                ctypes.memset(output.data, 0, output.size)
            kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def _keyring_backend():
    """Accept OS credential stores only; never use plaintext/file fallbacks."""
    try:
        import keyring
        selected = keyring.get_keyring()
        candidates = getattr(selected, 'backends', (selected,))
        allowed = {'keyring.backends.macOS', 'keyring.backends.SecretService',
                   'keyring.backends.kwallet', 'keyring.backends.libsecret'}
        for candidate in candidates:
            if type(candidate).__module__ in allowed and candidate.priority > 0:
                return candidate
    except Exception:
        pass
    raise SessionError('An unlocked OS credential store is required for automatic session protection; '
                       'configure macOS Keychain or Linux Secret Service/KWallet. No plaintext session was written')


class SessionProtection:
    """Windows DPAPI or AES-GCM with a random key in the OS credential store."""
    def __init__(self):
        self._key = None
        self._key_id = None

    def prepare(self):
        # Fail before asking Eitaa for an OTP if local protection is unavailable.
        if WINDOWS:
            _dpapi(b'fnceitaa-protection-check')
        else:
            self._vault_key()

    def _vault_key(self, identifier=None):
        if self._key is not None and (identifier is None or identifier == self._key_id):
            return self._key
        backend = _keyring_backend()
        try:
            if identifier is None:
                identifier = os.urandom(16)
                key = os.urandom(32)
                backend.set_password(SERVICE, identifier.hex(), base64.b64encode(key).decode('ascii'))
                # Verify persistence before creating a session that depends on it.
                saved = backend.get_password(SERVICE, identifier.hex())
                if saved != base64.b64encode(key).decode('ascii'):
                    raise ValueError
            else:
                saved = backend.get_password(SERVICE, identifier.hex())
                key = base64.b64decode(saved, validate=True) if isinstance(saved, str) else b''
            if len(key) != 32:
                raise ValueError
        except Exception:
            raise SessionError('Session key is unavailable in the OS credential store; '
                               'unlock the original store or log in with a new session file') from None
        self._key_id, self._key = identifier, key
        return key

    def seal(self, plaintext):
        if len(plaintext) > MAX_PLAINTEXT:
            raise SessionError('Account session exceeds size limit')
        if WINDOWS:
            return MAGIC + b'W' + _dpapi(plaintext)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = self._vault_key()
        nonce = os.urandom(12)
        header = MAGIC + b'K' + self._key_id + nonce
        return header + AESGCM(key).encrypt(nonce, plaintext, header)

    def open(self, data):
        if not data.startswith(MAGIC) or len(data) <= len(MAGIC) + 1:
            raise SessionError('Malformed or unsupported .eitaa session; migrate old plaintext files explicitly')
        kind, body = data[len(MAGIC):len(MAGIC) + 1], data[len(MAGIC) + 1:]
        if kind == b'W' and WINDOWS:
            return _dpapi(body, decrypt=True)
        if kind == b'K' and not WINDOWS:
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            if len(body) < 16 + 12 + 16:
                raise SessionError('Truncated encrypted session')
            key = self._vault_key(body[:16])
            header = data[:len(MAGIC) + 1 + 28]
            try:
                plaintext = AESGCM(key).decrypt(body[16:28], body[28:], header)
            except InvalidTag:
                raise SessionError('Damaged session or wrong OS credential store') from None
            if len(plaintext) > MAX_PLAINTEXT:
                raise SessionError('Account session exceeds size limit')
            return plaintext
        raise SessionError('Session protection belongs to a different operating system or unsupported format')
