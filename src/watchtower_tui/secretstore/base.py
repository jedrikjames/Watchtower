from __future__ import annotations

import abc


class SecretStore(abc.ABC):
    """A tiny string-to-string map that keeps its values away from other users.

    Keys are opaque ASCII identifiers chosen by us, e.g. ``account:9f3c...``.
    Values are UTF-8 strings; in practice a JSON credential blob.
    """

    #: Shown in the settings screen.
    name: str = "store"
    description: str = ""

    @property
    def locked(self) -> bool:
        """True when the store needs a passphrase before it can be read."""
        return False

    def unlock(self, passphrase: str) -> None:  # pragma: no cover - overridden where relevant
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, key: str) -> str | None: ...

    @abc.abstractmethod
    def put(self, key: str, value: str) -> None: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def keys(self) -> list[str]: ...

    def close(self) -> None:
        """Drop any key material held in memory."""
