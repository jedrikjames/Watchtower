"""What you get when you press enter on a card."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from ...models import AccountState
from ...providers import get_provider
from ...timefmt import ago


class AccountActionsScreen(ModalScreen[str | None]):
    """Returns the chosen action id, or None."""

    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def __init__(self, state: AccountState, identity: str = "") -> None:
        super().__init__()
        self.state = state
        self.identity = identity
        self._actions: list[str] = []

    def compose(self) -> ComposeResult:
        account = self.state.account
        provider = get_provider(account.provider)
        provider_name = provider.info.display_name if provider else account.provider

        with Vertical(classes="modal"):
            yield Label(account.label, classes="modal-title")
            yield Static(self._summary(provider_name), classes="modal-detail")
            yield OptionList(*self._build_options(), id="actions")
            yield Static("enter select · esc close", classes="modal-hint")

    def _summary(self, provider_name: str) -> str:
        account = self.state.account
        bits = [provider_name]
        if account.plan:
            bits.append(account.plan)
        if self.identity:
            bits.append(self.identity)
        bits.append(account.auth_method.label)

        lines = [" · ".join(bits)]
        lines.append(f"Last updated {ago(self.state.last_success)}.")
        if self.state.message:
            lines.append(self.state.message)
        return "\n".join(lines)

    def _build_options(self) -> list[Option]:
        enabled = self.state.account.enabled
        entries = [
            ("refresh", "Refresh now"),
            ("rename", "Rename"),
            ("reauth", "Sign in again"),
            ("toggle", "Resume updates" if not enabled else "Pause updates"),
            ("remove", "Remove account"),
        ]
        self._actions = [key for key, _ in entries]
        return [Option(text) for _, text in entries]

    def on_mount(self) -> None:
        options = self.query_one("#actions", OptionList)
        options.focus()
        options.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if 0 <= event.option_index < len(self._actions):
            self.dismiss(self._actions[event.option_index])
        else:  # pragma: no cover
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
