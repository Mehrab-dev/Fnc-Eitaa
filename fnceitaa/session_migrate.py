"""Protect a legacy session offline, without requesting a password or OTP."""
import argparse

from .errors import SessionError
from .session import migrate_legacy_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Existing plaintext .session file')
    parser.add_argument('destination', nargs='?', help='New .eitaa file (default: same base name)')
    args = parser.parse_args()
    try:
        path = migrate_legacy_session(args.source, args.destination)
    except SessionError as exc:
        parser.exit(1, f'{exc}\n')
    print(f'Protected session saved: {path}')
    print('Original retained. After verifying reconnect, remove the plaintext source yourself.')
    print('Queues and checkpoints were not moved; pass their old paths explicitly to continue them.')


if __name__ == '__main__':
    main()
