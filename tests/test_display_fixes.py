"""Regressions for four things that were wrong in the live app."""

from __future__ import annotations

import pytest

from watchtower.logos import CLAUDE_BRAILLE, OPENAI_BRAILLE, to_braille
from watchtower.providers.claude import ClaudeProvider, _normalise_tier
from watchtower.providers.codex import CodexProvider
from watchtower.settings import Settings
from watchtower.timefmt import until, utcnow


class TestSubscriptionTier:
    """The card showed "Stripe Subscription" - a payment method, not a plan."""

    def test_billing_mechanisms_are_never_shown_as_a_plan(self):
        for mechanism in ("stripe_subscription", "stripe", "invoice", "manual", "credit_card"):
            assert _normalise_tier(mechanism) == ""

    def test_rate_limit_tier_wins_over_billing_type(self):
        tier = ClaudeProvider._tier_from_profile(
            account={"subscription_type": None},
            organization={
                "billing_type": "stripe_subscription",
                "rate_limit_tier": "default_claude_max_20x",
            },
        )
        assert tier == "Max 20x"

    def test_a_profile_with_only_billing_type_reports_nothing(self):
        """Better a blank plan than a wrong one."""
        assert ClaudeProvider._tier_from_profile({}, {"billing_type": "stripe_subscription"}) == ""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("default_claude_max_20x", "Max 20x"),
            ("claude_max_5x", "Max 5x"),
            ("default_claude_pro", "Pro"),
            ("max", "Max"),
            ("pro", "Pro"),
            ("team", "Team"),
            ("enterprise", "Enterprise"),
            ("free", "Free"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_tier_identifiers_are_tidied(self, raw, expected):
        assert _normalise_tier(raw) == expected

    def test_multiplier_keeps_its_lowercase_x(self):
        """title() turns max_20x into "Max 20X", which looks wrong."""
        assert "20X" not in _normalise_tier("default_claude_max_20x")

    def test_boolean_flags_are_a_last_resort(self):
        assert ClaudeProvider._tier_from_profile({"has_claude_max": True}, {}) == "Max"
        assert ClaudeProvider._tier_from_profile({"has_claude_pro": True}, {}) == "Pro"

    def test_subscription_type_is_used_when_present(self):
        assert ClaudeProvider._tier_from_profile({"subscription_type": "max_20x"}, {}) == "Max 20x"


class TestNextRunCountdown:
    """The status line always read "next in now"."""

    async def test_next_run_is_set_before_the_completion_callback(self):
        """The UI reads next_run from on_complete, so it has to be fresh by then."""
        from watchtower.service.refresh import RefreshScheduler

        class FakeManager:
            settings = Settings()

            async def refresh_all(self, *, force=False):
                return []

        seen = []
        manager = FakeManager()
        scheduler = RefreshScheduler(manager, on_complete=lambda _: seen.append(scheduler.next_run))

        await scheduler._tick()

        assert seen and seen[0] is not None
        assert seen[0] > utcnow(), "next_run must be in the future when the UI reads it"
        assert until(seen[0]) not in ("", "now")

    async def test_countdown_renders_as_a_duration(self):
        from watchtower.service.refresh import RefreshScheduler

        class FakeManager:
            settings = Settings()

            async def refresh_all(self, *, force=False):
                return []

        scheduler = RefreshScheduler(FakeManager())
        await scheduler._tick()
        assert until(scheduler.next_run).endswith(("s", "m", "h"))


class TestUsageAuthFailures:
    """A 401 from an undocumented usage endpoint is not proof of a dead login."""

    async def test_a_working_credential_is_not_reported_as_expired(self, settings):
        from watchtower.errors import ReauthRequired, UsageUnavailable
        from watchtower.models import AuthMethod, Credential
        from watchtower.secretstore.vault import FileVault
        from watchtower.service import AccountManager

        class AlwaysRefusesUsage:
            info = CodexProvider.info

            async def fetch_usage(self, credential):
                raise ReauthRequired("usage says 401")

            async def refresh_credential(self, credential):
                # The credential is fine; the endpoint is the problem.
                return Credential(method=AuthMethod.OAUTH, access_token="at-fresh")

        vault = FileVault()
        vault.create("test passphrase")
        manager = AccountManager(settings, store=vault)

        with pytest.raises(UsageUnavailable) as caught:
            await manager._fetch_usage_verifying_auth(
                AlwaysRefusesUsage(), "acct", Credential(method=AuthMethod.OAUTH, access_token="at")
            )
        assert "sign in" not in caught.value.friendly.lower()
        assert "endpoint" in caught.value.friendly.lower()

    async def test_a_genuinely_dead_credential_still_asks_for_reauth(self, settings):
        from watchtower.errors import ReauthRequired
        from watchtower.models import AuthMethod, Credential
        from watchtower.secretstore.vault import FileVault
        from watchtower.service import AccountManager

        class EverythingIsDead:
            info = CodexProvider.info

            async def fetch_usage(self, credential):
                raise ReauthRequired("usage says 401")

            async def refresh_credential(self, credential):
                raise ReauthRequired("refresh token rejected")

        vault = FileVault()
        vault.create("test passphrase")
        manager = AccountManager(settings, store=vault)

        with pytest.raises(ReauthRequired):
            await manager._fetch_usage_verifying_auth(
                EverythingIsDead(), "acct", Credential(method=AuthMethod.OAUTH, access_token="at")
            )


class TestLogos:
    def test_braille_encodes_dots_in_the_right_places(self):
        assert to_braille(("#.", "..", "..", "..")) == ("\u2801",)  # dot 1
        assert to_braille((".#", "..", "..", "..")) == ("\u2808",)  # dot 4
        assert to_braille(("..", "..", "..", "#.")) == ("\u2840",)  # dot 7
        assert to_braille(("##", "##", "##", "##")) == ("\u28ff",)  # all eight
        assert to_braille(("..", "..", "..", "..")) == ("\u2800",)  # blank

    def test_marks_fit_the_card(self):
        for logo in (OPENAI_BRAILLE, CLAUDE_BRAILLE):
            assert len(logo) == 3, "three rows, to sit beside the three header lines"
            assert {len(line) for line in logo} == {5}, "five cells wide"

    def test_marks_are_not_blank(self):
        for logo in (OPENAI_BRAILLE, CLAUDE_BRAILLE):
            assert any(ch != "\u2800" for line in logo for ch in line)

    def test_the_two_marks_are_distinguishable(self):
        assert OPENAI_BRAILLE != CLAUDE_BRAILLE

    def test_logo_style_setting_is_validated(self):
        assert Settings.from_dict({"logo_style": "interpretive dance"}).logo_style == "image"
        assert Settings.from_dict({"logo_style": "dots"}).logo_style == "dots"
