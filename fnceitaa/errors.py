import asyncio
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


class QueueError(FncEitaaError):
    """Invalid or unavailable durable queue; no implicit network retry."""


class QueueConflictError(QueueError, SessionError):
    """A durable cursor, idempotency key or claim changed in another worker."""


class UpdateGapError(FncEitaaError):
    """The saved cursor cannot be continued without an explicit resnapshot."""

    def __init__(self, scope, reason):
        self.scope = scope
        super().__init__(f'Update gap ({scope}): {reason}; checkpoint was not advanced')


class AuthenticationError(FncEitaaError):
    """The requested operation is invalid in the current login state."""


class PasswordRequiredError(AuthenticationError):
    """OTP accepted; call sign_in_password to finish authentication."""


class SessionRevokedError(AuthenticationError):
    """The server rejected a saved account session; no automatic OTP is sent."""


class SendMessageUncertainError(APIRequestError):
    """A text send may have reached Eitaa; never resend blindly."""

    def __init__(self, random_id: int):
        self.random_id = random_id
        super().__init__(f'Text-send outcome is uncertain (random_id={random_id}); no automatic retry sent')


class SendMessageCancelledError(asyncio.CancelledError):
    """Cancellation preserves the send identity and normal asyncio semantics."""

    def __init__(self, random_id: int):
        self.random_id = random_id
        super().__init__(f'Text send cancelled; outcome may be uncertain (random_id={random_id})')


class MessageOperationUncertainError(APIRequestError):
    """An edit/delete/forward/copy may have taken effect; do not retry blindly."""

    def __init__(self, operation, message_ids, random_ids=()):
        self.operation = operation
        self.message_ids = tuple(message_ids)
        self.random_ids = tuple(random_ids)
        super().__init__(f'{operation} outcome is uncertain; message_ids={self.message_ids}; '
                         f'random_ids={self.random_ids}; no automatic retry sent')


class MessageOperationCancelledError(asyncio.CancelledError):
    """Cancellation after starting a mutation retains its correlation IDs."""

    def __init__(self, operation, message_ids, random_ids=()):
        self.operation = operation
        self.message_ids = tuple(message_ids)
        self.random_ids = tuple(random_ids)
        super().__init__(f'{operation} cancelled; outcome may be uncertain; '
                         f'message_ids={self.message_ids}; random_ids={self.random_ids}')


class UploadInterruptedError(APIRequestError):
    """A file part was not confirmed; no message-send RPC was started."""

    def __init__(self, file_id, part):
        self.file_id, self.part = file_id, part
        super().__init__(f'Upload part not confirmed; file_id={file_id}; part={part}; no message sent')


class UploadCancelledError(asyncio.CancelledError):
    """Cancelled upload; bytes may be staged, but no message-send RPC started."""

    def __init__(self, file_id, part):
        self.file_id, self.part = file_id, part
        super().__init__(f'Upload cancelled; file_id={file_id}; part={part}; no message sent')
