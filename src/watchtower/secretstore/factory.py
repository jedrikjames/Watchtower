from __future__ import annotations

from dataclasses import dataclass

from ..errors import StorageError
from ..logging_setup import get_logger
from .base import SecretStore
from .keyring_store import KeyringStore
from .vault import FileVault

log = get_logger("secretstore")


@dataclass(slots=True)
class BackendInfo:
    key: str
    name: str
    available: bool
    detail: str


def describe_backends() -> list[BackendInfo]:
    """For the settings screen, so the user can see what they are actually on."""
    probe = KeyringStore.probe()
    keychain = BackendInfo(
        key="keyring",
        name="OS keychain",
        available=probe is not None,
        detail=probe.description if probe else "no usable backend on this machine",
    )
    vault = FileVault()
    return [
        keychain,
        BackendInfo(
            key="file",
            name="Encrypted file",
            available=True,
            detail="exists, passphrase required" if vault.exists() else "not created yet",
        ),
    ]


def open_store(preference: str = "auto") -> SecretStore:
    """Pick a secret store.

    ``auto`` uses the OS keychain when there is one and the encrypted file when
    there is not. The returned store may be locked; the caller is responsible
    for prompting.
    """
    if preference == "file":
        return FileVault()

    if preference == "keyring":
        store = KeyringStore.probe()
        if store is None:
            raise StorageError(
                "keyring requested but unavailable",
                friendly=(
                    "No system keychain is available on this machine. "
                    "Switch the secret backend to 'file' in settings."
                ),
            )
        return store

    store = KeyringStore.probe()
    if store is not None:
        log.info("using OS keychain (%s)", store.description)
        return store

    log.info("no keychain available, falling back to the encrypted file vault")
    return FileVault()
