"""The provider mark, as its own widget.

Two reasons it is not just part of the card's render():

* Graphics protocols hate being repainted, and re-emitting a Sixel or Kitty
  image whenever the card body changes is exactly the case textual-image warns
  flickers. As a separate widget the mark is drawn once and left alone.
* It keeps both ways of drawing a mark - real icon and dot grid - in one
  place instead of branching inside the card layout.

Pillow and textual-image are ordinary dependencies, but every import of them
is still guarded: a broken or partial install should cost you the icons, not
the dashboard.
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


#: Result of the one and only probe. None means it has not been run.
_support: bool | None = None


def probe_image_support() -> bool:
    """Ask the terminal whether it can draw images, and remember the answer.

    **This has to run before the Textual app starts.** The probe writes an
    escape sequence and waits for the terminal to answer on stdin; once Textual
    is running its own input thread grabs that answer first, so the query always
    times out and every terminal looks incapable. textual-image says as much in
    its own docstring, and calling this from compose() was exactly the mistake.

    Importing textual_image.renderable *is* the probe - it picks a renderer at
    import time - so the import is deliberately kept in here.
    """
    global _support
    if _support is not None:
        return _support

    if not images_available():
        _support = False
        return _support

    try:
        from textual_image.renderable import Image as Chosen
        from textual_image.renderable.sixel import Image as SixelImage
        from textual_image.renderable.tgp import Image as TGPImage
    except Exception as exc:  # pragma: no cover - depends on the installed version
        log.info("could not probe terminal image support: %s", type(exc).__name__)
        _support = False
        return _support

    chosen = Chosen.__module__.rsplit(".", 1)[-1]
    _support = Chosen in (SixelImage, TGPImage)
    log.info("terminal image support: %s (renderer: %s)", _support, chosen)
    return _support


def terminal_supports_images() -> bool:
    """Whether images can be drawn. Never probes - see probe_image_support.

    textual-image falls back to half-block or Unicode approximations when no
    graphics protocol is available. Those look worse than our dot marks at this
    size, so "no protocol" means "no images" rather than silently handing the
    user something uglier than the default.
    """
    if _support is None:
        log.warning("image support was never probed; falling back to dots")
        return False
    return _support


def logo_slot_width(style: str) -> int:
    """How many cells wide the mark needs.

    Text marks are five cells because that is how they were drawn. An image is
    square, and terminal cells are not: at a typical 10x20 the three rows we
    give it are 60px tall, so it needs six cells to be 60px wide too. Getting
    this wrong is what makes the icon look stretched vertically, and since cell
    size depends on the reader's font it has to be measured, not assumed.
    """
    if style != "image":
        return LOGO_WIDTH
    try:
        from textual_image._terminal import get_cell_size

        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            wanted = round(LOGO_HEIGHT * cell.height / cell.width)
            return max(3, min(10, wanted))
    except Exception as exc:  # pragma: no cover - terminal dependent
        log.info("could not read the cell size: %s", type(exc).__name__)
    return LOGO_WIDTH + 1


def icon_path(provider_id: str) -> Path | None:
    candidate = ASSETS / f"{_ICON_NAMES.get(provider_id, provider_id)}.png"
    return candidate if candidate.is_file() else None


#: provider id -> the Bootstrap Icons file name it maps to
_ICON_NAMES = {"codex": "openai", "claude": "claude"}


class TextLogo(Static):
    """The dot-grid mark. Renders once and never changes."""

    def __init__(self, lines: tuple[str, ...], colour: str, **kw) -> None:
        text = Text("\n".join(lines), style=colour, no_wrap=True)
        super().__init__(text, **kw)
        self.add_class("card-logo")


def build_logo(provider, style: str, colour: str) -> Widget:
    """Pick the best mark this machine can actually draw.

    Falls back from image to dots rather than failing, because a card without
    a logo is still a useful card.
    """
    if style == "image":
        path = icon_path(provider.info.id)
        if path is not None and terminal_supports_images():
            try:
                from textual_image.widget import Image

                widget = Image(str(path))
                widget.add_class("card-logo")
                # Width comes from the measured cell aspect, not the CSS, so
                # the icon stays square whatever font the reader uses.
                widget.styles.width = logo_slot_width("image")
                return widget
            except Exception as exc:
                log.info("image logo unavailable, using dots: %s", type(exc).__name__)
        elif path is None:
            log.info("no icon asset for %s, using dots", provider.info.id)
        else:
            log.info("terminal has no graphics protocol, using dots")

    return TextLogo(provider.info.logo, colour)
