"""OS keychain backend.

Windows Credential Manager, macOS Keychain, or whatever SecretService provider
is running on Linux. This is the default because the OS is better at protecting
this than we are.

The chunking below is not premature cleverness. Windows caps a credential blob
at 2560 bytes (CRED_MAX_CREDENTIAL_BLOB_SIZE) and an OAuth credential holding
two JWTs goes past that without much trouble. Writing it in one go fails with a
memorably unhelpful "Stub received bad data", so we split it ourselves.
"""

from __future__ import annotations

from ..errors import StorageError
from ..logging_setup import get_logger
from .base import SecretStore

log = get_logger("secretstore.keyring")

SERVICE = "Watchtower"
INDEX_KEY = "__index__"

# Comfortably under the Windows limit once base64 and the chunk header are on.
CHUNK_SIZE = 1200

RAW_PREFIX = "r:"
CHUNK_PREFIX = "c:"


class KeyringStore(SecretStore):
    name = "OS keychain"

    def __init__(self, service: str = SERVICE):
        import keyring  # imported lazily; it pulls in platform backends

        self._keyring = keyring
        self._service = service
        self.description = self._describe_backend()

    def _describe_backend(self) -> str:
        try:
            backend = self._keyring.get_keyring()
        except Exception:  # pragma: no cover
            return "unknown backend"
        return getattr(backend, "name", None) or type(backend).__name__

    # -- availability ---------------------------------------------------

    @classmethod
    def probe(cls, service: str = SERVICE) -> KeyringStore | None:
        """Return a working store, or None if this machine has no usable keychain.

        We do a real round trip rather than trusting the backend list. A
        headless Linux box will happily report a SecretService backend that
        then fails because there is no D-Bus session.
        """
        try:
            import keyring
            from keyring.backends import fail
        except Exception as exc:
            log.info("keyring is not importable: %s", type(exc).__name__)
            return None

        try:
            if isinstance(keyring.get_keyring(), fail.Keyring):
                log.info("keyring has no usable backend on this platform")
                return None
        except Exception as exc:
            log.info("keyring backend lookup failed: %s", type(exc).__name__)
            return None

        probe_key = "__watchtower_probe__"
        try:
            keyring.set_password(service, probe_key, "ok")
            value = keyring.get_password(service, probe_key)
            keyring.delete_password(service, probe_key)
        except Exception as exc:
            log.info("keyring round trip failed: %s", type(exc).__name__)
            return None

        if value != "ok":
            log.info("keyring round trip returned the wrong value")
            return None
        return cls(service)

    # -- primitives -----------------------------------------------------

    def _read(self, key: str) -> str | None:
        try:
            return self._keyring.get_password(self._service, key)
        except Exception as exc:
            raise StorageError(
                f"keychain read failed for {key}",
                friendly="Could not read from the system keychain.",
            ) from exc

    def _write(self, key: str, value: str) -> None:
        try:
            self._keyring.set_password(self._service, key, value)
        except Exception as exc:
            raise StorageError(
                f"keychain write failed for {key}",
                friendly="Could not write to the system keychain.",
            ) from exc

    def _erase(self, key: str) -> None:
        try:
            self._keyring.delete_password(self._service, key)
        except Exception:
            # Deleting something that is not there is not an error we care about.
            pass

    # -- SecretStore ----------------------------------------------------

    def get(self, key: str) -> str | None:
        head = self._read(key)
        if head is None:
            return None
        if head.startswith(RAW_PREFIX):
            return head[len(RAW_PREFIX) :]
        if head.startswith(CHUNK_PREFIX):
            try:
                count = int(head[len(CHUNK_PREFIX) :])
            except ValueError:
                log.warning("chunk header for %s is malformed", key)
                return None
            parts = []
            for index in range(count):
                part = self._read(f"{key}#{index}")
                if part is None:
                    log.warning("chunk %s of %s is missing", index, key)
                    return None
                parts.append(part)
            return "".join(parts)
        # Written by an older build that did not use prefixes.
        return head

    def put(self, key: str, value: str) -> None:
        self._drop_chunks(key)
        if len(value) <= CHUNK_SIZE:
            self._write(key, RAW_PREFIX + value)
            self._index_add(key)
            return

        chunks = [value[i : i + CHUNK_SIZE] for i in range(0, len(value), CHUNK_SIZE)]
        for index, chunk in enumerate(chunks):
            self._write(f"{key}#{index}", chunk)
        self._write(key, f"{CHUNK_PREFIX}{len(chunks)}")
        self._index_add(key)

    def delete(self, key: str) -> None:
        self._drop_chunks(key)
        self._erase(key)
        self._index_remove(key)

    def keys(self) -> list[str]:
        # Most keyring backends cannot enumerate, so we keep our own index.
        raw = self._read(INDEX_KEY)
        if not raw:
            return []
        return [k for k in raw.split("\x1f") if k]

    def _drop_chunks(self, key: str) -> None:
        head = self._read(key)
        if head and head.startswith(CHUNK_PREFIX):
            try:
                count = int(head[len(CHUNK_PREFIX) :])
            except ValueError:
                count = 0
            for index in range(count):
                self._erase(f"{key}#{index}")

    def _index_add(self, key: str) -> None:
        current = self.keys()
        if key in current:
            return
        current.append(key)
        self._write(INDEX_KEY, "\x1f".join(current))

    def _index_remove(self, key: str) -> None:
        current = self.keys()
        if key not in current:
            return
        current.remove(key)
        if current:
            self._write(INDEX_KEY, "\x1f".join(current))
        else:
            self._erase(INDEX_KEY)
