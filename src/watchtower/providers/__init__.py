"""Provider adapters.

Register a new one by adding it to ``_PROVIDERS`` below. Everything else in the
app looks providers up by id, so nothing further needs to change.
"""

from __future__ import annotations

from .base import Identity, ImportCandidate, Provider, ProviderInfo
from .claude import ClaudeProvider
from .codex import CodexProvider

_PROVIDERS: dict[str, Provider] = {}


def _register(provider: Provider) -> None:
    _PROVIDERS[provider.info.id] = provider


_register(CodexProvider())
_register(ClaudeProvider())


def all_providers() -> list[Provider]:
    """In the order they should appear in the "add account" list."""
    return list(_PROVIDERS.values())


def get_provider(provider_id: str) -> Provider | None:
    return _PROVIDERS.get(provider_id)


def provider_ids() -> list[str]:
    return list(_PROVIDERS)


__all__ = [
    "ClaudeProvider",
    "CodexProvider",
    "Identity",
    "ImportCandidate",
    "Provider",
    "ProviderInfo",
    "all_providers",
    "get_provider",
    "provider_ids",
]
