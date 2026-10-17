"""Login password proofs for the two algorithms observed in the Eitaa web source.

SRP math follows the primary specification at https://core.telegram.org/api/srp.
Only the matching named KDF is accepted; no plaintext password is sent.
"""
from functools import lru_cache
import hashlib
import secrets

from .errors import AuthenticationError, UnsupportedOperationError

SRP_ALGORITHM = 'passwordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow'


def _hash(data):
    return hashlib.sha256(data).digest()


def _bytes(value, *, maximum=1024):
    if not isinstance(value, bytes) or not 0 < len(value) <= maximum:
        raise AuthenticationError('Invalid password protocol parameters')
    return value


def legacy_password_hash(password, salt):
    salt = _bytes(salt)
    return _hash(salt + _password_bytes(password) + salt)


def _password_bytes(password):
    if not isinstance(password, str) or not password or len(password) > 1024:
        raise ValueError('Password must contain between 1 and 1024 characters')
    return password.encode('utf-8')


def _probable_prime(n):
    for prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % prime == 0:
            return n == prime
    d, shifts = n - 1, 0
    while d % 2 == 0:
        shifts += 1
        d //= 2
    # Independent random Miller-Rabin witnesses, error probability <= 2^-128.
    for _ in range(64):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(shifts - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


@lru_cache(maxsize=8)
def _group(p_bytes, g):
    p = int.from_bytes(p_bytes, 'big')
    if len(p_bytes) != 256 or p.bit_length() != 2048 or type(g) is not int:
        raise AuthenticationError('SRP requires a safe 2048-bit group')
    residues = {2: p % 8 == 7, 3: p % 3 == 2, 4: True,
                5: p % 5 in (1, 4), 6: p % 24 in (19, 23), 7: p % 7 in (3, 5, 6)}
    if not residues.get(g, False) or not _probable_prime(p) or not _probable_prime((p - 1) // 2):
        raise AuthenticationError('Invalid SRP prime or generator')
    return p


def _padded(n):
    return n.to_bytes(256, 'big')


def _good(n, p):
    return 0 < n < p and n.bit_length() >= 1984 and (p - n).bit_length() >= 1984


def srp_password_proof(password, state):
    password_bytes = _password_bytes(password)
    algo = state.get('current_algo', {})
    if algo.get('_') != SRP_ALGORITHM:
        raise UnsupportedOperationError('Unsupported account password KDF')
    p_bytes = _bytes(algo.get('p'), maximum=256)
    g = algo.get('g')
    p = _group(p_bytes, g)
    salt1, salt2 = _bytes(algo.get('salt1')), _bytes(algo.get('salt2'))
    B_bytes = _bytes(state.get('srp_B'), maximum=256)
    B = int.from_bytes(B_bytes, 'big')
    srp_id = state.get('srp_id')
    if not 0 < B < p or type(srp_id) is not int or not -(2**63) <= srp_id < 2**63:
        raise AuthenticationError('Invalid server SRP value')
    gb = _padded(g)
    B_bytes = _padded(B)
    ph1 = _hash(salt2 + _hash(salt1 + password_bytes + salt1) + salt2)
    ph2 = _hash(salt2 + hashlib.pbkdf2_hmac('sha512', ph1, salt1, 100000) + salt2)
    x = int.from_bytes(ph2, 'big')
    k = int.from_bytes(_hash(p_bytes + gb), 'big')
    base = (B - k * pow(g, x, p)) % p
    if not _good(base, p):
        raise AuthenticationError('Unsafe server SRP value')
    for _ in range(128):
        # Local CSPRNG; server-provided secure_random is never the sole entropy.
        a = int.from_bytes(secrets.token_bytes(256), 'big')
        A = pow(g, a, p)
        if not _good(A, p):
            continue
        A_bytes = _padded(A)
        u = int.from_bytes(_hash(A_bytes + B_bytes), 'big')
        if u:
            break
    else:
        raise AuthenticationError('Cannot generate safe SRP ephemeral value')
    key = _hash(_padded(pow(base, a + u * x, p)))
    xor = bytes(a ^ b for a, b in zip(_hash(p_bytes), _hash(gb)))
    proof = _hash(xor + _hash(salt1) + _hash(salt2) + A_bytes + B_bytes + key)
    return {'_': 'inputCheckPasswordSRP', 'srp_id': srp_id, 'A': A_bytes, 'M1': proof}
