"""Terminal login for AccountClient.start(); no credentials in CLI arguments."""
import getpass
import sys
import warnings

from .errors import AccountRPCError, AuthenticationError, PasswordRequiredError


def _terminal_available():
    return bool(sys.stdin is not None and sys.stdin.isatty())


def _secret(prompt):
    # Fail closed rather than letting getpass echo a credential to redirected input.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass(prompt)


async def _read_secret(prompt):
    try:
        # Keep terminal input on the calling thread so Ctrl+C interrupts
        # getpass/input instead of leaving an uninterruptible worker behind.
        return _secret(prompt)
    except (EOFError, getpass.GetPassWarning):
        raise AuthenticationError('Secure terminal input unavailable; use manual login with auto_login=False') from None


async def interactive_login(client):
    """Run terminal login; exit cleanly on a server-requested flood wait."""
    try:
        await _interactive_login(client)
    except AccountRPCError as exc:
        if exc.retry_after is None:
            raise
        minutes, seconds = divmod(exc.retry_after, 60)
        print(f'Eitaa rate limit: try again in {minutes} min {seconds} sec '
              f'({exc.retry_after} seconds).')
        print('Login stopped. Run the program again after this wait; no automatic retry was sent.')
        await client.disconnect()
        # Returning would let an async-with body run without authentication.
        # SystemExit ends terminal login without an unhandled-error traceback.
        raise SystemExit(1) from None


async def _interactive_login(client):
    """One code request, bounded code/password attempts, no flood/transport retry."""
    if not _terminal_available():
        raise AuthenticationError(
            'Login required but no interactive terminal is available. '
            'Run python -m examples.account_login --live locally, '
            'or use auto_login=False and the explicit login methods.'
        )
    # Local import avoids an initialization cycle with AccountClient.
    from .account_client import normalize_phone

    if client._challenge is None:
        print('Login required. Enter your Iranian mobile number (09...) or an international number (+country code).')
        for attempt in range(3):
            try:
                phone = normalize_phone(input('Phone number (09... or +country code): '))
                break
            except EOFError:
                raise AuthenticationError('Login cancelled; no code requested') from None
            except ValueError:
                if attempt == 2:
                    raise
                print('Invalid phone format. Use 11 digits starting with 09, or + and country code.')
        challenge = await client.request_code(phone)
        print(f'Code response: {challenge.delivery}; length: {challenge.length}; timeout: {challenge.timeout}')
        if challenge.synthetic_ack:
            print('Server acknowledged the request; code delivery is not independently confirmed.')

    if client._password_code is None:
        for attempt in range(3):
            try:
                await client.sign_in(await _read_secret('Login code (hidden): '))
                break
            except PasswordRequiredError:
                break
            except AccountRPCError as exc:
                if exc.rpc_type not in ('PHONE_CODE_INVALID', 'PHONE_CODE_EMPTY') or attempt == 2:
                    raise
                print('Incorrect code. Enter the same received code again; no new code requested.')
            except ValueError:
                if attempt == 2:
                    raise
                print('The login code must contain digits. Enter it again.')

    if client._password_code is not None:
        for attempt in range(3):
            try:
                await client.sign_in_password(await _read_secret('Two-step password (hidden): '))
                break
            except AccountRPCError as exc:
                if exc.rpc_type != 'PASSWORD_HASH_INVALID' or attempt == 2:
                    raise
                print('Incorrect password. Enter it again.')

    if not client.is_authorized:
        raise AuthenticationError('Login did not establish an authorized session')
    print('Login verified; session saved. Continuing the requested operation.')
