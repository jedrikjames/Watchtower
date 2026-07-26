"""User settings.

config.json is a file a user might reasonably open in an editor, so loading it
never raises on a bad value. Anything unparseable falls back to the default and
gets a line in the log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .errors import StorageError
from .fsutil import backup_corrupt_file, read_json, write_json_atomic
from .logging_setup import get_logger
from .paths import config_file

log = get_logger("settings")

MIN_REFRESH_SECONDS = 30
MAX_REFRESH_SECONDS = 3600

SECRET_BACKENDS = ("auto", "keyring", "file")


def _matches_type(current: Any, value: Any) -> bool:
    """Would assigning ``value`` keep the field the type it is meant to be?

    bool is checked before int on purpose - in Python True is an int, and
    "mask_identities": 1 should be rejected rather than quietly accepted.
    """
    if isinstance(current, bool):
        return isinstance(value, bool)
    if isinstance(current, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(current, str):
        return isinstance(value, str)
    return False


@dataclass(slots=True)
class Settings:
    #: How often the dashboard re-polls. The brief asked for a minute; the
    #: providers rate limit hard below that, so 30s is the floor.
    refresh_seconds: int = 60

    #: "auto" prefers the OS keychain and falls back to the encrypted file.
    secret_backend: str = "auto"

    #: Blank out emails on the cards. Handy when you are sharing a screen.
    mask_identities: bool = False

    #: Cards at or above this percentage are drawn in the warning colour.
    warn_at_percent: int = 80

    #: And this one in the danger colour.
    danger_at_percent: int = 95

    #: Ask before deleting an account.
    confirm_remove: bool = True

    #: Try to open the system browser during sign-in. Turn off on headless
    #: boxes and Watchtower will just print the URL for you to copy.
    open_browser: bool = True

    #: Textual theme name.
    theme: str = "textual-dark"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> Settings:
        settings = cls()
        if not isinstance(raw, dict):
            return settings

        known = {f.name: f.type for f in fields(cls)}
        for key, value in raw.items():
            if key not in known:
                log.info("ignoring unknown setting %r", key)
                continue
            current = getattr(settings, key)
            if _matches_type(current, value):
                setattr(settings, key, value)
            else:
                log.info("setting %r had an unusable value, keeping the default", key)

        settings.normalise()
        return settings

    def normalise(self) -> None:
        self.refresh_seconds = max(
            MIN_REFRESH_SECONDS, min(MAX_REFRESH_SECONDS, int(self.refresh_seconds))
        )
        if self.secret_backend not in SECRET_BACKENDS:
            log.info("unknown secret_backend %r, using auto", self.secret_backend)
            self.secret_backend = "auto"
        self.warn_at_percent = max(1, min(100, int(self.warn_at_percent)))
        self.danger_at_percent = max(1, min(100, int(self.danger_at_percent)))
        if self.danger_at_percent < self.warn_at_percent:
            self.danger_at_percent = self.warn_at_percent


def load(path: Path | None = None) -> Settings:
    path = path or config_file()
    try:
        raw = read_json(path, default={})
    except StorageError as exc:
        moved = backup_corrupt_file(path)
        log.warning("could not load settings (%s); moved aside to %s", exc, moved)
        return Settings()
    return Settings.from_dict(raw)


def save(settings: Settings, path: Path | None = None) -> None:
    settings.normalise()
    write_json_atomic(path or config_file(), settings.to_dict())
