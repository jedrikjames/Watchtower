"""The chrome: no Textual defaults, transparent background, our own hints."""

from __future__ import annotations

from pathlib import Path

import pytest

from watchtower_tui.models import AuthMethod, Credential, UsageReport, UsageWindow
from watchtower_tui.providers.base import Identity, Provider, ProviderInfo
from watchtower_tui.secretstore.vault import FileVault
from watchtower_tui.service import AccountManager
from watchtower_tui.tui import WatchtowerApp
from watchtower_tui.tui.widgets import CardBody, KeyHints, StatusLine

CSS = Path(__file__).parent.parent / "src/watchtower_tui/tui/app.tcss"


class Stub(Provider):
    info = ProviderInfo(
        id="stub", display_name="Stub", accent="#888888", logo=("a", "b", "c"), signin_label="in"
    )

    async def fetch_usage(self, credential):
        return UsageReport(windows=[UsageWindow("5h", "5-hour", 10.0)])

    async def identify(self, credential):
        return Identity()


@pytest.fixture
def stub():
    import watchtower_tui.providers as registry

    registry._PROVIDERS["stub"] = Stub()
    yield
    registry._PROVIDERS.pop("stub", None)


def build(settings, count=1):
    vault = FileVault()
    vault.create("test passphrase")
    manager = AccountManager(settings, store=vault)
    manager.load()
    for i in range(count):
        manager.create_account(
            "stub", f"A{i}", Credential(method=AuthMethod.OAUTH, access_token="t"), Identity()
        )
    return WatchtowerApp(settings, manager), manager


async def boot(pilot, app):
    for _ in range(60):
        if app._booted:
            break
        await pilot.pause(0.05)
    await pilot.pause(0.3)


class TestTextualDefaultsAreOff:
    def test_command_palette_is_disabled(self):
        assert WatchtowerApp.ENABLE_COMMAND_PALETTE is False

    async def test_ctrl_q_does_not_quit(self, settings, stub):
        app, _ = build(settings)
        async with app.run_test(size=(80, 16)) as pilot:
            await boot(pilot, app)
            await pilot.press("ctrl+q")
            await pilot.pause(0.3)
            assert app.is_running, "ctrl+q is Textual's binding, not ours"

    async def test_ctrl_p_opens_nothing(self, settings, stub):
        app, _ = build(settings)
        async with app.run_test(size=(80, 16)) as pilot:
            await boot(pilot, app)
            await pilot.press("ctrl+p")
            await pilot.pause(0.3)
            assert len(app.screen_stack) == 1

    async def test_notifications_go_to_the_status_line_not_a_toast(self, settings, stub):
        """Toasts are gone, but an error still has to reach the user."""
        app, _ = build(settings)
        async with app.run_test(size=(90, 16)) as pilot:
            await boot(pilot, app)
            app.notify("Could not reach the provider.", severity="error")
            await pilot.pause(0.3)
            rendered = str(app.query_one(StatusLine).render())
            assert "Could not reach the provider." in rendered


class TestKeyHints:
    async def test_hints_are_shown_at_the_top(self, settings, stub):
        app, _ = build(settings)
        async with app.run_test(size=(90, 16)) as pilot:
            await boot(pilot, app)
            hints = app.query_one(KeyHints)
            assert hints.size.height >= 1, "a zero-height widget silently vanishes"
            text = str(hints.render())
            for key, label in (("a", "Add"), ("s", "Settings"), ("?", "Help"), ("q", "Quit")):
                assert key in text and label in text

    def test_rename_remove_and_refresh_are_not_advertised(self):
        from watchtower_tui.tui.widgets.keyhints import HINTS

        shown = {label for _, label in HINTS}
        assert not shown & {"Rename", "Remove", "Refresh"}

    def test_the_keys_are_drawn_as_reversed_caps(self):
        from watchtower_tui.tui.widgets.keyhints import KeyHints as K

        spans = K().render().spans
        assert any("on white" in str(span.style) for span in spans)

    async def test_the_textual_footer_is_gone(self, settings, stub):
        from textual.widgets import Footer

        app, _ = build(settings)
        async with app.run_test(size=(90, 16)) as pilot:
            await boot(pilot, app)
            assert not list(app.query(Footer))


class TestTransparency:
    def test_the_main_chrome_uses_the_terminal_background(self):
        css = CSS.read_text(encoding="utf-8")
        for block in ("Screen {", "#topbar {", "#board {", ".account-card {"):
            start = css.index(block)
            body = css[start : css.index("}", start)]
            assert "background: transparent;" in body, f"{block} should not paint a background"

    def test_modals_keep_a_background(self):
        """An overlay has to be readable against whatever is behind it."""
        css = CSS.read_text(encoding="utf-8")
        start = css.index(".modal {")
        assert "background: $panel;" in css[start : css.index("}", start)]


class TestCardsDoNotTick:
    async def test_the_second_tick_leaves_the_cards_alone(self, settings, stub):
        """Repainting a card redraws its mark, and a Sixel redrawn at 1Hz flickers."""
        app, _ = build(settings)
        async with app.run_test(size=(90, 16)) as pilot:
            await boot(pilot, app)
            body = app.query_one(CardBody)
            calls = []
            body.refresh = lambda *a, **k: calls.append(1)  # type: ignore[method-assign]

            app._tick()
            app._tick()
            app._tick()
            await pilot.pause(0.2)

            assert calls == [], "the tick must not refresh card bodies"


def test_layer_names_are_declared_on_the_screen():
    """Textual resolves `layer:` against the screen's layer list.

    A name it cannot find sorts below everything, which put the mark behind the
    card body and made the logo invisible. Declaring them only on the card is
    not enough.
    """
    css = CSS.read_text(encoding="utf-8")
    screen = css[css.index("Screen {") : css.index("}", css.index("Screen {"))]
    assert "layers:" in screen
    layers = screen[screen.index("layers:") + len("layers:") :].split(";")[0].split()
    assert "body" in layers and "mark" in layers
    assert layers.index("mark") > layers.index("body"), "mark must be drawn on top"


class TestReordering:
    """`[` and `]` move a card. These press the real characters.

    The original bindings said "bracketleft"/"bracketright", which Textual
    never emits - it calls them left_square_bracket and right_square_bracket.
    The old test pressed the wrong name too, so it synthesised an event that
    matched the wrong binding and passed while the feature was broken.
    """

    async def test_bracket_keys_reorder_cards(self, settings, stub):
        from watchtower_tui.tui.widgets import AccountCard

        app, manager = build(settings, count=3)
        async with app.run_test(size=(120, 20)) as pilot:
            await boot(pilot, app)
            labels = lambda: [  # noqa: E731
                manager.get(c.account_id).account.label for c in app.query(AccountCard)
            ]
            assert labels() == ["A0", "A1", "A2"]

            app.query(AccountCard).first().focus()
            await pilot.pause(0.2)
            await pilot.press("]")  # the actual key, not a made-up name
            await pilot.pause(0.4)
            assert labels() == ["A1", "A0", "A2"], "] should move the card right"

            await pilot.press("[")
            await pilot.pause(0.4)
            assert labels() == ["A0", "A1", "A2"], "[ should move it back"

    def test_the_bindings_use_textual_key_names(self):
        from textual.keys import _character_to_key

        bound = {b.key for b in WatchtowerApp.BINDINGS if "square" in b.key or "bracket" in b.key}
        assert _character_to_key("[") in bound
        assert _character_to_key("]") in bound
