"""Keyboard reference."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from ...paths import app_dir

KEYS: list[tuple[str, str]] = [
    ("↑ ↓ ← →", "Move between cards (h j k l also work)"),
    ("enter", "Open actions for the selected card"),
    ("a", "Add an account"),
    ("r", "Refresh everything now"),
    ("R", "Sign in again for the selected card"),
    ("e", "Rename the selected card"),
    ("d", "Remove the selected card"),
    ("space", "Pause or resume the selected card"),
    ("[ ]", "Move the selected card left or right"),
    ("s", "Settings"),
    ("?", "This screen"),
    ("q", "Quit"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Keyboard", classes="modal-title")
            with VerticalScroll(classes="help-body"):
                for key, description in KEYS:
                    yield Static(f"[b]{key:<9}[/b]  {description}", classes="help-row")
                yield Static("", classes="help-row")
                yield Static(
                    f"[b]Data[/b]      {app_dir()}\n"
                    "[b]Secrets[/b]   OS keychain, or an encrypted vault if there is none",
                    classes="help-row",
                )
            yield Static("esc close", classes="modal-hint")

    def action_close(self) -> None:
        self.dismiss(None)
