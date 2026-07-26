"""The bar across the top: what the app is called, and what the poller is doing.

The brief asked for refresh status to be obvious, so it is a permanent fixture
rather than a toast that disappears. It re-renders once a second so the "12s
ago" stays honest.
"""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.widgets import Static

from ...timefmt import ago, until, utcnow
from ..render import palette

SPINNER = "◐◓◑◒"


class StatusLine(Static):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.in_flight = False
        self.last_run: datetime | None = None
        self.next_run: datetime | None = None
        self.account_count = 0
        self.problem_count = 0
        self._frame = 0
        self._flash = ""
        self._flash_error = False

    def set_status(
        self,
        *,
        in_flight: bool,
        last_run: datetime | None,
        next_run: datetime | None,
        account_count: int,
        problem_count: int,
    ) -> None:
        self.in_flight = in_flight
        self.last_run = last_run
        self.next_run = next_run
        self.account_count = account_count
        self.problem_count = problem_count
        self.refresh()

    def tick(self) -> None:
        """Called once a second by the app. Only this line, never the cards."""
        self._frame = (self._frame + 1) % len(SPINNER)
        self.refresh()

    def flash(self, message: str, *, error: bool = False) -> None:
        """Show a transient message where the refresh status usually sits.

        Toasts are disabled, so this is the only place a failure can surface.
        It must never be silently dropped.
        """
        self._flash = message
        self._flash_error = error
        self.refresh()

    def clear_flash(self) -> None:
        self._flash = ""
        self.refresh()

    def render(self) -> Text:
        width = max(20, self.content_size.width or 60)

        colours = palette()
        left = Text(no_wrap=True)
        left.append("Watchtower", style=colours.title)
        if self.account_count:
            plural = "" if self.account_count == 1 else "s"
            left.append(f"  {self.account_count} account{plural}", style=colours.muted)
        if self.problem_count:
            left.append(f"  {self.problem_count} need attention", style=colours.danger)

        right = self._right()
        gap = max(1, width - left.cell_len - right.cell_len)

        line = Text(no_wrap=True)
        line.append_text(left)
        line.append(" " * gap)
        line.append_text(right)
        return line

    def _right(self) -> Text:
        colours = palette()
        out = Text(no_wrap=True)
        if self._flash:
            out.append("• ", style=colours.danger if self._flash_error else colours.ok)
            out.append(self._flash, style=colours.danger if self._flash_error else colours.subtitle)
            return out
        if self.in_flight:
            out.append(SPINNER[self._frame], style=colours.accent)
            out.append(" refreshing", style=colours.muted)
            return out

        if self.last_run is None:
            out.append("○", style=colours.muted)
            out.append(" waiting for first refresh", style=colours.muted)
            return out

        out.append("●", style=colours.ok)
        out.append(f" updated {ago(self.last_run)}", style=colours.muted)
        if self.next_run is not None:
            remaining = until(self.next_run, now=utcnow())
            if remaining:
                out.append(f" · next in {remaining}", style=colours.muted)
        return out
