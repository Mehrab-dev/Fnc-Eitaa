"""Local interactive login. Never pass credentials in command-line arguments."""
import argparse
import asyncio
import getpass
import warnings

from fnceitaa import (AccountClient, AccountRPCError, FncEitaaError,
                      PasswordRequiredError)
from fnceitaa.account_client import normalize_phone
from fnceitaa.protocol import TLCodecError


def secret(prompt):
    # Do not silently echo secrets when stdin is redirected or getpass fails.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass(prompt)


def read_phone():
    print('Enter your Iranian mobile number (09...) or an international number (+country code).')
    while True:
        phone = input('Phone number (09... or +country code): ')
        try:
            return normalize_phone(phone)
        except ValueError:
            print('Invalid phone format. Use 11 digits starting with 09, or + and country code.')
            print('Enter the full number again, or press Ctrl+C to cancel.')


async def login(session_path):
    async with AccountClient(session_path, auto_login=False) as client:
        if client.is_authorized:
            user = await client.get_me()
            print(f"Session reconnected. Account ID: {user['id']}")
            return
        phone = read_phone()
        try:
            receipt = await client.request_code(phone)
        except AccountRPCError as exc:
            if exc.code == 401 and exc.rpc_type == 'INVALID_LOGIN':
                print('Eitaa rejected auth.sendCode before returning a code challenge.')
                print('Phone format passed local validation. This does not establish that the phone or account is invalid.')
                print('Live SDK login is not verified. No automatic retry or session save was performed.')
            raise
        print(f'Code response: {receipt.delivery}; length: {receipt.length}; timeout: {receipt.timeout}')
        if receipt.synthetic_ack:
            print('Server returned a pong ACK; code delivery is not independently confirmed.')
        for attempt in range(3):
            try:
                user = await client.sign_in(secret('Login code (hidden; submitted to Eitaa): '))
                break
            except PasswordRequiredError:
                for password_attempt in range(3):
                    try:
                        user = await client.sign_in_password(secret('Two-step password (hidden; sends proof): '))
                        break
                    except AccountRPCError as exc:
                        if exc.rpc_type != 'PASSWORD_HASH_INVALID' or password_attempt == 2:
                            raise
                        print('Incorrect password. Try again.')
                break
            except AccountRPCError as exc:
                if exc.rpc_type not in ('PHONE_CODE_INVALID', 'PHONE_CODE_EMPTY') or attempt == 2:
                    raise
                print('Incorrect code. Try again with the same received code.')
        print(f"Login verified; session saved. Account ID: {user['id']}")
        print('Run this command again to check reconnect without another OTP.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Enable interactive Eitaa login/reconnect')
    parser.add_argument('--session', default='account.eitaa', help='Account session file (default: account.eitaa)')
    args = parser.parse_args()
    if not args.live:
        print('Offline mode. No network request and no session file created.')
        print('To log in locally: python -m examples.account_login --live')
        print('Enter your phone, code and password only in this terminal, not in chat.')
        return
    try:
        asyncio.run(login(args.session))
    except AccountRPCError as exc:
        if exc.retry_after is not None:
            minutes, seconds = divmod(exc.retry_after, 60)
            print(f'Eitaa rate limit: wait at least {minutes} min {seconds} sec from this response before trying again.')
            print('No automatic retry will be sent. Close this program and retry manually after the wait.')
            parser.exit(1)
        parser.exit(1, f'AccountRPCError: {exc}\n')
    except TLCodecError as exc:
        print('Response decoding failed; the server outcome is uncertain. No automatic retry was sent.')
        print('Share only the structural TLCodecError line below, never your code, token or session file.')
        parser.exit(1, f'TLCodecError: {exc}\n')
    except (FncEitaaError, ValueError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        parser.exit(1, 'Login interrupted or secure terminal input unavailable.\n')


if __name__ == '__main__':
    main()
