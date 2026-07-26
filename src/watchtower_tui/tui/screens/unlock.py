"""Vault passphrase prompt.

Only ever shown on machines with no OS keychain. Two modes: create a new vault
(asks twice) or unlock an existing one.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ...secretstore.vault import MIN_PASSPHRASE

CREATE_BLURB = (
    "This machine has no system keychain, so Watchtower keeps your tokens in an "
    "encrypted file instead. Choose a passphrase to protect it.\n\n"
    "There is no way to recover it. If you forget it, delete the vault and "
    "add your accounts again."
)
UNLOCK_BLURB = "Enter the passphrase for your local vault."


class UnlockScreen(ModalScreen[str | None]):
    """Returns the passphrase, or None if the user gave up."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, *, creating: bool, error: str = "") -> None:
        super().__init__()
        self.creating = creating
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(
                "Set a vault passphrase" if self.creating else "Unlock", classes="modal-title"
            )
            yield Static(CREATE_BLURB if self.creating else UNLOCK_BLURB, classes="modal-detail")
            if self.error:
                yield Static(self.error, classes="modal-error")
            yield Input(password=True, placeholder="Passphrase", id="passphrase")
            if self.creating:
                yield Input(password=True, placeholder="Passphrase again", id="confirm")
            with Horizontal(classes="modal-buttons"):
                yield Button(
                    "Create" if self.creating else "Unlock", variant="primary", id="submit"
                )
                yield Button("Quit", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#passphrase", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.creating and event.input.id == "passphrase":
            self.query_one("#confirm", Input).focus()
            return
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        passphrase = self.query_one("#passphrase", Input).value
        if len(passphrase) < MIN_PASSPHRASE:
            self.notify(f"Use at least {MIN_PASSPHRASE} characters.", severity="error")
            return
        if self.creating and passphrase != self.query_one("#confirm", Input).value:
            self.notify("The two passphrases do not match.", severity="error")
            return
        self.dismiss(passphrase)
