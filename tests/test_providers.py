"""Usage parsing, against payloads shaped like the ones the CLIs receive.

These are the parts most likely to break when a vendor changes something, so
they are pinned hard and they all assert graceful degradation rather than an
exception escaping to the UI.
"""

from __future__ import annotations

import pytest

from watchtower.errors import UsageUnavailable
from watchtower.providers.claude import ClaudeProvider
from watchtower.providers.codex import CodexProvider, _window_label


class TestClaude:
    def test_parses_the_standard_windows(self):
        report = ClaudeProvider.parse_usage(
            {
                "five_hour": {"utilization": 42.5, "resets_at": "2026-07-26T20:00:00Z"},
                "seven_day": {"utilization": 18, "resets_at": "2026-08-01T00:00:00Z"},
                "seven_day_opus": {"utilization": 3},
            }
        )
        assert [w.label for w in report.windows] == ["5-hour", "Weekly", "Weekly (Opus)"]
        assert report.windows[0].percent == 42.5
        assert report.windows[0].resets_at is not None
        assert report.headline.label == "5-hour"

    def test_unknown_windows_still_show_up(self):
        """A new limit type appearing server-side must not vanish from the UI."""
        report = ClaudeProvider.parse_usage(
            {"five_hour": {"utilization": 1}, "monthly_opus": {"utilization": 9}}
        )
        assert "Monthly opus" in [w.label for w in report.windows]

    def test_nested_under_rate_limits(self):
        report = ClaudeProvider.parse_usage({"rate_limits": {"five_hour": {"utilization": 5}}})
        assert report.windows[0].percent == 5

    def test_percentages_are_clamped(self):
        report = ClaudeProvider.parse_usage({"five_hour": {"utilization": 1000}})
        assert report.windows[0].value == 100.0

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {},
            {"five_hour": "not a dict"},
            {"five_hour": {"utilization": "high"}},
            {"rate_limits": {}},
        ],
    )
    def test_unusable_payloads_degrade(self, payload):
        with pytest.raises(UsageUnavailable):
            ClaudeProvider.parse_usage(payload)

    def test_plan_names_are_tidied(self):
        assert ClaudeProvider._plan_name("max_20x") == "Max 20x"
        assert ClaudeProvider._plan_name({"type": "pro"}) == "Pro"
        assert ClaudeProvider._plan_name("") == ""


class TestCodex:
    def test_parses_primary_and_secondary(self):
        report = CodexProvider.parse_usage(
            {
                "rate_limits": {
                    "primary": {
                        "used_percent": 12.5,
                        "window_minutes": 300,
                        "resets_in_seconds": 3600,
                    },
                    "secondary": {
                        "used_percent": 66.0,
                        "window_minutes": 10080,
                        "resets_in_seconds": 400000,
                    },
                },
                "plan_type": "pro",
            }
        )
        assert [w.label for w in report.windows] == ["5-hour", "Weekly"]
        assert report.plan == "Pro"
        assert report.windows[0].resets_at is not None
        assert report.headline.label == "Weekly"

    def test_window_lengths_are_read_not_assumed(self):
        report = CodexProvider.parse_usage(
            {"rate_limits": {"primary": {"used_percent": 1, "window_minutes": 180}}}
        )
        assert report.windows[0].label == "3-hour"

    @pytest.mark.parametrize(
        "minutes,expected",
        [
            (10080, "Weekly"),
            (1440, "Daily"),
            (300, "5-hour"),
            (30, "30-minute"),
            (4320, "3-day"),
            (None, "x"),
        ],
    )
    def test_window_labels(self, minutes, expected):
        assert _window_label(minutes, "x") == expected

    @pytest.mark.parametrize("payload", [None, {}, {"rate_limits": {"primary": {}}}])
    def test_unusable_payloads_degrade(self, payload):
        with pytest.raises(UsageUnavailable):
            CodexProvider.parse_usage(payload)


def test_oauth_endpoints_are_sane():
    for provider in (ClaudeProvider(), CodexProvider()):
        endpoints = provider.oauth_endpoints()
        assert endpoints.authorize_url.startswith("https://")
        assert endpoints.token_url.startswith("https://")
        assert endpoints.client_id
        assert endpoints.redirect_port > 1024
        assert endpoints.token_request_style in ("form", "json")


def test_endpoints_can_be_overridden_by_env(monkeypatch):
    """The escape hatch for when a vendor moves an undocumented endpoint."""
    monkeypatch.setenv("WATCHTOWER_CLAUDE_USAGE_URL", "https://example.test/usage")
    assert (
        ClaudeProvider().env_override("WATCHTOWER_CLAUDE_USAGE_URL", "fallback")
        == "https://example.test/usage"
    )
    monkeypatch.setenv("WATCHTOWER_CLAUDE_USAGE_URL", "   ")
    assert ClaudeProvider().env_override("WATCHTOWER_CLAUDE_USAGE_URL", "fallback") == "fallback"
