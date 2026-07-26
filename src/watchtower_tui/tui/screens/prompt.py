"""Two small modals the app needs everywhere: ask a question, ask for a string."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no. Defaults to no, and escape means no."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
    ]

    def __init__(self, question: str, detail: str = "", confirm_label: str = "Remove") -> None:
        super().__init__()
        self.question = question
        self.detail = detail
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal modal-narrow"):
            yield Label(self.question, classes="modal-title")
            if self.detail:
                yield Static(self.detail, classes="modal-detail")
            with Horizontal(classes="modal-buttons"):
                yield Button(self.confirm_label, variant="error", id="confirm")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextPrompt(ModalScreen[str | None]):
    """One line of input. Returns None if cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        title: str,
        *,
        value: str = "",
        placeholder: str = "",
        detail: str = "",
        password: bool = False,
        submit_label: str = "Save",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.value = value
        self.placeholder = placeholder
        self.detail = detail
        self.password = password
        self.submit_label = submit_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal modal-narrow"):
            yield Label(self.title_text, classes="modal-title")
            if self.detail:
                yield Static(self.detail, classes="modal-detail")
            yield Input(
                value=self.value,
                placeholder=self.placeholder,
                password=self.password,
                id="value",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button(self.submit_label, variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        field = self.query_one("#value", Input)
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.dismiss(self.query_one("#value", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
