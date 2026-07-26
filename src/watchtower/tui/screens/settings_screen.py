"""Settings.

Returns a new Settings object, or None if cancelled. Validation happens here
so a bad number never reaches the config file.
"""

from __future__ import annotations

from dataclasses import replace

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from ...secretstore import describe_backends
from ...settings import LOGO_STYLES, MAX_REFRESH_SECONDS, MIN_REFRESH_SECONDS, Settings

#: Wording for the logo_style choices, so the dropdown is not raw enum values.
_LOGO_LABELS = {
    "image": "Icons (needs Sixel/Kitty)",
    "dots": "Dots",
}


class SettingsScreen(ModalScreen[Settings | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, settings: Settings, themes: list[str] | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.themes = themes or ["textual-dark", "textual-light"]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal modal-wide"):
            yield Label("Settings", classes="modal-title")
            with VerticalScroll(classes="settings-body"):
                yield from self._row(
                    "Refresh every",
                    Input(
                        value=str(self.settings.refresh_seconds),
                        id="refresh_seconds",
                        type="integer",
                        classes="small-input",
                    ),
                    f"seconds ({MIN_REFRESH_SECONDS}-{MAX_REFRESH_SECONDS})",
                )
                yield from self._row(
                    "Warn above",
                    Input(
                        value=str(self.settings.warn_at_percent),
                        id="warn_at_percent",
                        type="integer",
                        classes="small-input",
                    ),
                    "percent used",
                )
                yield from self._row(
                    "Danger above",
                    Input(
                        value=str(self.settings.danger_at_percent),
                        id="danger_at_percent",
                        type="integer",
                        classes="small-input",
                    ),
                    "percent used",
                )
                yield from self._row(
                    "Hide email addresses",
                    Switch(value=self.settings.mask_identities, id="mask_identities"),
                    "useful when sharing a screen",
                )
                yield from self._row(
                    "Confirm before removing",
                    Switch(value=self.settings.confirm_remove, id="confirm_remove"),
                    "",
                )
                yield from self._row(
                    "Open a browser on sign-in",
                    Switch(value=self.settings.open_browser, id="open_browser"),
                    "turn off for headless machines",
                )
                yield from self._row(
                    "Theme",
                    Select(
                        [(name, name) for name in self.themes],
                        value=self.settings.theme
                        if self.settings.theme in self.themes
                        else Select.BLANK,
                        id="theme",
                        allow_blank=True,
                    ),
                    "",
                )
                yield from self._row(
                    "Provider logos",
                    Select(
                        [(_LOGO_LABELS[s], s) for s in LOGO_STYLES],
                        value=self.settings.logo_style
                        if self.settings.logo_style in LOGO_STYLES
                        else Select.BLANK,
                        id="logo_style",
                        allow_blank=True,
                    ),
                    "",
                )
                yield Static(
                    "  Real icons need a terminal with Sixel or Kitty graphics; "
                    "anything else falls back to dots on its own.",
                    classes="settings-note",
                )
                yield Static("Where secrets are kept", classes="settings-heading")
                for backend in describe_backends():
                    mark = "•" if backend.key == self._effective_backend() else " "
                    state = (
                        backend.detail if backend.available else f"unavailable - {backend.detail}"
                    )
                    yield Static(f"{mark} [b]{backend.name}[/b] — {state}", classes="settings-note")
                yield Static(
                    "Change the backend by editing config.json; Watchtower will migrate "
                    "nothing automatically, so re-add accounts after switching.",
                    classes="settings-note",
                )

            with Horizontal(classes="modal-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def _effective_backend(self) -> str:
        if self.settings.secret_backend != "auto":
            return self.settings.secret_backend
        for backend in describe_backends():
            if backend.key == "keyring" and backend.available:
                return "keyring"
        return "file"

    @staticmethod
    def _row(label: str, control, hint: str):
        with Horizontal(classes="settings-row"):
            yield Label(label, classes="settings-label")
            yield control
            if hint:
                yield Static(hint, classes="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#refresh_seconds", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        try:
            refresh = int(self.query_one("#refresh_seconds", Input).value or 0)
            warn = int(self.query_one("#warn_at_percent", Input).value or 0)
            danger = int(self.query_one("#danger_at_percent", Input).value or 0)
        except ValueError:
            self.notify("Those need to be whole numbers.", severity="error")
            return

        if not MIN_REFRESH_SECONDS <= refresh <= MAX_REFRESH_SECONDS:
            self.notify(
                f"Refresh must be between {MIN_REFRESH_SECONDS} and {MAX_REFRESH_SECONDS} seconds.",
                severity="error",
            )
            return
        if not 1 <= warn <= 100 or not 1 <= danger <= 100:
            self.notify("Thresholds must be between 1 and 100.", severity="error")
            return
        if danger < warn:
            self.notify("The danger threshold cannot be below the warning one.", severity="error")
            return

        theme = self.query_one("#theme", Select).value
        logo_style = self.query_one("#logo_style", Select).value
        updated = replace(
            self.settings,
            refresh_seconds=refresh,
            warn_at_percent=warn,
            danger_at_percent=danger,
            mask_identities=self.query_one("#mask_identities", Switch).value,
            confirm_remove=self.query_one("#confirm_remove", Switch).value,
            open_browser=self.query_one("#open_browser", Switch).value,
            theme=str(theme) if theme is not Select.BLANK else self.settings.theme,
            logo_style=str(logo_style)
            if logo_style is not Select.BLANK
            else self.settings.logo_style,
        )
        updated.normalise()
        self.dismiss(updated)
