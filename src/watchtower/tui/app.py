"""The application.

Responsibilities, roughly in order of how often they matter:

* keep the grid of cards in step with AccountManager's state,
* turn keypresses into manager calls,
* drive the modal flows (add, rename, remove, reauth, settings),
* and make sure a failure in any of the above shows up as a sentence rather
  than a traceback over the top of the dashboard.

No provider or storage logic lives here.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, ItemGrid, Vertical, VerticalScroll
from textual.widgets import Static

from ..errors import AuthCancelled, StorageError, VaultPassphraseError, WatchtowerError
from ..logging_setup import get_logger
from ..models import AccountState
from ..providers import get_provider
from ..service import AccountManager, RefreshScheduler
from ..settings import Settings
from ..settings import save as save_settings
from .render import set_palette
from .screens import (
    AccountActionsScreen,
    AddAccountScreen,
    ConfirmScreen,
    HelpScreen,
    SettingsScreen,
    SignInScreen,
    TextPrompt,
    UnlockScreen,
)
from .widgets import AccountCard, KeyHints, StatusLine
from .widgets.account_card import CardBody
from .widgets.logo import probe_image_support

log = get_logger("tui")

MIN_CARD_WIDTH = 46


class WatchtowerApp(App[None]):
    # Absolute rather than the usual bare "app.tcss". Textual resolves a
    # relative CSS_PATH against the module's directory, which does not survive
    # being frozen into a single-file binary; deriving it from __file__ works
    # both from source and from inside PyInstaller's extraction directory.
    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "Watchtower"
    SUB_TITLE = "AI account usage"

    #: No ctrl+p command palette. It is Textual's, not ours, and it advertises
    #: itself in the footer we have replaced.
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("a", "add_account", "Add"),
        Binding("r", "refresh", "Refresh"),
        Binding("shift+r", "reauth", "Re-auth", show=False),
        Binding("e", "rename", "Rename"),
        Binding("d", "remove", "Remove"),
        Binding("space", "toggle_paused", "Pause", show=False),
        Binding("s", "settings", "Settings"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit"),
        # Textual binds ctrl+q to quit by default. Rebinding it to nothing is
        # the only way to take a base-class binding out of circulation.
        Binding("ctrl+q", "noop", "", show=False, priority=True),
        # navigation
        Binding("right,l,tab", "focus_next_card", "", show=False),
        Binding("left,h,shift+tab", "focus_previous_card", "", show=False),
        Binding("down,j", "focus_next_card", "", show=False),
        Binding("up,k", "focus_previous_card", "", show=False),
        Binding("bracketright", "move_card(1)", "", show=False),
        Binding("bracketleft", "move_card(-1)", "", show=False),
    ]

    def __init__(self, settings: Settings, manager: AccountManager) -> None:
        super().__init__()
        self.settings = settings
        self.manager = manager
        self.scheduler = RefreshScheduler(
            manager,
            on_start=self._on_refresh_start,
            on_complete=self._on_refresh_complete,
        )
        self._booted = False

        # Has to happen here rather than in compose(): the probe waits for the
        # terminal to answer on stdin, and once Textual is running its input
        # thread takes that answer first. __init__ is the last safe moment.
        if settings.logo_style == "image":
            probe_image_support()

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="topbar"):
            yield StatusLine()
            yield KeyHints()
        with VerticalScroll(id="board"):
            yield ItemGrid(id="cards", min_column_width=MIN_CARD_WIDTH)
            with Vertical(id="empty"):
                yield Static("No accounts yet", classes="empty-title")
                yield Static(
                    "Watchtower keeps an eye on the usage limits for your Codex\n"
                    "and Claude accounts. Add one to get started - it opens a\n"
                    "browser and keeps the tokens on this machine.",
                    classes="empty-body",
                )
                yield Static("press [b]a[/b] to add an account", classes="empty-hint")

    def on_mount(self) -> None:
        self._apply_theme()
        self.start_up()

    def _apply_theme(self) -> None:
        if self.settings.theme in self.available_themes:
            self.theme = self.settings.theme
        set_palette(self.current_theme)

    def watch_theme(self, _theme: str) -> None:
        """Re-derive the Rich palette whenever the Textual theme changes."""
        try:
            set_palette(self.current_theme)
        except Exception:  # fires once before the app is fully constructed
            return
        for body in self.query(CardBody):
            body.refresh()

    def action_noop(self) -> None:
        """Target for bindings that exist purely to disable a default."""

    def notify(  # type: ignore[override]
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        """Route messages to the status line instead of a toast.

        Toasts are disabled, but errors still have to reach the user, so this
        replaces the mechanism rather than removing it. Full detail goes to the
        log; the line gets a sentence.
        """
        error = severity in ("error", "warning")
        log.info("notice (%s): %s", severity, message)
        try:
            status = self.query_one(StatusLine)
        except Exception:  # pragma: no cover - before mount or during teardown
            return
        status.flash(message, error=error)
        self.set_timer(8.0 if error else 4.0, status.clear_flash)

    def _modal_open(self) -> bool:
        """True when something is already on top of the dashboard."""
        return len(self.screen_stack) > 1

    # -- startup ----------------------------------------------------------

    @work
    async def start_up(self) -> None:
        """Unlock the store if we have to, then load and start polling."""
        if not await self._ensure_store_ready():
            self.exit()
            return

        try:
            self.manager.load()
        except StorageError as exc:
            self.notify(exc.friendly, severity="error", timeout=12)

        await self._sync_cards()
        self._booted = True
        self.scheduler.start()
        self.set_interval(1.0, self._tick)
        self._update_status()

    async def _ensure_store_ready(self) -> bool:
        manager = self.manager

        if manager.store_needs_setup:
            passphrase = await self.push_screen_wait(UnlockScreen(creating=True))
            if passphrase is None:
                return False
            try:
                manager.create_vault(passphrase)
            except WatchtowerError as exc:
                self.notify(exc.friendly, severity="error", timeout=12)
                return False
            return True

        error = ""
        while manager.store_locked:
            passphrase = await self.push_screen_wait(UnlockScreen(creating=False, error=error))
            if passphrase is None:
                return False
            try:
                manager.unlock_vault(passphrase)
            except VaultPassphraseError:
                error = "That passphrase did not work. Try again."
            except WatchtowerError as exc:
                self.notify(exc.friendly, severity="error", timeout=12)
                return False
        return True

    # -- card plumbing ------------------------------------------------------

    def _identity_for(self, state: AccountState) -> str:
        return self.manager.display_identity(state)

    async def _sync_cards(self) -> None:
        """Update in place when possible; rebuild only when the set changes."""
        grid = self.query_one("#cards", ItemGrid)
        states = self.manager.states
        wanted = [s.id for s in states]
        current = [card.account_id for card in grid.query(AccountCard)]

        if wanted != current:
            focused_id = self._focused_card_id()
            await grid.remove_children()
            if states:
                await grid.mount_all(
                    [AccountCard(s, self.settings, self._identity_for(s)) for s in states]
                )
            self._restore_focus(focused_id)
        else:
            by_id = {s.id: s for s in states}
            for card in grid.query(AccountCard):
                state = by_id.get(card.account_id)
                if state is not None:
                    card.update_state(state, self._identity_for(state))

        self.query_one("#empty").display = not states
        grid.display = bool(states)
        self._update_status()

    def _restore_focus(self, account_id: str | None) -> None:
        cards = list(self.query(AccountCard))
        if not cards:
            return
        for card in cards:
            if card.account_id == account_id:
                card.focus()
                return
        cards[0].focus()

    def _focused_card_id(self) -> str | None:
        focused = self.focused
        return focused.account_id if isinstance(focused, AccountCard) else None

    def _selected(self) -> AccountState | None:
        """The focused card's state, or the only one if there is just one."""
        account_id = self._focused_card_id()
        if account_id is None:
            cards = list(self.query(AccountCard))
            if len(cards) == 1:
                account_id = cards[0].account_id
        if account_id is None:
            self.notify("Select a card first.", severity="warning")
            return None
        return self.manager.get(account_id)

    # -- refresh wiring -----------------------------------------------------

    def _on_refresh_start(self) -> None:
        self._update_status()

    async def _on_refresh_complete(self, _updated: list[AccountState]) -> None:
        await self._sync_cards()

    def _tick(self) -> None:
        """Once a second, and only the status line.

        The cards are deliberately left alone. Repainting a card redraws its
        mark, and re-emitting a Sixel image at 1Hz flickers. Card bodies are
        refreshed when the usage actually changes instead, which is the only
        time they have anything new to say.

        Scheduler values are read fresh every tick rather than cached from the
        last refresh, so the countdown cannot drift out of step with the loop
        that owns it.
        """
        try:
            self._update_status()
            self.query_one(StatusLine).tick()
        except Exception:  # pragma: no cover - during teardown
            return

    def _update_status(self) -> None:
        try:
            status = self.query_one(StatusLine)
        except Exception:  # pragma: no cover
            return
        states = self.manager.states
        status.set_status(
            in_flight=self.scheduler.in_flight,
            last_run=self.scheduler.last_run,
            next_run=self.scheduler.next_run,
            account_count=len(states),
            problem_count=sum(1 for s in states if s.status.is_problem),
        )

    # -- navigation ---------------------------------------------------------

    def action_focus_next_card(self) -> None:
        self._cycle_focus(1)

    def action_focus_previous_card(self) -> None:
        self._cycle_focus(-1)

    def _cycle_focus(self, offset: int) -> None:
        cards = list(self.query(AccountCard))
        if not cards:
            return
        current = self._focused_card_id()
        index = next((i for i, c in enumerate(cards) if c.account_id == current), -1)
        cards[(index + offset) % len(cards)].focus()

    def action_move_card(self, offset: int) -> None:
        state = self._selected()
        if state is None:
            return
        self.manager.move(state.id, offset)
        self.call_later(self._sync_cards)

    # -- account actions ----------------------------------------------------

    def on_account_card_activated(self, message: AccountCard.Activated) -> None:
        self.open_actions(message.account_id)

    @work
    async def open_actions(self, account_id: str) -> None:
        state = self.manager.get(account_id)
        if state is None or self._modal_open():
            return
        action = await self.push_screen_wait(AccountActionsScreen(state, self._identity_for(state)))
        if action == "refresh":
            await self._refresh_one(account_id)
        elif action == "rename":
            await self._rename(account_id)
        elif action == "reauth":
            await self._reauth(account_id)
        elif action == "toggle":
            self._toggle(account_id)
        elif action == "remove":
            await self._remove(account_id)

    def action_refresh(self) -> None:
        if not self._booted:
            return
        self.scheduler.request_now()
        self._update_status()
        self.notify("Refreshing...", timeout=2)

    @work
    async def action_add_account(self) -> None:
        if not self._booted or self._modal_open():
            return
        candidates = self.manager.import_candidates()
        # Do not offer to import something already added.
        existing = {(s.account.provider, s.account.auth_method.value) for s in self.manager.states}
        candidates = [c for c in candidates if (c.provider_id, "imported") not in existing]

        choice = await self.push_screen_wait(AddAccountScreen(candidates))
        if choice is None:
            return

        if choice.kind == "import" and choice.candidate is not None:
            try:
                state = self.manager.adopt(choice.candidate)
            except WatchtowerError as exc:
                self.notify(exc.friendly, severity="error", timeout=10)
                return
            await self._sync_cards()
            self.notify(f"Added {state.account.label}.")
            await self._refresh_one(state.id)
            return

        await self._oauth_add(choice.provider_id)

    async def _oauth_add(self, provider_id: str) -> None:
        provider = get_provider(provider_id)
        if provider is None:
            self.notify("That provider is not available.", severity="error")
            return

        result = await self._run_signin(
            provider.info.signin_label,
            lambda screen: self.manager.sign_in(provider_id, on_event=screen.on_signin_event),
        )
        if result is None:
            return

        credential, identity = result
        try:
            state = self.manager.create_account(
                provider_id, identity.label or provider.info.display_name, credential, identity
            )
        except WatchtowerError as exc:
            self.notify(exc.friendly, severity="error", timeout=10)
            return

        await self._sync_cards()
        self.notify(f"Added {state.account.label}.")
        await self._refresh_one(state.id)

    async def _reauth(self, account_id: str) -> None:
        state = self.manager.get(account_id)
        if state is None:
            return
        provider = get_provider(state.account.provider)
        title = provider.info.signin_label if provider else "Sign in"

        result = await self._run_signin(
            title, lambda screen: self.manager.reauth(account_id, on_event=screen.on_signin_event)
        )
        if result is None:
            return
        await self._sync_cards()
        self.notify(f"{state.account.label} signed in again.")
        await self._refresh_one(account_id)

    async def _run_signin(self, title: str, start):
        """Push the progress modal and race the flow against the cancel button.

        Returns whatever the flow returned, or None if it was cancelled or
        failed (in which case the user has already been told why).
        """
        screen = SignInScreen(title)
        await self.push_screen(screen, wait_for_dismiss=False)

        flow = asyncio.create_task(start(screen))
        cancel = asyncio.create_task(screen.wait_cancelled())
        try:
            done, _ = await asyncio.wait({flow, cancel}, return_when=asyncio.FIRST_COMPLETED)

            if cancel in done:
                flow.cancel()
                return None

            cancel.cancel()
            return flow.result()

        except AuthCancelled:
            self.notify("Sign-in cancelled.", severity="warning")
            return None
        except WatchtowerError as exc:
            self.notify(exc.friendly, severity="error", timeout=12)
            return None
        except asyncio.CancelledError:
            return None
        except Exception:
            log.exception("sign-in failed unexpectedly")
            self.notify("Sign-in failed. See the log for details.", severity="error", timeout=10)
            return None
        finally:
            for task in (flow, cancel):
                task.cancel()
            if self.screen is screen:
                self.pop_screen()

    async def _refresh_one(self, account_id: str) -> None:
        state = self.manager.get(account_id)
        if state is not None:
            state.retry_after = None
        await self.manager.refresh_account(account_id)
        await self._sync_cards()

    @work
    async def action_rename(self) -> None:
        if self._modal_open():
            return
        state = self._selected()
        if state is not None:
            await self._rename(state.id)

    async def _rename(self, account_id: str) -> None:
        state = self.manager.get(account_id)
        if state is None:
            return
        name = await self.push_screen_wait(
            TextPrompt(
                "Rename account",
                value=state.account.label,
                placeholder="Account name",
                detail="Only changes the label shown here.",
            )
        )
        if name is None:
            return
        self.manager.rename(account_id, name)
        await self._sync_cards()

    @work
    async def action_remove(self) -> None:
        if self._modal_open():
            return
        state = self._selected()
        if state is not None:
            await self._remove(state.id)

    async def _remove(self, account_id: str) -> None:
        state = self.manager.get(account_id)
        if state is None:
            return

        if self.settings.confirm_remove:
            confirmed = await self.push_screen_wait(
                ConfirmScreen(
                    f"Remove {state.account.label}?",
                    detail=(
                        "The stored sign-in is deleted from this machine. Your account "
                        "with the provider is untouched."
                    ),
                )
            )
            if not confirmed:
                return

        label = state.account.label
        self.manager.remove(account_id)
        await self._sync_cards()
        self.notify(f"Removed {label}.")

    def action_toggle_paused(self) -> None:
        state = self._selected()
        if state is not None:
            self._toggle(state.id)

    def _toggle(self, account_id: str) -> None:
        state = self.manager.get(account_id)
        if state is None:
            return
        now_enabled = not state.account.enabled
        self.manager.set_enabled(account_id, now_enabled)
        self.call_later(self._sync_cards)
        if now_enabled:
            self.scheduler.request_now()

    @work
    async def action_reauth(self) -> None:
        if self._modal_open():
            return
        state = self._selected()
        if state is not None:
            await self._reauth(state.id)

    # -- settings and help ---------------------------------------------------

    @work
    async def action_settings(self) -> None:
        if self._modal_open():
            return
        updated = await self.push_screen_wait(
            SettingsScreen(self.settings, sorted(self.available_themes))
        )
        if updated is None:
            return

        for field in fields(updated):
            setattr(self.settings, field.name, getattr(updated, field.name))

        try:
            save_settings(self.settings)
        except WatchtowerError as exc:
            self.notify(exc.friendly, severity="error", timeout=10)

        self._apply_theme()
        await self._sync_cards()
        for card in self.query(AccountCard):
            card.refresh()
        self.notify("Settings saved.")

    def action_help(self) -> None:
        if not self._modal_open():
            self.push_screen(HelpScreen())

    # -- shutdown -------------------------------------------------------------

    async def on_unmount(self) -> None:
        await self.scheduler.stop()
        self.manager.close()
