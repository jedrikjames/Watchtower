"""Progress while a browser sign-in is in flight.

The URL is shown in full when we could not open a browser, because on a remote
box that string is the only way through. It is an authorisation *request* URL,
so there is nothing secret in it - unlike the callback, which never appears
anywhere in the UI.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, LoadingIndicator, Static


class SignInScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str) -> None:
        super().__init__()
        self.title_text = title
        self._cancelled = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    async def wait_cancelled(self) -> None:
        """Lets the caller race the sign-in against the user pressing escape."""
        await self._cancelled.wait()

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.title_text, classes="modal-title")
            yield LoadingIndicator(id="spinner")
            yield Static("Starting...", id="signin-message", classes="modal-detail")
            yield Static("", id="signin-url", classes="signin-url")
            yield Button("Cancel", id="cancel")

    def on_signin_event(self, event) -> None:
        """Sink for AccountManager.sign_in progress events."""
        self.set_progress(event.message, event.url)

    def set_progress(self, message: str, url: str = "") -> None:
        self.query_one("#signin-message", Static).update(message)
        url_widget = self.query_one("#signin-url", Static)
        url_widget.update(url)
        url_widget.display = bool(url)

    def finish(self, message: str) -> None:
        """Swap the spinner out; used briefly before the screen is popped."""
        self.query_one("#spinner", LoadingIndicator).display = False
        self.query_one("#signin-message", Static).update(message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self._cancelled.set()
        self.dismiss(False)
