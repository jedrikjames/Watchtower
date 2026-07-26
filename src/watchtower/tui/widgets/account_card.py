"""One card per account.

Everything is drawn in a single render() rather than assembled from child
widgets. With a grid of these it is noticeably cheaper, and it makes the
alignment between the logo, the bars and the percentages exact.

Layout, at a card width of about 46 columns:

    ╲ │ ╱   Personal                        ●
    ──╋──   Claude · Max 20x
    ╱ │ ╲   me@example.com

    5-hour   ████████░░░░░░░░░░░░   42%
    Weekly   ███░░░░░░░░░░░░░░░░░   18%

    resets in 2h 14m · updated 12s ago
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.widgets import Static

from ...models import AccountState, AccountStatus
from ...providers import get_provider
from ...settings import Settings
from ...timefmt import ago, until
from ..render import bar, palette, percent_text, status_look, truncate, usage_colour
from .logo import LOGO_WIDTH, build_logo

GUTTER = "   "
LABEL_WIDTH = 8
MAX_WINDOWS = 3
MIN_BAR = 6


class AccountCard(Container):
    """A focusable card. Pressing enter on it opens the actions menu.

    The mark is a separate child widget rather than part of the body render.
    The body redraws every second to keep the relative times honest, and
    re-emitting a Sixel or Kitty image at that rate is the case textual-image
    warns flickers. Drawn once, left alone.
    """

    can_focus = True

    BINDINGS = [Binding("enter", "activate", "Account actions", show=False)]

    class Activated(Message):
        """The user pressed enter (or clicked) on a card."""

        def __init__(self, account_id: str) -> None:
            super().__init__()
            self.account_id = account_id

    def __init__(self, state: AccountState, settings: Settings, identity: str = "", **kw) -> None:
        super().__init__(**kw)
        self.state = state
        self.settings = settings
        self.identity = identity
        self.add_class("account-card")

    def compose(self):
        provider = get_provider(self.state.account.provider)
        if provider is not None:
            colour = provider.info.accent
            yield build_logo(provider, self.settings.logo_style, colour)
        yield CardBody(self.state, self.settings, self.identity)

    @property
    def account_id(self) -> str:
        return self.state.id

    def update_state(self, state: AccountState, identity: str = "") -> None:
        self.state = state
        self.identity = identity
        self.set_class(state.status.is_problem, "-problem")
        self.set_class(not state.account.enabled, "-paused")
        try:
            body = self.query_one(CardBody)
        except Exception:
            return
        body.state = state
        body.identity = identity
        body.refresh()

    # -- interaction -----------------------------------------------------

    def on_click(self) -> None:
        self.focus()

    def action_activate(self) -> None:
        self.post_message(self.Activated(self.account_id))


class CardBody(Static):
    """Everything on the card except the mark."""

    def __init__(self, state: AccountState, settings: Settings, identity: str = "", **kw) -> None:
        super().__init__(**kw)
        self.state = state
        self.settings = settings
        self.identity = identity

    def render(self) -> Group:
        account = self.state.account
        provider = get_provider(account.provider)
        provider_name = provider.info.display_name if provider else account.provider

        width = max(28, self.content_size.width or 44)
        text_width = max(10, width - LOGO_WIDTH - len(GUTTER))

        glyph, dot_colour, _ = status_look(self.state.status)

        # -- header, three lines beside the logo
        subtitle = provider_name
        if account.plan:
            subtitle = f"{provider_name} · {account.plan}"

        header_rows = [
            self._title_row(account.label, glyph, dot_colour, text_width),
            Text(truncate(subtitle, text_width), style=palette().subtitle, no_wrap=True),
            Text(truncate(self.identity, text_width), style=palette().muted, no_wrap=True),
        ]

        # The first three rows are indented past the mark, which the logo
        # widget draws over the top of.
        lines: list[Text] = []
        for index in range(3):
            row = Text(" " * LOGO_WIDTH)
            row.append(GUTTER)
            row.append_text(header_rows[index])
            lines.append(row)

        lines.append(Text(""))
        lines.extend(self._body(width))
        lines.append(Text(""))
        lines.append(self._footer(width))
        return Group(*lines)

    def _title_row(self, label: str, glyph: str, dot_colour: str, width: int) -> Text:
        row = Text(no_wrap=True)
        title = truncate(label, max(4, width - 2))
        row.append(title, style=palette().title)
        row.append(" " * max(1, width - len(title) - 1))
        row.append(glyph, style=dot_colour)
        return row

    def _body(self, width: int) -> list[Text]:
        """The usage bars, or a message explaining why there are none."""
        state = self.state

        if not state.account.enabled:
            return self._note("Paused. Press space to resume.", width)
        if state.status is AccountStatus.NEEDS_AUTH:
            return self._note(state.message or "Sign-in needed. Press R.", width)
        if state.status is AccountStatus.ERROR and state.report is None:
            return self._note(state.message or "Could not load usage.", width)
        if state.report is None or not state.report.windows:
            if state.status is AccountStatus.LOADING:
                return self._note("Checking usage...", width)
            return self._note(state.message or "No usage data yet.", width)

        bar_width = max(MIN_BAR, width - LABEL_WIDTH - 7)
        rows: list[Text] = []
        for window in state.report.windows[:MAX_WINDOWS]:
            value = window.percent if window.known else None
            colour = usage_colour(value, self.settings)
            row = Text(no_wrap=True)
            row.append(
                truncate(window.label, LABEL_WIDTH).ljust(LABEL_WIDTH), style=palette().subtitle
            )
            row.append(" ")
            row.append_text(bar(value, bar_width, colour=colour))
            row.append(" ")
            row.append_text(percent_text(value, colour=colour))
            rows.append(row)

        # Keep every card the same height however many windows it has.
        while len(rows) < MAX_WINDOWS:
            rows.append(Text(""))
        return rows

    @staticmethod
    def _note(message: str, width: int) -> list[Text]:
        return [
            Text(""),
            Text(truncate(message, width), style=palette().muted, no_wrap=True),
            Text(""),
        ]

    def _footer(self, width: int) -> Text:
        state = self.state
        parts: list[str] = []

        if state.report is not None and state.account.enabled:
            head = state.report.headline
            if head is not None and head.resets_at is not None:
                remaining = until(head.resets_at)
                if remaining:
                    parts.append(f"resets in {remaining}")

        if state.status is AccountStatus.RATE_LIMITED:
            parts.append("rate limited")
        elif state.status is AccountStatus.STALE:
            parts.append("stale")

        if state.last_success is not None:
            parts.append(f"updated {ago(state.last_success)}")
        elif state.account.enabled and state.status is not AccountStatus.NEEDS_AUTH:
            parts.append("never updated")

        return Text(truncate(" · ".join(parts), width), style=palette().muted, no_wrap=True)
