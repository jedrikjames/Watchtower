"""The provider interface.

Adding a provider means writing one subclass and registering it. Nothing in the
TUI or the storage layer knows what a Codex or a Claude is.

A note on the usage endpoints. Neither vendor publishes a stable, documented
"how much of my subscription have I used" API - both of the endpoints we call
are the ones their own CLIs use, and they can change without warning. So:

* every network call is allowed to fail without taking the app down,
* the URLs can be overridden with an environment variable,
* and a provider that cannot answer returns UsageUnavailable rather than
  guessing, so the card says "unavailable" instead of inventing a number.

See docs/providers.md for how to write your own.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field

from ..auth.oauth import OAuthClient, OAuthEndpoints
from ..models import Credential, UsageReport


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    id: str
    display_name: str
    #: Card accent, used for the logo and the usage bar.
    accent: str
    #: Mark drawn on the card: three lines of five cells, braille by default.
    logo: tuple[str, ...]
    #: What the "sign in" button should say.
    signin_label: str
    #: Box-drawing fallback for terminals whose font lacks braille.
    logo_blocks: tuple[str, ...] = ()
    docs_url: str = ""
    api_key_label: str = "API key"
    api_key_hint: str = ""


@dataclass(slots=True)
class Identity:
    """Whatever we can find out about who an account belongs to."""

    label: str = ""
    email: str = ""
    plan: str = ""
    account_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class Provider(abc.ABC):
    info: ProviderInfo

    #: Which ways of adding an account this provider offers.
    supports_oauth: bool = True
    supports_api_key: bool = False

    # -- auth -----------------------------------------------------------

    def oauth_endpoints(self) -> OAuthEndpoints:
        raise NotImplementedError(f"{self.info.id} does not do OAuth")

    def oauth_client(self) -> OAuthClient:
        from .http import USER_AGENT

        return OAuthClient(self.oauth_endpoints(), user_agent=USER_AGENT)

    async def refresh_credential(self, credential: Credential) -> Credential:
        """Return a credential with a fresh access token.

        The default implementation does the standard refresh grant. Providers
        with an unusual flow can override it.
        """
        from ..models import AuthMethod

        if credential.method is AuthMethod.API_KEY:
            return credential

        tokens = await self.oauth_client().refresh(credential.refresh_token)
        refreshed = Credential(
            method=credential.method,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token or credential.refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes or credential.scopes,
            account_hint=credential.account_hint,
            extra=dict(credential.extra),
        )
        refreshed.register_for_redaction()
        return self.finalise_credential(refreshed, tokens)

    def credential_from_api_key(self, api_key: str) -> Credential:
        raise NotImplementedError(f"{self.info.id} does not accept an API key")

    def finalise_credential(self, credential: Credential, tokens: object) -> Credential:
        """Hook for pulling provider-specific bits out of a token response.

        Codex, for instance, needs the id_token kept around because the account
        id and plan name only exist in its claims. Default is a no-op.
        """
        return credential

    # -- data -----------------------------------------------------------

    @abc.abstractmethod
    async def fetch_usage(self, credential: Credential) -> UsageReport:
        """Current usage. Raise UsageUnavailable if the provider will not say."""

    async def identify(self, credential: Credential) -> Identity:
        """Best-effort: who is this? Never fatal - a card can live without it."""
        return Identity()

    # -- local CLI import ------------------------------------------------

    def import_candidates(self) -> list[ImportCandidate]:
        """Credentials belonging to this provider's own CLI, if it is installed."""
        return []

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def env_override(name: str, default: str) -> str:
        """Let users point an adapter at a different URL without a code change."""
        return os.environ.get(name, "").strip() or default

    @staticmethod
    def env_port(name: str, default: int) -> int:
        """Override the loopback port.

        Mostly for the test suite, but it also rescues anyone whose default
        port is permanently occupied. Note that the provider has to accept the
        new redirect URI, and most only allow the port their own CLI uses.
        """
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            port = int(raw)
        except ValueError:
            return default
        return port if 1024 <= port <= 65535 else default


@dataclass(slots=True)
class ImportCandidate:
    """A credential we found sitting on disk that the user could adopt."""

    provider_id: str
    label: str
    source: str  # path, shown to the user so they know what we found
    credential: Credential
    identity: Identity = field(default_factory=Identity)
