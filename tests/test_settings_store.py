"""Config and account files are user-editable, so nothing here may raise."""

from __future__ import annotations

import json

import pytest

from watchtower import settings as settings_module
from watchtower.errors import StorageError
from watchtower.fsutil import read_json, write_json_atomic
from watchtower.models import Account, UsageReport, UsageWindow
from watchtower.paths import accounts_file, app_dir, config_file
from watchtower.settings import MAX_REFRESH_SECONDS, MIN_REFRESH_SECONDS, Settings
from watchtower.store import AccountRepository, UsageCache


class TestSettings:
    def test_defaults_round_trip(self):
        settings_module.save(Settings())
        assert settings_module.load() == Settings()

    def test_unknown_keys_are_ignored(self):
        write_json_atomic(config_file(), {"refresh_seconds": 90, "nonsense": True})
        loaded = settings_module.load()
        assert loaded.refresh_seconds == 90
        assert not hasattr(loaded, "nonsense")

    @pytest.mark.parametrize(
        "value,expected",
        [(1, MIN_REFRESH_SECONDS), (99999, MAX_REFRESH_SECONDS), (60, 60)],
    )
    def test_refresh_interval_is_clamped(self, value, expected):
        write_json_atomic(config_file(), {"refresh_seconds": value})
        assert settings_module.load().refresh_seconds == expected

    def test_wrong_types_fall_back_to_defaults(self):
        write_json_atomic(
            config_file(),
            {"refresh_seconds": "sixty", "mask_identities": "yes", "theme": 7},
        )
        loaded = settings_module.load()
        assert loaded.refresh_seconds == Settings().refresh_seconds
        assert loaded.mask_identities is False
        assert loaded.theme == Settings().theme

    def test_unknown_backend_falls_back_to_auto(self):
        write_json_atomic(config_file(), {"secret_backend": "carrier pigeon"})
        assert settings_module.load().secret_backend == "auto"

    def test_danger_cannot_sit_below_warn(self):
        write_json_atomic(config_file(), {"warn_at_percent": 90, "danger_at_percent": 10})
        loaded = settings_module.load()
        assert loaded.danger_at_percent >= loaded.warn_at_percent

    def test_corrupt_config_is_moved_aside_not_fatal(self):
        config_file().write_text("{ this is not json")
        assert settings_module.load() == Settings()
        assert list(app_dir().glob("config.json.corrupt*"))


class TestAccountRepository:
    def test_round_trip(self):
        repo = AccountRepository()
        accounts = [Account.create("codex", "Work"), Account.create("claude", "Home")]
        repo.save(accounts)
        assert [a.label for a in repo.load()] == ["Work", "Home"]

    def test_missing_file_is_an_empty_list(self):
        assert AccountRepository().load() == []

    def test_malformed_entries_are_skipped_not_fatal(self):
        write_json_atomic(
            accounts_file(),
            {
                "version": 1,
                "accounts": [
                    {"id": "1", "provider": "codex", "label": "Good"},
                    {"provider": "codex"},  # no id
                    "not an object",
                    {"id": "1", "provider": "codex", "label": "Duplicate id"},
                ],
            },
        )
        loaded = AccountRepository().load()
        assert [a.label for a in loaded] == ["Good"]

    def test_unexpected_shape_reads_as_empty(self):
        write_json_atomic(accounts_file(), {"version": 1, "accounts": "nope"})
        assert AccountRepository().load() == []

    def test_corrupt_file_is_moved_aside(self):
        accounts_file().write_text("not json at all")
        with pytest.raises(StorageError) as caught:
            AccountRepository().load()
        assert "moved aside" in caught.value.friendly
        assert list(app_dir().glob("accounts.json.corrupt*"))


class TestUsageCache:
    def test_round_trip(self):
        cache = UsageCache()
        cache.save({"abc": UsageReport(windows=[UsageWindow("5h", "5-hour", 42.0)], plan="Pro")})
        loaded = cache.load()
        assert loaded["abc"].plan == "Pro"
        assert loaded["abc"].windows[0].percent == 42.0

    def test_corrupt_cache_is_discarded_silently(self):
        cache = UsageCache()
        cache.save({"abc": UsageReport()})
        cache._path.write_text("{{{")
        assert cache.load() == {}


class TestFileHandling:
    def test_atomic_write_leaves_no_temp_files(self):
        target = app_dir() / "thing.json"
        write_json_atomic(target, {"a": 1})
        assert read_json(target) == {"a": 1}
        assert not list(app_dir().glob(".thing.json.*"))

    def test_overwrite_keeps_the_old_file_on_failure(self):
        target = app_dir() / "thing.json"
        write_json_atomic(target, {"generation": 1})
        write_json_atomic(target, {"generation": 2})
        assert read_json(target) == {"generation": 2}

    def test_oversized_config_is_refused(self):
        target = app_dir() / "huge.json"
        target.write_text(json.dumps({"padding": "x" * (3 * 1024 * 1024)}))
        with pytest.raises(StorageError):
            read_json(target)

    def test_empty_file_reads_as_the_default(self):
        target = app_dir() / "empty.json"
        target.write_text("   ")
        assert read_json(target, default={"fallback": True}) == {"fallback": True}

    def test_app_dir_is_created_and_usable(self):
        directory = app_dir()
        assert directory.is_dir()
        probe = directory / "probe.json"
        write_json_atomic(probe, {"ok": True})
        assert read_json(probe) == {"ok": True}


class TestPaths:
    def test_env_override_pointing_at_a_file_is_rejected(self, tmp_path, monkeypatch):
        from watchtower.paths import ENV_HOME

        target = tmp_path / "a-file"
        target.write_text("x")
        monkeypatch.setenv(ENV_HOME, str(target))
        with pytest.raises(StorageError):
            app_dir()
