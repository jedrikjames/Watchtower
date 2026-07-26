"""The logo widget, including the fallbacks when images are not on offer."""

from __future__ import annotations

from watchtower.providers import ClaudeProvider, CodexProvider
from watchtower.tui.widgets.logo import TextLogo, build_logo, icon_path, images_available


class TestIconAssets:
    def test_both_providers_have_a_rendered_icon(self):
        for provider_id in ("codex", "claude"):
            path = icon_path(provider_id)
            assert path is not None, f"{provider_id} has no PNG"
            assert path.stat().st_size > 0

    def test_unknown_providers_have_none(self):
        assert icon_path("a-provider-that-does-not-exist") is None


class TestFallbacks:
    """Whatever happens, a card gets a mark."""

    def test_braille_is_the_default(self):
        widget = build_logo(ClaudeProvider(), "braille", "#d97757")
        assert isinstance(widget, TextLogo)

    def test_blocks_style_uses_the_box_drawing_mark(self):
        widget = build_logo(CodexProvider(), "blocks", "#10a37f")
        assert isinstance(widget, TextLogo)

    def test_image_style_falls_back_when_the_terminal_cannot_draw(self, monkeypatch):
        """Headless, or a terminal with no graphics protocol."""
        monkeypatch.setattr("watchtower.tui.widgets.logo.terminal_supports_images", lambda: False)
        widget = build_logo(ClaudeProvider(), "image", "#d97757")
        assert isinstance(widget, TextLogo), "must fall back rather than render nothing"

    def test_image_style_falls_back_without_the_optional_extra(self, monkeypatch):
        monkeypatch.setattr("watchtower.tui.widgets.logo.images_available", lambda: False)
        monkeypatch.setattr("watchtower.tui.widgets.logo.terminal_supports_images", lambda: False)
        assert isinstance(build_logo(CodexProvider(), "image", "#10a37f"), TextLogo)

    def test_a_provider_without_an_icon_still_gets_a_mark(self, monkeypatch):
        monkeypatch.setattr("watchtower.tui.widgets.logo.terminal_supports_images", lambda: True)
        monkeypatch.setattr("watchtower.tui.widgets.logo.icon_path", lambda _: None)
        assert isinstance(build_logo(ClaudeProvider(), "image", "#d97757"), TextLogo)


def test_probing_never_raises():
    """Called at startup; an exception here would take the dashboard with it."""
    from watchtower.tui.widgets.logo import terminal_supports_images

    assert isinstance(terminal_supports_images(), bool)
    assert isinstance(images_available(), bool)


class TestStyling:
    """The mark is styled by a class we own, not by the library's type name.

    textual-image swaps the concrete class depending on which graphics protocol
    the terminal speaks - with Sixel active it is literally named "Image" - so a
    CSS type selector matches nothing and the mark silently loses its size and
    its layer. That is how it ended up invisible on a terminal that supported
    Sixel perfectly well.
    """

    def test_text_logos_carry_the_styling_hook(self):
        for style in ("braille", "blocks"):
            widget = build_logo(ClaudeProvider(), style, "#d97757")
            assert widget.has_class("card-logo")

    def test_the_stylesheet_targets_the_class_not_a_type(self):
        from pathlib import Path

        css = (Path(__file__).parent.parent / "src/watchtower/tui/app.tcss").read_text(
            encoding="utf-8"
        )
        assert ".account-card > .card-logo" in css
        for guessed in ("AutoImage", "SixelImage", "TGPImage", "HalfcellImage", "UnicodeImage"):
            assert guessed not in css, f"{guessed} is a library type name and may not match"


class TestSlotWidth:
    """A square icon in non-square cells needs its width computed, not assumed.

    Cells are typically twice as tall as they are wide, so three rows of mark
    is about six cells of width. Reserving five stretched the icon vertically.
    """

    def test_text_marks_are_five_cells(self):
        from watchtower.tui.widgets.logo import LOGO_WIDTH, logo_slot_width

        assert logo_slot_width("braille") == LOGO_WIDTH
        assert logo_slot_width("blocks") == LOGO_WIDTH

    def test_image_width_follows_the_cell_aspect(self, monkeypatch):
        from types import SimpleNamespace

        import watchtower.tui.widgets.logo as logo_mod

        def fake_cell_size(w, h):
            return lambda: SimpleNamespace(width=w, height=h)

        # 10x20 cells: 3 rows = 60px tall, so 6 cells = 60px wide. Square.
        monkeypatch.setattr("textual_image._terminal.get_cell_size", fake_cell_size(10, 20))
        assert logo_mod.logo_slot_width("image") == 6

        # 8x16 is the same 1:2 ratio, so also 6.
        monkeypatch.setattr("textual_image._terminal.get_cell_size", fake_cell_size(8, 16))
        assert logo_mod.logo_slot_width("image") == 6

        # A squarer cell needs fewer columns.
        monkeypatch.setattr("textual_image._terminal.get_cell_size", fake_cell_size(10, 12))
        assert logo_mod.logo_slot_width("image") == 4

    def test_width_is_clamped_against_a_nonsense_cell_size(self, monkeypatch):
        from types import SimpleNamespace

        import watchtower.tui.widgets.logo as logo_mod

        monkeypatch.setattr(
            "textual_image._terminal.get_cell_size", lambda: SimpleNamespace(width=1, height=400)
        )
        assert 3 <= logo_mod.logo_slot_width("image") <= 10

    def test_falls_back_when_the_cell_size_is_unreadable(self, monkeypatch):
        import watchtower.tui.widgets.logo as logo_mod

        def boom():
            raise RuntimeError("no terminal")

        monkeypatch.setattr("textual_image._terminal.get_cell_size", boom)
        assert logo_mod.logo_slot_width("image") >= 3


def test_the_body_indents_by_the_real_slot_width():
    """Otherwise a six-cell icon overlaps the account name."""
    from watchtower.settings import Settings
    from watchtower.tui.widgets.account_card import CardBody

    settings = Settings()
    body = CardBody(state=None, settings=settings, identity="", logo_width=7)
    assert body.logo_width == 7
