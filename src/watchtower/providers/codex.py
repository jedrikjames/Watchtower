"""Codex (ChatGPT) adapter.

"Sign in with ChatGPT" is OAuth + PKCE against auth.openai.com using the public
client id the Codex CLI ships with, and a loopback redirect on port 1455. That
port is not negotiable - it is part of the registered redirect URI - so if
something else is holding it we say so plainly.

The usage endpoint reports two rolling windows, which OpenAI calls primary and
secondary. In practice that is the 5 hour and the weekly allowance, but we read
the window length out of the response rather than assuming, because those
numbers have moved before.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..auth.oauth import OAuthEndpoints, decode_jwt_claims
from ..errors import UsageUnavailable
from ..logging_setup import get_logger
from ..logos import OPENAI_BRAILLE
from ..models import AuthMethod, Credential, UsageReport, UsageWindow
from ..timefmt import from_iso, utcnow
from .base import Identity, ImportCandidate, Provider, ProviderInfo
from .http import client, get_json

log = get_logger("providers.codex")

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_PORT = 1455
REDIRECT_PATH = "/auth/callback"

USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"

#: Where the id_token hides the ChatGPT-specific claims.
AUTH_CLAIM_NAMESPACE = "https://api.openai.com/auth"

LOGO = OPENAI_BRAILLE

PLAN_NAMES = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "business": "Business",
    "team": "Team",
    "enterprise": "Enterprise",
    "edu": "Edu",
}


def _window_label(minutes: Any, fallback: str) -> str:
    """Name a window from its length, e.g. 300 -> '5-hour', 10080 -> 'Weekly'."""
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return fallback
    minutes = int(minutes)
    if minutes == 10080:
        return "Weekly"
    if minutes == 1440:
        return "Daily"
    if minutes < 60:
        return f"{minutes}-minute"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}-day"
    if minutes % 60 == 0:
        return f"{minutes // 60}-hour"
    return fallback


def _as_percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(100.0, float(value)))


class CodexProvider(Provider):
    info = ProviderInfo(
        id="codex",
        display_name="Codex",
        accent="#10a37f",
        logo=LOGO,
        signin_label="Sign in with ChatGPT",
        docs_url="https://developers.openai.com/codex",
    )
    supports_oauth = True
    supports_api_key = False  # a platform API key is billed separately from a ChatGPT plan

    def oauth_endpoints(self) -> OAuthEndpoints:
        return OAuthEndpoints(
            client_id=self.env_override("WATCHTOWER_CODEX_CLIENT_ID", CLIENT_ID),
            authorize_url=self.env_override("WATCHTOWER_CODEX_AUTHORIZE_URL", AUTHORIZE_URL),
            token_url=self.env_override("WATCHTOWER_CODEX_TOKEN_URL", TOKEN_URL),
            scopes=("openid", "profile", "email", "offline_access"),
            redirect_port=self.env_port("WATCHTOWER_CODEX_REDIRECT_PORT", REDIRECT_PORT),
            redirect_path=REDIRECT_PATH,
            token_request_style="form",
            extra_authorize_params={"id_token_add_organizations": "true"},
        )

    # -- headers -----------------------------------------------------------

    @staticmethod
    def _headers(credential: Credential) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {credential.bearer}",
            # The ChatGPT backend rejects callers it does not recognise, so
            # identify ourselves the way its own CLI does.
            "originator": "codex_cli_rs",
        }
        # It also needs to know which workspace we are asking about when the
        # account belongs to more than one. Without this the request is refused
        # with a 401 that looks exactly like a dead token.
        if credential.account_hint:
            headers["chatgpt-account-id"] = credential.account_hint
        return headers

    # -- usage -------------------------------------------------------------

    async def fetch_usage(self, credential: Credential) -> UsageReport:
        url = self.env_override("WATCHTOWER_CODEX_USAGE_URL", USAGE_URL)
        async with client() as http:
            payload = await get_json(
                http, url, headers=self._headers(credential), label="codex usage"
            )
        report = self.parse_usage(payload)
        if not report.plan:
            report.plan = self._plan_from_credential(credential)
        return report

    @classmethod
    def parse_usage(cls, payload: Any) -> UsageReport:
        if not isinstance(payload, dict):
            raise UsageUnavailable(
                "usage payload was not an object",
                friendly="ChatGPT sent usage data in a shape we did not recognise.",
            )

        limits = (
            payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else payload
        )
        now = utcnow()
        windows: list[UsageWindow] = []

        for key, fallback in (("primary", "Primary"), ("secondary", "Secondary")):
            entry = limits.get(key)
            if not isinstance(entry, dict):
                continue
            percent = _as_percent(
                entry.get("used_percent", entry.get("utilization", entry.get("percent_used")))
            )
            if percent is None:
                continue

            resets_at = None
            resets_in = entry.get("resets_in_seconds", entry.get("seconds_until_reset"))
            if (
                isinstance(resets_in, (int, float))
                and not isinstance(resets_in, bool)
                and resets_in >= 0
            ):
                resets_at = now + timedelta(seconds=float(resets_in))
            elif isinstance(entry.get("resets_at"), str):
                resets_at = from_iso(entry["resets_at"])

            windows.append(
                UsageWindow(
                    key=key,
                    label=_window_label(entry.get("window_minutes"), fallback),
                    percent=percent,
                    resets_at=resets_at,
                )
            )

        if not windows:
            raise UsageUnavailable(
                "no recognisable windows in the usage payload",
                friendly="ChatGPT did not report usage limits for this account.",
            )

        plan = payload.get("plan_type") or payload.get("plan") or ""
        return UsageReport(windows=windows, plan=cls._plan_name(plan), fetched_at=now)

    @staticmethod
    def _plan_name(raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        return PLAN_NAMES.get(text.lower(), text.replace("_", " ").title())

    @classmethod
    def _plan_from_credential(cls, credential: Credential) -> str:
        return cls._plan_name(credential.extra.get("plan", ""))

    # -- credential shaping --------------------------------------------------

    def finalise_credential(self, credential: Credential, tokens: object) -> Credential:
        """Keep the id_token: the account id and plan live in its claims."""
        id_token = str(getattr(tokens, "id_token", "") or "") or str(
            credential.extra.get("id_token") or ""
        )
        if not id_token:
            return credential

        claims = decode_jwt_claims(id_token)
        auth = (
            claims.get(AUTH_CLAIM_NAMESPACE)
            if isinstance(claims.get(AUTH_CLAIM_NAMESPACE), dict)
            else {}
        )

        credential.extra["id_token"] = id_token
        if auth.get("chatgpt_plan_type"):
            credential.extra["plan"] = str(auth["chatgpt_plan_type"])
        if not credential.account_hint and auth.get("chatgpt_account_id"):
            credential.account_hint = str(auth["chatgpt_account_id"])
        return credential

    # -- identity -----------------------------------------------------------

    async def identify(self, credential: Credential) -> Identity:
        """Read the id_token claims.

        Unverified - see decode_jwt_claims. It is display only, and there is no
        cheaper way to get the plan name without another round trip.
        """
        token = str(credential.extra.get("id_token") or "")
        claims = decode_jwt_claims(token)
        if not claims:
            return Identity()

        auth = claims.get(AUTH_CLAIM_NAMESPACE)
        auth = auth if isinstance(auth, dict) else {}

        email = str(claims.get("email") or "")
        plan = self._plan_name(auth.get("chatgpt_plan_type") or "")
        account_id = str(auth.get("chatgpt_account_id") or "")
        return Identity(
            label=str(claims.get("name") or email or "ChatGPT"),
            email=email,
            plan=plan,
            account_id=account_id,
        )

    # -- importing from the Codex CLI ---------------------------------------

    def import_candidates(self) -> list[ImportCandidate]:
        path = Path.home() / ".codex" / "auth.json"
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            log.info("could not read %s: %s", path.name, type(exc).__name__)
            return []

        tokens = raw.get("tokens") if isinstance(raw, dict) else None
        if not isinstance(tokens, dict):
            return []

        access = str(tokens.get("access_token") or "")
        if not access:
            return []

        id_token = str(tokens.get("id_token") or "")
        claims = decode_jwt_claims(id_token)
        auth = (
            claims.get(AUTH_CLAIM_NAMESPACE)
            if isinstance(claims.get(AUTH_CLAIM_NAMESPACE), dict)
            else {}
        )

        credential = Credential(
            method=AuthMethod.IMPORTED,
            access_token=access,
            refresh_token=str(tokens.get("refresh_token") or ""),
            account_hint=str(tokens.get("account_id") or auth.get("chatgpt_account_id") or ""),
            extra={"id_token": id_token} if id_token else {},
        )
        credential.register_for_redaction()

        email = str(claims.get("email") or "")
        plan = self._plan_name(auth.get("chatgpt_plan_type") or "")
        return [
            ImportCandidate(
                provider_id=self.info.id,
                label=f"Codex CLI{f' ({plan})' if plan else ''}",
                source=str(path),
                credential=credential,
                identity=Identity(label=email or "Codex CLI", email=email, plan=plan),
            )
        ]
