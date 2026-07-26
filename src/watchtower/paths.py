"""Where we keep things on disk.

Windows        %APPDATA%\\Watchtower
macOS          ~/Library/Application Support/Watchtower
Linux/BSD      $XDG_CONFIG_HOME/Watchtower  (usually ~/.config/Watchtower)

Set WATCHTOWER_HOME to override, which is what the test suite does and what you
want for a portable install on a USB stick.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_path

from .errors import StorageError

APP_NAME = "Watchtower"
ENV_HOME = "WATCHTOWER_HOME"


def _from_env() -> Path | None:
    raw = os.environ.get(ENV_HOME, "").strip()
    if not raw:
        return None
    # expanduser first so "~/foo" works, then resolve to kill any ".." games
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:  # pragma: no cover - only on exotic filesystems
        raise StorageError(
            f"{ENV_HOME} is not a usable path", friendly=f"{ENV_HOME} is not a usable path."
        ) from exc
    if candidate.exists() and not candidate.is_dir():
        raise StorageError(
            f"{ENV_HOME} points at a file",
            friendly=f"{ENV_HOME} points at a file, not a folder.",
        )
    return candidate


def app_dir() -> Path:
    """The application directory. Created on first use with tight permissions."""
    from .fsutil import ensure_private_dir  # local import, fsutil imports us back

    root = _from_env()
    if root is None:
        # appauthor=False keeps it at %APPDATA%\Watchtower instead of
        # %APPDATA%\Watchtower\Watchtower, which is what everyone actually expects.
        root = Path(user_config_path(APP_NAME, appauthor=False, roaming=True))
    ensure_private_dir(root)
    return root


def accounts_file() -> Path:
    return app_dir() / "accounts.json"


def config_file() -> Path:
    return app_dir() / "config.json"


def vault_file() -> Path:
    return app_dir() / "secrets.vault"


def log_file() -> Path:
    return app_dir() / "watchtower.log"


def cache_file() -> Path:
    """Last known usage snapshots, so the dashboard is not empty on startup."""
    return app_dir() / "usage-cache.json"
