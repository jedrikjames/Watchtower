"""Careful file handling.

Two things matter here:

1. Writes are atomic. A power cut halfway through saving accounts.json should
   not leave you with a truncated file and no accounts.
2. Nothing we create is readable by other users on the machine. On POSIX that
   is a chmod. On Windows it means breaking ACL inheritance and granting the
   current user only, which we shell out to icacls for so we do not have to
   depend on pywin32.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .errors import StorageError

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Config files should never be big. If one is, something is wrong and we would
# rather say so than try to parse 400 MB of JSON.
MAX_CONFIG_BYTES = 2 * 1024 * 1024

_hardened: set[str] = set()


def _harden_windows(path: Path) -> None:
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - getuser is very hard to break
        log.debug("could not determine current user, skipping ACL tightening")
        return

    domain = os.environ.get("USERDOMAIN", "")
    principal = f"{domain}\\{user}" if domain else user

    # (OI)(CI) are *inheritance* flags and only mean anything on a container.
    # Putting them on a file yields an inherit-only ACE that grants the file
    # itself nothing, which combined with /inheritance:r locks us out of our
    # own data. Directories get the inheritance flags, files get plain F.
    rights = "(OI)(CI)F" if path.is_dir() else "F"

    # /inheritance:r drops inherited ACEs, /grant:r replaces any existing ACE
    # for the principal rather than adding a second one.
    cmd = ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:{rights}", "/Q"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("icacls could not be run: %s", type(exc).__name__)
        return
    if result.returncode != 0:
        # Not fatal. Roaming profiles and network shares reject this fairly
        # often and the vault is encrypted anyway.
        log.warning(
            "could not tighten permissions on the data directory (icacls exited %s)",
            result.returncode,
        )


def harden_path(path: Path) -> None:
    """Best effort: make ``path`` readable by the current user only."""
    key = str(path)
    if key in _hardened:
        return
    _hardened.add(key)

    if IS_WINDOWS:
        _harden_windows(path)
        return

    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except OSError as exc:
        log.warning("could not set permissions on %s: %s", path.name, type(exc).__name__)


def ensure_private_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(
            f"cannot create {path}",
            friendly=f"Could not create the data folder at {path}.",
        ) from exc
    harden_path(path)
    return path


def write_bytes_atomic(path: Path, data: bytes, *, private: bool = True) -> None:
    """Write ``data`` to ``path`` via a temp file in the same directory.

    Same directory matters: os.replace is only atomic within one filesystem.
    """
    ensure_private_dir(path.parent)
    tmp_name: str | None = None
    try:
        # mkstemp already creates the file 0600 and O_EXCL, which is exactly
        # what we want for a file that is about to hold a vault.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise StorageError(
            f"cannot write {path.name}",
            friendly=f"Could not save {path.name}. Is the disk full or read-only?",
        ) from exc
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    if private and IS_WINDOWS:
        # Files created inside a hardened directory inherit its ACL, so this is
        # only needed when the directory itself could not be tightened.
        harden_path(path)


def write_json_atomic(path: Path, payload: Any, *, private: bool = True) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    write_bytes_atomic(path, body.encode("utf-8"), private=private)


def read_json(path: Path, *, default: Any = None) -> Any:
    """Read a JSON file, with the guard rails you want around user-editable files."""
    if not path.exists():
        return default
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StorageError(
            f"cannot stat {path.name}", friendly=f"Could not open {path.name}."
        ) from exc

    if size > MAX_CONFIG_BYTES:
        raise StorageError(
            f"{path.name} is {size} bytes",
            friendly=f"{path.name} is unexpectedly large and was not loaded.",
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StorageError(
            f"cannot read {path.name}",
            friendly=f"{path.name} could not be read. It may be corrupt.",
        ) from exc

    if not raw.strip():
        return default

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StorageError(
            f"{path.name} is not valid JSON",
            friendly=f"{path.name} is not valid JSON (line {exc.lineno}). "
            "Fix it or delete it and Watchtower will start fresh.",
        ) from exc


def backup_corrupt_file(path: Path) -> Path | None:
    """Move a file we could not parse out of the way so we can start clean."""
    if not path.exists():
        return None
    target = path.with_suffix(path.suffix + ".corrupt")
    counter = 1
    while target.exists():
        target = path.with_suffix(f"{path.suffix}.corrupt{counter}")
        counter += 1
    try:
        os.replace(path, target)
    except OSError:
        return None
    return target
