"""The data the app moves around.

Account and UsageReport get written to disk. Credential never does - it goes
to the secret store, which is a different thing entirely - and it has a repr
that refuses to print its own contents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .logging_setup import register_secret
from .timefmt import from_iso, to_iso, utcnow


class AuthMethod(str, Enum):
    OAUTH = "oauth"
    API_KEY = "api_key"
    IMPORTED = "imported"  # lifted from a local CLI's own credential file

    @property
    def label(self) -> str:
        return {
            AuthMethod.OAUTH: "Browser sign-in",
            AuthMethod.API_KEY: "API key",
            AuthMethod.IMPORTED: "Imported from CLI",
        }[self]


class AccountStatus(str, Enum):
    UNKNOWN = "unknown"
    LOADING = "loading"
    OK = "ok"
    STALE = "stale"
    RATE_LIMITED = "rate_limited"
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"
    DISABLED = "disabled"

    @property
    def is_problem(self) -> bool:
        return self in (AccountStatus.NEEDS_AUTH, AccountStatus.ERROR)


@dataclass(slots=True)
class UsageWindow:
    """One rate limit bucket, e.g. 'the 5 hour window'."""

    key: str
    label: str
    percent: float | None = None
    resets_at: datetime | None = None
    used: float | None = None
    limit: float | None = None
    unit: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        return self.percent is not None

    @property
    def value(self) -> float:
        """Percent clamped into 0-100 so a bad payload cannot draw a bar off the card."""
        if self.percent is None:
            return 0.0
        return max(0.0, min(100.0, float(self.percent)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "percent": self.percent,
            "resets_at": to_iso(self.resets_at),
            "used": self.used,
            "limit": self.limit,
            "unit": self.unit,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UsageWindow:
        percent = raw.get("percent")
        return cls(
            key=str(raw.get("key", "window")),
            label=str(raw.get("label", "Usage")),
            percent=float(percent) if isinstance(percent, (int, float)) else None,
            resets_at=from_iso(raw.get("resets_at")),
            used=raw.get("used"),
            limit=raw.get("limit"),
            unit=str(raw.get("unit", "")),
            note=str(raw.get("note", "")),
        )


@dataclass(slots=True)
class UsageReport:
    windows: list[UsageWindow] = field(default_factory=list)
    plan: str = ""
    note: str = ""
    fetched_at: datetime = field(default_factory=utcnow)

    @property
    def headline(self) -> UsageWindow | None:
        """The window we show the big percentage for: whichever is fullest."""
        known = [w for w in self.windows if w.known]
        if not known:
            return self.windows[0] if self.windows else None
        return max(known, key=lambda w: w.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "windows": [w.to_dict() for w in self.windows],
            "plan": self.plan,
            "note": self.note,
            "fetched_at": to_iso(self.fetched_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UsageReport:
        windows = raw.get("windows")
        return cls(
            windows=[UsageWindow.from_dict(w) for w in windows if isinstance(w, dict)]
            if isinstance(windows, list)
            else [],
            plan=str(raw.get("plan", "")),
            note=str(raw.get("note", "")),
            fetched_at=from_iso(raw.get("fetched_at")) or utcnow(),
        )


@dataclass(slots=True, repr=False)
class Credential:
    """Tokens. Not serialised anywhere except through the secret store."""

    method: AuthMethod = AuthMethod.OAUTH
    access_token: str = ""
    refresh_token: str = ""
    api_key: str = ""
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    account_hint: str = ""  # provider-side account id, not a secret but not shown
    extra: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        # If this ever ends up in a traceback we want it to say nothing useful.
        state = "expired" if self.expired() else "valid"
        return f"<Credential {self.method.value} {state}>"

    __str__ = __repr__

    @property
    def bearer(self) -> str:
        return self.access_token or self.api_key

    def expired(self, *, skew_seconds: int = 300) -> bool:
        """True if the token is gone or will be within ``skew_seconds``.

        The skew means we refresh slightly early rather than watching a request
        fail and then retrying.
        """
        if self.method is AuthMethod.API_KEY:
            return False
        if not self.access_token:
            return True
        if self.expires_at is None:
            return False
        return utcnow() + timedelta(seconds=skew_seconds) >= self.expires_at

    def register_for_redaction(self) -> None:
        for value in (self.access_token, self.refresh_token, self.api_key):
            register_secret(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "api_key": self.api_key,
            "expires_at": to_iso(self.expires_at),
            "scopes": list(self.scopes),
            "account_hint": self.account_hint,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Credential:
        try:
            method = AuthMethod(raw.get("method", "oauth"))
        except ValueError:
            method = AuthMethod.OAUTH
        scopes = raw.get("scopes")
        cred = cls(
            method=method,
            access_token=str(raw.get("access_token") or ""),
            refresh_token=str(raw.get("refresh_token") or ""),
            api_key=str(raw.get("api_key") or ""),
            expires_at=from_iso(raw.get("expires_at")),
            scopes=tuple(str(s) for s in scopes) if isinstance(scopes, list) else (),
            account_hint=str(raw.get("account_hint") or ""),
            extra=raw.get("extra") if isinstance(raw.get("extra"), dict) else {},
        )
        cred.register_for_redaction()
        return cred


@dataclass(slots=True)
class Account:
    """What the user sees in a card. Contains nothing secret."""

    id: str
    provider: str
    label: str
    identity: str = ""  # email or org, whatever the provider gave us
    plan: str = ""
    auth_method: AuthMethod = AuthMethod.OAUTH
    enabled: bool = True
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @classmethod
    def create(cls, provider: str, label: str, **kw: Any) -> Account:
        return cls(id=uuid.uuid4().hex[:12], provider=provider, label=label, **kw)

    def touch(self) -> None:
        self.updated_at = utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "label": self.label,
            "identity": self.identity,
            "plan": self.plan,
            "auth_method": self.auth_method.value,
            "enabled": self.enabled,
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Account:
        try:
            method = AuthMethod(raw.get("auth_method", "oauth"))
        except ValueError:
            method = AuthMethod.OAUTH
        return cls(
            id=str(raw["id"]),
            provider=str(raw["provider"]),
            label=str(raw.get("label") or raw["provider"]),
            identity=str(raw.get("identity", "")),
            plan=str(raw.get("plan", "")),
            auth_method=method,
            enabled=bool(raw.get("enabled", True)),
            created_at=from_iso(raw.get("created_at")) or utcnow(),
            updated_at=from_iso(raw.get("updated_at")) or utcnow(),
        )


@dataclass(slots=True)
class AccountState:
    """Runtime view of an account: the card renders straight off this."""

    account: Account
    report: UsageReport | None = None
    status: AccountStatus = AccountStatus.UNKNOWN
    message: str = ""
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    failures: int = 0
    retry_after: datetime | None = None

    @property
    def id(self) -> str:
        return self.account.id

    def may_poll(self, *, now: datetime | None = None) -> bool:
        if not self.account.enabled:
            return False
        if self.retry_after is None:
            return True
        return (now or utcnow()) >= self.retry_after
