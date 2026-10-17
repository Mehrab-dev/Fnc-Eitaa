"""SRP client proof checked against a server-side key, not its own formula."""
import hashlib
import unittest
from unittest.mock import patch

from fnceitaa.errors import AuthenticationError, UnsupportedOperationError
from fnceitaa.password import SRP_ALGORITHM, _group, _probable_prime, srp_password_proof

# RFC 3526 section 3 (2048-bit group 14), used only as a test group.
P_BYTES = bytes.fromhex('''
FFFFFFFF FFFFFFFF C90FDAA2 2168C234 C4C6628B 80DC1CD1
29024E08 8A67CC74 020BBEA6 3B139B22 514A0879 8E3404DD
EF9519B3 CD3A431B 302B0A6D F25F1437 4FE1356D 6D51C245
E485B576 625E7EC6 F44C42E9 A637ED6B 0BFF5CB6 F406B7ED
EE386BFB 5A899FA5 AE9F2411 7C4B1FE6 49286651 ECE45B3D
C2007CB8 A163BF05 98DA4836 1C55D39A 69163FA8 FD24CF5F
83655D23 DCA3AD96 1C62F356 208552BB 9ED52907 7096966D
670C354E 4ABC9804 F1746C08 CA18217C 32905E46 2E36CE3B
E39E772C 180E8603 9B2783A2 EC07A28F B5C55DF0 6F4C52C9
DE2BCBF6 95581718 3995497C EA956AE5 15D22618 98FA0510
15728E5A 8AACAA68 FFFFFFFF FFFFFFFF
''')


def H(data):
    return hashlib.sha256(data).digest()


def PAD(n):
    return n.to_bytes(256, 'big')


class SRPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = int.from_bytes(P_BYTES, 'big')
        # Model stored server verifier. Server has no client ephemeral secret a.
        salt1, salt2 = b'first fixture salt', b'second fixture salt'
        first = H(salt2 + H(salt1 + 'رمز-test'.encode() + salt1) + salt2)
        x = int.from_bytes(H(salt2 + hashlib.pbkdf2_hmac('sha512', first, salt1, 100000) + salt2), 'big')
        cls.verifier = pow(2, x, cls.p)
        cls.server_secret = int.from_bytes(b'\x42' * 256, 'big')
        k = int.from_bytes(H(P_BYTES + PAD(2)), 'big')
        B = (k * cls.verifier + pow(2, cls.server_secret, cls.p)) % cls.p
        cls.state = {'_': 'account.password', 'current_algo': {
            '_': SRP_ALGORITHM, 'p': P_BYTES, 'g': 2, 'salt1': salt1, 'salt2': salt2},
            'srp_B': PAD(B), 'srp_id': -5, 'secure_random': b'ignored server entropy'}

    def server_proof(self, proof):
        A = int.from_bytes(proof['A'], 'big')
        u = int.from_bytes(H(proof['A'] + self.state['srp_B']), 'big')
        shared = pow((A * pow(self.verifier, u, self.p)) % self.p, self.server_secret, self.p)
        xor = bytes(a ^ b for a, b in zip(H(P_BYTES), H(PAD(2))))
        algo = self.state['current_algo']
        return H(xor + H(algo['salt1']) + H(algo['salt2']) + proof['A'] + self.state['srp_B'] + H(PAD(shared)))

    def test_proof_matches_independent_server_and_uses_local_entropy(self):
        with patch('fnceitaa.password.secrets.token_bytes', return_value=b'\x23' * 256) as random:
            proof = srp_password_proof('رمز-test', self.state)
        random.assert_called_once_with(256)
        self.assertEqual(proof['M1'], self.server_proof(proof))
        self.assertEqual(proof['srp_id'], -5)
        self.assertEqual(len(proof['A']), 256)
        self.assertEqual(len(proof['M1']), 32)
        self.assertNotIn('رمز-test', repr(proof))

    def test_wrong_password_cannot_match_server(self):
        proof = srp_password_proof('wrong-password', self.state)
        self.assertNotEqual(proof['M1'], self.server_proof(proof))

    def test_invalid_B_and_unknown_algorithm_are_rejected(self):
        for B in (b'\x00', P_BYTES, b'x' * 257):
            with self.assertRaises(AuthenticationError):
                srp_password_proof('password', {**self.state, 'srp_B': B})
        with self.assertRaises(UnsupportedOperationError):
            srp_password_proof('password', {'current_algo': {'_': 'unknown'}})
        with self.assertRaises(ValueError):
            srp_password_proof('', self.state)

    def test_unsafe_groups_and_composites_are_rejected(self):
        for group, g in ((b'x', 2), (P_BYTES, 1), (P_BYTES, True), (b'\xff' * 256, 2)):
            with self.assertRaises(AuthenticationError):
                _group(group, g)
        self.assertTrue(_probable_prime(101))
        self.assertFalse(_probable_prime(341))

    def test_entropy_failure_is_bounded(self):
        with patch('fnceitaa.password.secrets.token_bytes', return_value=b'\x00' * 256) as random:
            with self.assertRaises(AuthenticationError):
                srp_password_proof('password', self.state)
        self.assertEqual(random.call_count, 128)


if __name__ == '__main__':
    unittest.main()
