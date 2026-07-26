from __future__ import annotations

import pytest

from watchtower import logging_setup, paths


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own app directory. Nothing touches the real one."""
    home = tmp_path / "watchtower"
    monkeypatch.setenv(paths.ENV_HOME, str(home))
    # fsutil caches which paths it has already hardened; a fresh tmp_path each
    # test would otherwise be skipped after the first one.
    from watchtower import fsutil

    fsutil._hardened.clear()
    logging_setup.forget_secrets()
    yield home


@pytest.fixture
def settings():
    from watchtower.settings import Settings

    value = Settings()
    value.secret_backend = "file"
    return value
