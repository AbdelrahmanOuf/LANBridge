"""Errors safe to present to a user (never include a password)."""


class BridgeError(Exception):
    """Expected operational error with an actionable user-facing message."""


class ValidationError(BridgeError):
    """Invalid or unsafe task input."""


class PlatformError(BridgeError):
    """Windows API or command failed."""


class CredentialError(PlatformError):
    """Authentication challenge, carrying a UNC path but no secret."""

    def __init__(self, unc: str, message: str):
        super().__init__(message)
        self.unc = unc
        self.auto_prompt = False


class BusyError(BridgeError):
    """An operation conflicts with an active transfer."""


class Stopped(BridgeError):
    """Cooperative cancellation before another unit of work starts."""
