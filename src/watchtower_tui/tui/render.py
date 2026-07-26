"""Small rendering helpers shared by the widgets.

Rich renderables cannot see Textual's CSS variables, so instead of inventing
style names that would never resolve we derive a concrete palette from whatever
theme is active and hand out real colours. The app calls set_palette() on
startup and whenever the theme changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.color import Color

from ..models import AccountStatus
from ..settings import Settings

FILLED = "█"
EMPTY = "░"

DEFAULT_FOREGROUND = "#e0e0e0"
DEFAULT_BACKGROUND = "#1e1e1e"


def _blend(foreground: str, background: str, factor: float) -> str:
    """Fade a colour towards the background. Used for the muted greys."""
    try:
        return Color.parse(foreground).blend(Color.parse(background), factor).hex
    except Exception:  # pragma: no cover - a theme with an unparseable colour
        return foreground


@dataclass(frozen=True, slots=True)
class Palette:
    ok: str
    warn: str
    danger: str
    muted: str
    track: str
    title: str
    subtitle: str
    accent: str

    @classmethod
    def from_theme(cls, theme: object) -> Palette:
        foreground = str(getattr(theme, "foreground", None) or DEFAULT_FOREGROUND)
        background = str(getattr(theme, "background", None) or DEFAULT_BACKGROUND)
        return cls(
            ok=str(getattr(theme, "success", None) or "#4ebf71"),
            warn=str(getattr(theme, "warning", None) or "#ffa62b"),
            danger=str(getattr(theme, "error", None) or "#ba3c5b"),
            muted=_blend(foreground, background, 0.45),
            track=_blend(foreground, background, 0.82),
            title=f"bold {foreground}",
            subtitle=_blend(foreground, background, 0.25),
            accent=str(
                getattr(theme, "accent", None) or getattr(theme, "primary", None) or "#0178d4"
            ),
        )


_palette = Palette.from_theme(None)


def set_palette(theme: object) -> None:
    global _palette
    _palette = Palette.from_theme(theme)


def palette() -> Palette:
    return _palette


#: status -> (glyph, palette attribute, short word for the status line)
STATUS_LOOK: dict[AccountStatus, tuple[str, str, str]] = {
    AccountStatus.OK: ("●", "ok", "ok"),
    AccountStatus.LOADING: ("◌", "muted", "checking"),
    AccountStatus.STALE: ("●", "warn", "stale"),
    AccountStatus.RATE_LIMITED: ("●", "warn", "rate limited"),
    AccountStatus.NEEDS_AUTH: ("▲", "danger", "sign-in needed"),
    AccountStatus.ERROR: ("▲", "danger", "error"),
    AccountStatus.DISABLED: ("○", "muted", "paused"),
    AccountStatus.UNKNOWN: ("○", "muted", "no data"),
}


def status_look(status: AccountStatus) -> tuple[str, str, str]:
    """Returns (glyph, colour, word)."""
    glyph, attribute, word = STATUS_LOOK.get(status, STATUS_LOOK[AccountStatus.UNKNOWN])
    return glyph, getattr(_palette, attribute), word


def usage_colour(percent: float | None, settings: Settings) -> str:
    """Green below the warning line, amber above it, red above the danger line."""
    if percent is None:
        return _palette.muted
    if percent >= settings.danger_at_percent:
        return _palette.danger
    if percent >= settings.warn_at_percent:
        return _palette.warn
    return _palette.ok


def bar(percent: float | None, width: int, *, colour: str) -> Text:
    """A block bar. Unknown usage renders as an empty track, not a zero."""
    width = max(4, width)
    if percent is None:
        return Text(EMPTY * width, style=_palette.track)

    ratio = max(0.0, min(1.0, percent / 100.0))
    filled = round(ratio * width)
    # Anything above zero should show at least one cell, otherwise 0.4% looks
    # identical to nothing at all. Likewise keep one empty cell until it is
    # genuinely full, so "nearly there" and "done" are distinguishable.
    if filled == 0 and percent > 0:
        filled = 1
    if filled == width and percent < 100:
        filled = width - 1

    out = Text()
    out.append(FILLED * filled, style=colour)
    out.append(EMPTY * (width - filled), style=_palette.track)
    return out


def percent_text(percent: float | None, *, colour: str) -> Text:
    if percent is None:
        return Text("  --", style=_palette.muted)
    return Text(f"{percent:3.0f}%", style=colour)


def truncate(value: str, limit: int) -> str:
    value = " ".join(str(value).split())  # collapse anything multi-line
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"
