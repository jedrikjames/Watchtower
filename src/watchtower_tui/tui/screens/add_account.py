"""Adding an account.

One flat list rather than a wizard. For a first-time user the whole decision is
visible at once: sign in with either provider, or adopt a credential we already
found on the machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from ...providers import all_providers
from ...providers.base import ImportCandidate


@dataclass(slots=True)
class AddChoice:
    kind: str  # "oauth" | "import"
    provider_id: str = ""
    candidate: ImportCandidate | None = None


class AddAccountScreen(ModalScreen[AddChoice | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, candidates: list[ImportCandidate] | None = None) -> None:
        super().__init__()
        self.candidates = candidates or []
        self._choices: list[AddChoice] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Add an account", classes="modal-title")
            yield Static(
                "A browser window opens for sign-in. Tokens are stored on this "
                "machine only - Watchtower has no server.",
                classes="modal-detail",
            )
            yield OptionList(*self._build_options(), id="choices")
            yield Static("enter select · esc cancel", classes="modal-hint")

    def _build_options(self) -> list[Option]:
        options: list[Option] = []
        self._choices = []

        for provider in all_providers():
            if not provider.supports_oauth:
                continue
            self._choices.append(AddChoice(kind="oauth", provider_id=provider.info.id))
            options.append(Option(f"{provider.info.signin_label}"))

        for candidate in self.candidates:
            self._choices.append(
                AddChoice(kind="import", provider_id=candidate.provider_id, candidate=candidate)
            )
            options.append(Option(f"Import {candidate.label}  ·  {_short(candidate.source)}"))

        return options

    def on_mount(self) -> None:
        options = self.query_one("#choices", OptionList)
        options.focus()
        if self._choices:
            options.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if 0 <= event.option_index < len(self._choices):
            self.dismiss(self._choices[event.option_index])
        else:  # pragma: no cover - only if the list and the model disagree
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _short(path: str, limit: int = 34) -> str:
    """Shorten a path so the option stays on one line.

    Collapsing the home directory to ~ is usually enough and reads much better
    than a truncated absolute path. Fall back to keeping the tail, which is
    where the interesting part of a path lives.
    """
    try:
        home = str(Path.home())
    except (OSError, RuntimeError):  # pragma: no cover - no home directory
        home = ""

    if home and path.startswith(home):
        path = "~" + path[len(home) :]
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1) :]
