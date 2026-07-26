"""Error types.

Every error carries a ``friendly`` string. The TUI shows that and nothing else,
so it must never contain a token, a URL with a query string, or a file path
outside the app directory.
"""

from __future__ import annotations


class WatchtowerError(Exception):
    """Base class. ``friendly`` is what the user sees."""

    default_friendly = "Something went wrong."

    def __init__(self, message: str = "", *, friendly: str | None = None):
        super().__init__(message or self.default_friendly)
        self.friendly = friendly or message or self.default_friendly


class ConfigError(WatchtowerError):
    default_friendly = "Your configuration file could not be read."


class StorageError(WatchtowerError):
    default_friendly = "Could not read or write the local data directory."


class VaultLocked(StorageError):
    default_friendly = "The local vault is locked. Enter your passphrase to unlock it."


class VaultPassphraseError(StorageError):
    default_friendly = "That passphrase did not work."


class AuthError(WatchtowerError):
    default_friendly = "Sign-in failed."


class AuthCancelled(AuthError):
    default_friendly = "Sign-in was cancelled."


class AuthTimeout(AuthError):
    default_friendly = "Sign-in timed out. The browser window was never completed."


class ReauthRequired(AuthError):
    """The stored credential is dead and refreshing it did not help."""

    default_friendly = "This account needs to be signed in again."


class ProviderError(WatchtowerError):
    default_friendly = "The provider returned an unexpected response."


class RateLimited(ProviderError):
    default_friendly = "The provider is rate limiting us. Showing the last known figures."

    def __init__(self, message: str = "", *, retry_after: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class NetworkError(ProviderError):
    default_friendly = "Could not reach the provider. Check your connection."


class UsageUnavailable(ProviderError):
    """The provider is reachable but does not expose usage for this account type."""

    default_friendly = "Usage figures are not available for this account."
