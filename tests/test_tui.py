"""The app boots, renders, and the keyboard flows do what they say.

Driven through Textual's Pilot, so these are real key presses against a real
(headless) app rather than direct method calls.
"""

from __future__ import annotations

import pytest

import watchtower_tui.providers as registry
from watchtower_tui.models import AccountStatus, AuthMethod, Credential, UsageReport, UsageWindow
from watchtower_tui.providers.base import Identity, Provider, ProviderInfo
from watchtower_tui.secretstore.vault import FileVault
from watchtower_tui.service import AccountManager
from watchtower_tui.tui import WatchtowerApp
from watchtower_tui.tui.widgets import AccountCard


class StubProvider(Provider):
    info = ProviderInfo(
        id="stub",
        display_name="Stub",
        accent="#d97757",
        logo=("╲ │ ╱", "──╋──", "╱ │ ╲"),
        signin_label="Sign in",
    )

    async def fetch_usage(self, credential):
        return UsageReport(
            windows=[UsageWindow("5h", "5-hour", 42.0), UsageWindow("7d", "Weekly", 7.0)],
            plan="Pro",
        )

    async def identify(self, credential):
        return Identity()


@pytest.fixture
def stub():
    registry._PROVIDERS["stub"] = StubProvider()
    yield
    registry._PROVIDERS.pop("stub", None)


def build(settings, labels=("Alpha", "Beta")):
    vault = FileVault()
    vault.create("test passphrase")
    manager = AccountManager(settings, store=vault)
    manager.load()
    for label in labels:
        manager.create_account(
            "stub",
            label,
            Credential(method=AuthMethod.OAUTH, access_token="at-value"),
            Identity(email=f"{label.lower()}@example.test"),
        )
    return WatchtowerApp(settings, manager), manager


async def boot(pilot, app):
    for _ in range(60):
        if app._booted:
            break
        await pilot.pause(0.05)
    await pilot.pause(0.3)


def screen_text(app) -> str:
    return "\n".join(
        "".join(segment.text for segment in strip)
        for strip in app.screen._compositor.render_strips()
    )


async def test_dashboard_shows_a_card_per_account(settings, stub):
    app, _ = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        cards = list(app.query(AccountCard))
        assert len(cards) == 2

        text = screen_text(app)
        assert "Alpha" in text and "Beta" in text
        assert "5-hour" in text and "Weekly" in text
        assert "42%" in text, "the percentage must be visible at a glance"
        assert "╋" in text, "the provider logo should be drawn"
        assert "2 accounts" in text


async def test_no_token_material_is_ever_rendered(settings, stub):
    app, _ = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        assert "at-value" not in screen_text(app)


async def test_empty_state_invites_the_first_account(settings, stub):
    app, _ = build(settings, labels=())
    async with app.run_test(size=(90, 24)) as pilot:
        await boot(pilot, app)
        text = screen_text(app)
        assert "No accounts yet" in text
        assert "press a to add an account" in text
        assert not list(app.query(AccountCard))


async def test_arrow_keys_move_between_cards(settings, stub):
    app, _ = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        app.query(AccountCard).first().focus()
        await pilot.pause(0.1)
        first = app.focused.account_id

        await pilot.press("right")
        await pilot.pause(0.1)
        assert app.focused.account_id != first

        await pilot.press("left")
        await pilot.pause(0.1)
        assert app.focused.account_id == first


async def test_rename_flow(settings, stub):
    app, manager = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        app.query(AccountCard).first().focus()
        await pilot.pause(0.1)
        account_id = app.focused.account_id

        await pilot.press("e")
        await pilot.pause(0.4)
        for _ in range(20):
            await pilot.press("backspace")
        await pilot.press(*"Renamed")
        await pilot.press("enter")
        await pilot.pause(0.4)

        assert manager.get(account_id).account.label == "Renamed"
        assert "Renamed" in screen_text(app)


async def test_remove_asks_first_and_can_be_declined(settings, stub):
    app, _ = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        app.query(AccountCard).first().focus()
        await pilot.pause(0.1)

        await pilot.press("d")
        await pilot.pause(0.4)
        assert "Remove" in screen_text(app)

        await pilot.press("n")
        await pilot.pause(0.4)
        assert len(list(app.query(AccountCard))) == 2

        await pilot.press("d")
        await pilot.pause(0.4)
        await pilot.press("y")
        await pilot.pause(0.5)
        assert len(list(app.query(AccountCard))) == 1


async def test_pause_and_resume(settings, stub):
    app, manager = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        app.query(AccountCard).first().focus()
        await pilot.pause(0.1)
        account_id = app.focused.account_id

        await pilot.press("space")
        await pilot.pause(0.4)
        assert manager.get(account_id).account.enabled is False
        assert manager.get(account_id).status is AccountStatus.DISABLED

        await pilot.press("space")
        await pilot.pause(0.4)
        assert manager.get(account_id).account.enabled is True


async def test_help_and_settings_open_and_close(settings, stub):
    app, _ = build(settings)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)

        await pilot.press("question_mark")
        await pilot.pause(0.4)
        assert "Keyboard" in screen_text(app)
        await pilot.press("escape")
        await pilot.pause(0.3)

        await pilot.press("s")
        await pilot.pause(0.4)
        assert "Settings" in screen_text(app)
        assert "Refresh every" in screen_text(app)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert len(app.screen_stack) == 1


async def test_add_screen_offers_both_providers(settings):
    """No stub fixture here, so the real Codex and Claude adapters are listed."""
    vault = FileVault()
    vault.create("test passphrase")
    manager = AccountManager(settings, store=vault)
    app = WatchtowerApp(settings, manager)
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        await pilot.press("a")
        await pilot.pause(0.5)
        text = screen_text(app)
        assert "Sign in with ChatGPT" in text
        assert "Sign in with Claude" in text


async def test_masking_setting_hides_the_email(settings, stub):
    settings.mask_identities = True
    app, _ = build(settings, labels=("Alpha",))
    async with app.run_test(size=(104, 30)) as pilot:
        await boot(pilot, app)
        text = screen_text(app)
        assert "alpha@example.test" not in text
        assert "@example.test" in text
