import re


class FncEitaaError(Exception):
    """ The Basic Of All Library Errors """
    pass


class SessionNotFoundError(FncEitaaError):
    """ Login Session Not Found """
    pass


class PeerResolveError(FncEitaaError):
    """ User ID, Chat or Channel Not Found """
    pass


class APIRequestError(FncEitaaError):
    """ Error in Response Received From Eitaa Server """
    pass


class UnsupportedOperationError(FncEitaaError):
    """The active backend does not support this operation."""


class AccountTransportError(APIRequestError):
    """HTTP failed. A transmitted operation may have an uncertain outcome."""

    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


class AccountRPCError(APIRequestError):
    """An explicitly decoded account RPC failure."""

    def __init__(self, code: int, rpc_type: str):
        self.code = code
        self.rpc_type = rpc_type
        super().__init__(f'Account RPC error {code}: {rpc_type}')

    @property
    def retry_after(self) -> int | None:
        """Server-requested wait in seconds, independent of Eitaa's RPC code."""
        match = re.fullmatch(r'FLOOD_WAIT_([0-9]{1,128})', self.rpc_type)
        return int(match.group(1)) if match else None


class AccountStateError(APIRequestError):
    """Account token control response; renewal belongs to the session layer."""

    def __init__(self, signal: str):
        self.signal = signal
        super().__init__(f'Account state requires handling: {signal}')


class SessionError(FncEitaaError):
    """Invalid, unreadable, conflicting or unwritable local account session."""


class AuthenticationError(FncEitaaError):
    """The requested operation is invalid in the current login state."""


class PasswordRequiredError(AuthenticationError):
    """OTP accepted; call sign_in_password to finish authentication."""


class SessionRevokedError(AuthenticationError):
    """The server rejected a saved account session; no automatic OTP is sent."""
