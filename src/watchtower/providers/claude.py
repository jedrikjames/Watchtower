"""Claude (Anthropic) adapter.

Sign-in is the same OAuth + PKCE flow Claude Code uses, against the public
client id that ships in it. Usage comes from the endpoint behind Claude Code's
own /usage command.

That endpoint rate limits *hard* - much harder than you would expect for a
read-only stats call - so the refresh loop treats a 429 as "keep the previous
numbers and back off", never as an error worth shouting about. See
anthropics/claude-code#31637.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..auth.oauth import OAuthEndpoints
from ..errors import UsageUnavailable
from ..logging_setup import get_logger
from ..models import AuthMethod, Credential, UsageReport, UsageWindow
from ..timefmt import from_iso, utcnow
from .base import Identity, ImportCandidate, Provider, ProviderInfo
from .http import client, get_json

log = get_logger("providers.claude")

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_PORT = 54545
REDIRECT_PATH = "/callback"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

OAUTH_BETA = "oauth-2025-04-20"
API_VERSION = "2023-06-01"

LOGO = ("╲ │ ╱", "──╋──", "╱ │ ╲")

# Window key -> what we call it on the card. Anything we do not recognise is
# still shown, with the raw key title-cased, so a new limit type appearing
# server-side does not vanish from the UI.
WINDOW_LABELS = {
    "five_hour": "5-hour",
    "seven_day": "Weekly",
    "seven_day_opus": "Weekly (Opus)",
    "seven_day_oauth_apps": "Weekly (apps)",
    "monthly": "Monthly",
}
WINDOW_ORDER = ["five_hour", "seven_day", "seven_day_opus", "seven_day_oauth_apps", "monthly"]

PLAN_NAMES = {
    "max": "Max",
    "max_5x": "Max 5x",
    "max_20x": "Max 20x",
    "pro": "Pro",
    "team": "Team",
    "enterprise": "Enterprise",
    "free": "Free",
}


def _as_percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(100.0, float(value)))


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        return from_iso(value)
    if isinstance(value, (int, float)) and value > 0:
        # Seconds or milliseconds; nobody is tracking usage in 1970.
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


class ClaudeProvider(Provider):
    info = ProviderInfo(
        id="claude",
        display_name="Claude",
        accent="#d97757",
        logo=LOGO,
        signin_label="Sign in with Claude",
        docs_url="https://claude.ai",
    )
    supports_oauth = True
    supports_api_key = False  # an API key says nothing about subscription limits

    def oauth_endpoints(self) -> OAuthEndpoints:
        return OAuthEndpoints(
            client_id=self.env_override("WATCHTOWER_CLAUDE_CLIENT_ID", CLIENT_ID),
            authorize_url=self.env_override("WATCHTOWER_CLAUDE_AUTHORIZE_URL", AUTHORIZE_URL),
            token_url=self.env_override("WATCHTOWER_CLAUDE_TOKEN_URL", TOKEN_URL),
            scopes=("org:create_api_key", "user:profile", "user:inference"),
            redirect_port=self.env_port("WATCHTOWER_CLAUDE_REDIRECT_PORT", REDIRECT_PORT),
            redirect_path=REDIRECT_PATH,
            token_request_style="json",
        )

    # -- headers ---------------------------------------------------------

    @staticmethod
    def _headers(credential: Credential) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credential.bearer}",
            "anthropic-beta": OAUTH_BETA,
            "anthropic-version": API_VERSION,
        }

    # -- usage -----------------------------------------------------------

    async def fetch_usage(self, credential: Credential) -> UsageReport:
        url = self.env_override("WATCHTOWER_CLAUDE_USAGE_URL", USAGE_URL)
        async with client() as http:
            payload = await get_json(
                http, url, headers=self._headers(credential), label="claude usage"
            )
        return self.parse_usage(payload)

    @classmethod
    def parse_usage(cls, payload: Any) -> UsageReport:
        """Turn the usage payload into windows.

        Kept as a classmethod with no I/O so it is straightforward to test
        against a captured response.
        """
        if not isinstance(payload, dict):
            raise UsageUnavailable(
                "usage payload was not an object",
                friendly="Anthropic sent usage data in a shape we did not recognise.",
            )

        # Some responses nest everything under rate_limits.
        source = (
            payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else payload
        )

        windows: list[UsageWindow] = []
        keys = [k for k in WINDOW_ORDER if k in source]
        keys += [k for k in source if k not in WINDOW_ORDER]

        for key in keys:
            entry = source.get(key)
            if not isinstance(entry, dict):
                continue
            percent = _as_percent(
                entry.get("utilization", entry.get("used_percent", entry.get("percent_used")))
            )
            if percent is None:
                continue
            windows.append(
                UsageWindow(
                    key=key,
                    label=WINDOW_LABELS.get(key, key.replace("_", " ").capitalize()),
                    percent=percent,
                    resets_at=_as_datetime(entry.get("resets_at") or entry.get("reset_at")),
                )
            )

        if not windows:
            raise UsageUnavailable(
                "no recognisable windows in the usage payload",
                friendly="Anthropic did not report any usage windows for this account.",
            )

        plan = payload.get("subscription") or payload.get("plan") or ""
        return UsageReport(windows=windows, plan=cls._plan_name(plan), fetched_at=utcnow())

    @staticmethod
    def _plan_name(raw: Any) -> str:
        if isinstance(raw, dict):
            raw = raw.get("type") or raw.get("name") or ""
        text = str(raw or "").strip()
        if not text:
            return ""
        return PLAN_NAMES.get(text.lower(), text.replace("_", " ").title())

    # -- identity ---------------------------------------------------------

    async def identify(self, credential: Credential) -> Identity:
        url = self.env_override("WATCHTOWER_CLAUDE_PROFILE_URL", PROFILE_URL)
        try:
            async with client() as http:
                payload = await get_json(
                    http, url, headers=self._headers(credential), label="claude profile"
                )
        except Exception as exc:
            # A card without an email is fine. A crash on sign-in is not.
            log.info("could not read the Claude profile: %s", type(exc).__name__)
            return Identity()

        if not isinstance(payload, dict):
            return Identity()

        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        organization = (
            payload.get("organization") if isinstance(payload.get("organization"), dict) else {}
        )

        email = str(account.get("email_address") or account.get("email") or "")
        plan = self._plan_name(
            organization.get("billing_type")
            or organization.get("subscription_type")
            or account.get("subscription_type")
            or ""
        )
        label = str(organization.get("name") or account.get("full_name") or email or "Claude")
        return Identity(
            label=label,
            email=email,
            plan=plan,
            account_id=str(account.get("uuid") or organization.get("uuid") or ""),
        )

    # -- importing from the Claude Code CLI --------------------------------

    def import_candidates(self) -> list[ImportCandidate]:
        path = Path.home() / ".claude" / ".credentials.json"
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            log.info("could not read %s: %s", path.name, type(exc).__name__)
            return []

        block = raw.get("claudeAiOauth") if isinstance(raw, dict) else None
        if not isinstance(block, dict):
            return []

        access = str(block.get("accessToken") or "")
        if not access:
            return []

        scopes = block.get("scopes")
        credential = Credential(
            method=AuthMethod.IMPORTED,
            access_token=access,
            refresh_token=str(block.get("refreshToken") or ""),
            expires_at=_as_datetime(block.get("expiresAt")),
            scopes=tuple(str(s) for s in scopes) if isinstance(scopes, list) else (),
        )
        credential.register_for_redaction()

        plan = self._plan_name(block.get("subscriptionType"))
        return [
            ImportCandidate(
                provider_id=self.info.id,
                label=f"Claude Code{f' ({plan})' if plan else ''}",
                source=str(path),
                credential=credential,
                identity=Identity(plan=plan),
            )
        ]
