"""Persistence for the non-secret half: which accounts exist, and what we last
saw their usage to be.

Keeping the usage cache separate from accounts.json means a corrupt cache can be
thrown away without losing the account list.
"""

from __future__ import annotations

from pathlib import Path

from .errors import StorageError
from .fsutil import backup_corrupt_file, read_json, write_json_atomic
from .logging_setup import get_logger
from .models import Account, UsageReport
from .paths import accounts_file, cache_file

log = get_logger("store")

ACCOUNTS_VERSION = 1


class AccountRepository:
    """The account list. Small, so we just rewrite the whole file each time."""

    def __init__(self, path: Path | None = None):
        self._path = path or accounts_file()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[Account]:
        try:
            raw = read_json(self._path, default=None)
        except StorageError as exc:
            moved = backup_corrupt_file(self._path)
            log.warning("accounts file unreadable (%s), moved to %s", exc, moved)
            raise StorageError(
                str(exc),
                friendly=(
                    "Your accounts file could not be read, so it was moved aside "
                    f"to {moved.name if moved else 'a backup'}. Add your accounts again."
                ),
            ) from exc

        if raw is None:
            return []
        if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
            log.warning("accounts file has an unexpected shape, treating as empty")
            return []

        accounts: list[Account] = []
        seen: set[str] = set()
        for entry in raw["accounts"]:
            if not isinstance(entry, dict):
                continue
            try:
                account = Account.from_dict(entry)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("skipping malformed account entry: %s", type(exc).__name__)
                continue
            if account.id in seen:
                log.warning("duplicate account id in file, skipping the second one")
                continue
            seen.add(account.id)
            accounts.append(account)
        return accounts

    def save(self, accounts: list[Account]) -> None:
        write_json_atomic(
            self._path,
            {"version": ACCOUNTS_VERSION, "accounts": [a.to_dict() for a in accounts]},
        )


class UsageCache:
    """Last known usage per account id.

    Purely a nicety: it means the dashboard has numbers on it the instant it
    opens instead of a grid of spinners.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or cache_file()

    def load(self) -> dict[str, UsageReport]:
        try:
            raw = read_json(self._path, default=None)
        except StorageError:
            backup_corrupt_file(self._path)
            return {}
        if not isinstance(raw, dict):
            return {}

        out: dict[str, UsageReport] = {}
        for account_id, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            try:
                out[str(account_id)] = UsageReport.from_dict(payload)
            except (TypeError, ValueError):
                continue
        return out

    def save(self, reports: dict[str, UsageReport]) -> None:
        try:
            write_json_atomic(self._path, {k: v.to_dict() for k, v in reports.items()})
        except StorageError as exc:
            # A cache write failing should never stop the app.
            log.warning("could not write usage cache: %s", exc)

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
