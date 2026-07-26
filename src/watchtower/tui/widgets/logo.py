"""The provider mark, as its own widget.

Two reasons it is not just part of the card's render():

* Graphics protocols hate being repainted. The card redraws every second to
  keep "updated 12s ago" honest, and re-emitting a Sixel or Kitty image that
  often is exactly the case textual-image warns flickers. As a separate widget
  it is drawn once and left alone.
* It keeps the three ways of drawing a mark - image, braille, box-drawing - in
  one place instead of branching inside the card layout.

Image support is optional. `pip install watchtower-tui[images]` pulls in
Pillow and textual-image; without them this quietly falls back to braille,
which is why every import of them is guarded.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from ...logging_setup import get_logger

log = get_logger("tui.logo")

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"

LOGO_WIDTH = 5
LOGO_HEIGHT = 3


def images_available() -> bool:
    """True when the optional image extra is installed."""
    try:
        import PIL  # noqa: F401
        import textual_image.widget  # noqa: F401
    except ImportError:
        return False
    return True


def terminal_supports_images() -> bool:
    """True when the terminal can actually draw one.

    textual-image falls back to half-block or Unicode approximations when no
    graphics protocol is available. Those look considerably worse than our
    braille at this size, so treat "no protocol" as "no images" rather than
    silently handing the user something uglier than the default.
    """
    if not images_available():
        return False
    try:
        # textual_image picks a renderer at import time by querying the
        # terminal, so importing it *is* the probe. It settles on halfcell or
        # unicode when nothing better is available.
        from textual_image.renderable import Image as Chosen
        from textual_image.renderable.sixel import Image as SixelImage
        from textual_image.renderable.tgp import Image as TGPImage
    except Exception as exc:  # pragma: no cover - depends on the installed version
        log.info("could not probe terminal image support: %s", type(exc).__name__)
        return False

    supported = Chosen in (SixelImage, TGPImage)
    log.info("terminal image support: %s (%s)", supported, Chosen.__module__.rsplit(".", 1)[-1])
    return supported


def icon_path(provider_id: str) -> Path | None:
    candidate = ASSETS / f"{_ICON_NAMES.get(provider_id, provider_id)}.png"
    return candidate if candidate.is_file() else None


#: provider id -> the Bootstrap Icons file name it maps to
_ICON_NAMES = {"codex": "openai", "claude": "claude"}


class TextLogo(Static):
    """Braille or box-drawing mark. Renders once and never changes."""

    def __init__(self, lines: tuple[str, ...], colour: str, **kw) -> None:
        text = Text("\n".join(lines), style=colour, no_wrap=True)
        super().__init__(text, **kw)


def build_logo(provider, style: str, colour: str) -> Widget:
    """Pick the best mark this machine can actually draw.

    Falls back down the chain image -> braille -> blocks rather than failing,
    because a card without a logo is still a useful card.
    """
    if style == "image":
        path = icon_path(provider.info.id)
        if path is not None and terminal_supports_images():
            try:
                from textual_image.widget import Image

                widget = Image(str(path))
                widget.add_class("logo-image")
                return widget
            except Exception as exc:
                log.info("image logo unavailable, using braille: %s", type(exc).__name__)
        elif path is None:
            log.info("no icon asset for %s, using braille", provider.info.id)
        else:
            log.info("terminal has no graphics protocol, using braille")

    if style == "blocks" and provider.info.logo_blocks:
        return TextLogo(provider.info.logo_blocks, colour)
    return TextLogo(provider.info.logo, colour)
