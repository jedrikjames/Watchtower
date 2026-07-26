"""Encrypted file vault - the fallback for machines with no usable keychain.

Format is a single JSON envelope holding one AES-256-GCM ciphertext. The whole
key/value map is encrypted as one blob rather than per entry, so the file does
not even reveal how many accounts you have.

  passphrase --scrypt--> 32 byte key --AES-256-GCM--> ciphertext

The KDF parameters live in the envelope and are fed to GCM as additional
authenticated data, so nobody can hand us a file with n=2 and have us accept it.

There is no key escrow and no recovery. Forget the passphrase and the accounts
have to be added again, which takes about a minute. That is the right trade for
something holding live OAuth tokens.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..errors import StorageError, VaultLocked, VaultPassphraseError
from ..fsutil import read_json, write_bytes_atomic
from ..logging_setup import get_logger
from ..paths import vault_file
from .base import SecretStore

log = get_logger("secretstore.vault")

VERSION = 1
SCRYPT_N = 2**15  # ~32 MB of memory, about 100ms on a normal laptop
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12

MIN_PASSPHRASE = 8


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any, *, field: str) -> bytes:
    if not isinstance(text, str):
        raise StorageError(
            f"vault field {field} is not a string", friendly="The vault file is damaged."
        )
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise StorageError(
            f"vault field {field} is not valid base64", friendly="The vault file is damaged."
        ) from exc


def _derive(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def _aad(header: dict[str, Any]) -> bytes:
    """Bind the KDF parameters to the ciphertext."""
    subset = {k: header[k] for k in ("version", "kdf", "n", "r", "p", "salt")}
    return json.dumps(subset, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FileVault(SecretStore):
    name = "Encrypted file"

    def __init__(self, path: Path | None = None):
        self._path = path or vault_file()
        self._key: bytearray | None = None
        self._data: dict[str, str] | None = None
        self._header: dict[str, Any] | None = None
        self.description = f"AES-256-GCM at {self._path.name}"

    # -- state ----------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    @property
    def locked(self) -> bool:
        return self._data is None

    def _require_unlocked(self) -> dict[str, str]:
        if self._data is None:
            raise VaultLocked()
        return self._data

    # -- lifecycle ------------------------------------------------------

    def create(self, passphrase: str) -> None:
        """Start a brand new vault. Refuses to clobber an existing one."""
        if self.exists():
            raise StorageError(
                "vault already exists",
                friendly="A vault already exists. Unlock it instead of creating a new one.",
            )
        self._check_passphrase(passphrase)
        salt = os.urandom(SALT_BYTES)
        self._header = {
            "version": VERSION,
            "kdf": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": _b64(salt),
        }
        self._key = bytearray(_derive(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P))
        self._data = {}
        self._flush()

    def unlock(self, passphrase: str) -> None:
        envelope = read_json(self._path)
        if not isinstance(envelope, dict):
            raise StorageError("vault is not an object", friendly="The vault file is damaged.")

        version = envelope.get("version")
        if version != VERSION:
            raise StorageError(
                f"vault version {version!r}",
                friendly=(
                    f"This vault was written by a different version of Watchtower (v{version})."
                ),
            )
        if envelope.get("kdf") != "scrypt":
            raise StorageError(
                "unknown kdf", friendly="The vault uses an unsupported key derivation."
            )

        n, r, p = envelope.get("n"), envelope.get("r"), envelope.get("p")
        if not all(isinstance(v, int) for v in (n, r, p)) or n < 2**14 or r < 1 or p < 1:
            raise StorageError(
                "kdf parameters rejected",
                friendly="The vault's key derivation settings look wrong; refusing to open it.",
            )

        salt = _unb64(envelope.get("salt"), field="salt")
        nonce = _unb64(envelope.get("nonce"), field="nonce")
        ciphertext = _unb64(envelope.get("ct"), field="ct")
        if len(salt) < 16 or len(nonce) != NONCE_BYTES:
            raise StorageError("bad salt or nonce length", friendly="The vault file is damaged.")

        header = {k: envelope[k] for k in ("version", "kdf", "n", "r", "p", "salt")}
        key = _derive(passphrase, salt, n=n, r=r, p=p)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(header))
        except InvalidTag as exc:
            raise VaultPassphraseError() from exc

        try:
            data = json.loads(plaintext)
        except json.JSONDecodeError as exc:  # pragma: no cover - GCM would have caught this
            raise StorageError(
                "vault contents are not JSON", friendly="The vault file is damaged."
            ) from exc
        if not isinstance(data, dict):
            raise StorageError(
                "vault contents are not a map", friendly="The vault file is damaged."
            )

        self._header = header
        self._key = bytearray(key)
        self._data = {str(k): str(v) for k, v in data.items()}
        log.info("vault unlocked with %d entries", len(self._data))

    def change_passphrase(self, new_passphrase: str) -> None:
        data = self._require_unlocked()
        self._check_passphrase(new_passphrase)
        salt = os.urandom(SALT_BYTES)
        self._header = {
            "version": VERSION,
            "kdf": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": _b64(salt),
        }
        self._wipe_key()
        self._key = bytearray(_derive(new_passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P))
        self._data = data
        self._flush()

    @staticmethod
    def _check_passphrase(passphrase: str) -> None:
        if len(passphrase) < MIN_PASSPHRASE:
            raise VaultPassphraseError(
                "passphrase too short",
                friendly=f"Use at least {MIN_PASSPHRASE} characters.",
            )

    # -- SecretStore ----------------------------------------------------

    def get(self, key: str) -> str | None:
        return self._require_unlocked().get(key)

    def put(self, key: str, value: str) -> None:
        self._require_unlocked()[key] = value
        self._flush()

    def delete(self, key: str) -> None:
        self._require_unlocked().pop(key, None)
        self._flush()

    def keys(self) -> list[str]:
        return sorted(self._require_unlocked())

    def close(self) -> None:
        self._wipe_key()
        self._data = None

    # -- internals ------------------------------------------------------

    def _wipe_key(self) -> None:
        if self._key is not None:
            for i in range(len(self._key)):
                self._key[i] = 0
            self._key = None

    def _flush(self) -> None:
        if self._key is None or self._data is None or self._header is None:
            raise VaultLocked()
        # A fresh nonce every write. Reusing one with the same key would be
        # catastrophic for GCM, and writes are rare enough that random 96 bit
        # nonces are not a collision risk.
        nonce = os.urandom(NONCE_BYTES)
        plaintext = json.dumps(self._data, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(bytes(self._key)).encrypt(nonce, plaintext, _aad(self._header))
        envelope = dict(self._header)
        envelope["nonce"] = _b64(nonce)
        envelope["ct"] = _b64(ciphertext)
        body = json.dumps(envelope, indent=2, sort_keys=True).encode("utf-8")
        write_bytes_atomic(self._path, body, private=True)
