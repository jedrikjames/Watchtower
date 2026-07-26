"""The bit that owns the accounts.

The TUI holds one AccountManager and asks it to do things. The manager owns the
account list, the credential store and the in-memory state each card renders
from. It does not import anything from watchtower_tui.tui, and it should stay
- there is a headless CLI that drives the same object.
"""

from __future__ import annotations

import asyncio
import json
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from ..auth import LoopbackReceiver, new_pkce, new_state
from ..errors import (
    AuthError,
    RateLimited,
    ReauthRequired,
    StorageError,
    UsageUnavailable,
    VaultLocked,
    WatchtowerError,
)
from ..logging_setup import get_logger
from ..models import Account, AccountState, AccountStatus, AuthMethod, Credential
from ..providers import Provider, get_provider
from ..providers.base import Identity, ImportCandidate
from ..secretstore import SecretStore, open_store
from ..secretstore.vault import FileVault
from ..settings import Settings
from ..store import AccountRepository, UsageCache
from ..timefmt import utcnow

log = get_logger("service.manager")

CREDENTIAL_PREFIX = "account:"

#: Backoff after a failed poll, in seconds, indexed by consecutive failures.
BACKOFF_LADDER = (30, 60, 120, 300, 600)
#: What we wait after a 429 when the provider does not tell us.
DEFAULT_RATE_LIMIT_BACKOFF = 300


@dataclass(slots=True)
class SignInEvent:
    """Progress report from a sign-in, so the UI can narrate it."""

    message: str
    url: str = ""
    done: bool = False


EventSink = Callable[[SignInEvent], None]


def _noop(_: SignInEvent) -> None:
    pass


class AccountManager:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: AccountRepository | None = None,
        cache: UsageCache | None = None,
        store: SecretStore | None = None,
    ):
        self.settings = settings
        self._repo = repository or AccountRepository()
        self._cache = cache or UsageCache()
        self._store = store or open_store(settings.secret_backend)
        self._states: dict[str, AccountState] = {}
        self._order: list[str] = []
        self._locks: dict[str, asyncio.Lock] = {}

    # -- store state -----------------------------------------------------

    @property
    def store(self) -> SecretStore:
        return self._store

    @property
    def store_locked(self) -> bool:
        return self._store.locked

    @property
    def store_needs_setup(self) -> bool:
        """True when we are on the file vault and it has not been created yet."""
        return isinstance(self._store, FileVault) and not self._store.exists()

    def create_vault(self, passphrase: str) -> None:
        if not isinstance(self._store, FileVault):
            raise StorageError("not using the file vault", friendly="No vault to create.")
        self._store.create(passphrase)

    def unlock_vault(self, passphrase: str) -> None:
        self._store.unlock(passphrase)

    # -- account list ----------------------------------------------------

    def load(self) -> list[AccountState]:
        cached = self._cache.load()
        self._states.clear()
        self._order.clear()
        for account in self._repo.load():
            if get_provider(account.provider) is None:
                log.warning(
                    "account %s uses unknown provider %r, skipping", account.id, account.provider
                )
                continue
            state = AccountState(account=account, report=cached.get(account.id))
            state.status = AccountStatus.UNKNOWN if state.report is None else AccountStatus.STALE
            if not account.enabled:
                state.status = AccountStatus.DISABLED
            self._states[account.id] = state
            self._order.append(account.id)
        return self.states

    @property
    def states(self) -> list[AccountState]:
        return [self._states[i] for i in self._order if i in self._states]

    def get(self, account_id: str) -> AccountState | None:
        return self._states.get(account_id)

    def provider_for(self, state: AccountState) -> Provider | None:
        return get_provider(state.account.provider)

    def _persist_accounts(self) -> None:
        self._repo.save([self._states[i].account for i in self._order if i in self._states])

    def _persist_cache(self) -> None:
        self._cache.save({s.id: s.report for s in self.states if s.report is not None})

    # -- credentials -----------------------------------------------------

    def _credential_key(self, account_id: str) -> str:
        return f"{CREDENTIAL_PREFIX}{account_id}"

    def load_credential(self, account_id: str) -> Credential | None:
        raw = self._store.get(self._credential_key(account_id))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            log.warning("stored credential for %s is not valid JSON", account_id)
            return None
        if not isinstance(payload, dict):
            return None
        return Credential.from_dict(payload)

    def save_credential(self, account_id: str, credential: Credential) -> None:
        credential.register_for_redaction()
        self._store.put(self._credential_key(account_id), json.dumps(credential.to_dict()))

    def _drop_credential(self, account_id: str) -> None:
        try:
            self._store.delete(self._credential_key(account_id))
        except StorageError as exc:
            log.warning("could not delete stored credential: %s", exc)

    # -- adding ----------------------------------------------------------

    async def sign_in(
        self, provider_id: str, *, on_event: EventSink = _noop
    ) -> tuple[Credential, Identity]:
        """Run the browser OAuth flow and return a credential. Nothing is saved."""
        provider = get_provider(provider_id)
        if provider is None:
            raise AuthError(
                f"unknown provider {provider_id!r}", friendly="That provider is not available."
            )
        if not provider.supports_oauth:
            raise AuthError(
                f"{provider_id} has no oauth",
                friendly=f"{provider.info.display_name} does not support sign-in.",
            )

        endpoints = provider.oauth_endpoints()
        pkce = new_pkce()
        state = new_state()

        async with LoopbackReceiver(endpoints.redirect_port, endpoints.redirect_path) as receiver:
            url = provider.oauth_client().authorization_url(
                pkce=pkce, state=state, redirect_uri=receiver.redirect_uri
            )
            opened = False
            if self.settings.open_browser:
                # webbrowser.open can block for a surprisingly long time.
                opened = await asyncio.to_thread(self._open_browser, url)

            on_event(
                SignInEvent(
                    message=(
                        "Waiting for you to finish in the browser..."
                        if opened
                        else "Open this URL in your browser to continue:"
                    ),
                    url="" if opened else url,
                )
            )

            result = await receiver.wait(state)
            on_event(SignInEvent(message="Exchanging the authorisation code..."))

            tokens = await provider.oauth_client().exchange_code(
                code=result.code, pkce=pkce, redirect_uri=receiver.redirect_uri, state=state
            )

        credential = Credential(
            method=AuthMethod.OAUTH,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
        )
        credential.register_for_redaction()
        credential = provider.finalise_credential(credential, tokens)

        on_event(SignInEvent(message="Reading account details..."))
        identity = await self._safe_identify(provider, credential)
        on_event(SignInEvent(message="Signed in.", done=True))
        return credential, identity

    @staticmethod
    def _open_browser(url: str) -> bool:
        try:
            return bool(webbrowser.open(url, new=2))
        except Exception as exc:  # pragma: no cover - platform dependent
            log.info("could not open a browser: %s", type(exc).__name__)
            return False

    @staticmethod
    async def _safe_identify(provider: Provider, credential: Credential) -> Identity:
        try:
            return await provider.identify(credential)
        except Exception as exc:
            log.info("identify failed for %s: %s", provider.info.id, type(exc).__name__)
            return Identity()

    def create_account(
        self,
        provider_id: str,
        label: str,
        credential: Credential,
        identity: Identity | None = None,
    ) -> AccountState:
        provider = get_provider(provider_id)
        if provider is None:
            raise WatchtowerError(
                f"unknown provider {provider_id!r}", friendly="That provider is not available."
            )

        identity = identity or Identity()
        account = Account.create(
            provider=provider_id,
            label=self._unique_label(label or identity.label or provider.info.display_name),
            identity=identity.email or identity.label,
            plan=identity.plan,
            auth_method=credential.method,
        )
        if identity.account_id and not credential.account_hint:
            credential.account_hint = identity.account_id

        self.save_credential(account.id, credential)
        state = AccountState(account=account, status=AccountStatus.UNKNOWN)
        self._states[account.id] = state
        self._order.append(account.id)
        self._persist_accounts()
        return state

    def adopt(self, candidate: ImportCandidate, label: str = "") -> AccountState:
        """Turn a credential we found on disk into a real account."""
        return self.create_account(
            candidate.provider_id,
            label or candidate.identity.label or candidate.label,
            candidate.credential,
            candidate.identity,
        )

    def import_candidates(self) -> list[ImportCandidate]:
        found: list[ImportCandidate] = []
        for provider in (get_provider(p) for p in self._provider_ids()):
            if provider is None:
                continue
            try:
                found.extend(provider.import_candidates())
            except Exception as exc:
                log.info("import scan failed for %s: %s", provider.info.id, type(exc).__name__)
        return found

    @staticmethod
    def _provider_ids() -> list[str]:
        from ..providers import provider_ids

        return provider_ids()

    def _unique_label(self, wanted: str) -> str:
        wanted = (wanted or "Account").strip()[:48] or "Account"
        existing = {s.account.label for s in self.states}
        if wanted not in existing:
            return wanted
        n = 2
        while f"{wanted} ({n})" in existing:
            n += 1
        return f"{wanted} ({n})"

    # -- editing ---------------------------------------------------------

    def rename(self, account_id: str, label: str) -> None:
        state = self._states.get(account_id)
        if state is None:
            return
        label = label.strip()[:48]
        if not label or label == state.account.label:
            return
        state.account.label = label
        state.account.touch()
        self._persist_accounts()

    def set_enabled(self, account_id: str, enabled: bool) -> None:
        state = self._states.get(account_id)
        if state is None:
            return
        state.account.enabled = enabled
        state.account.touch()
        state.status = AccountStatus.DISABLED if not enabled else AccountStatus.UNKNOWN
        state.retry_after = None
        state.failures = 0
        self._persist_accounts()

    def remove(self, account_id: str) -> None:
        state = self._states.pop(account_id, None)
        if state is None:
            return
        if account_id in self._order:
            self._order.remove(account_id)
        self._drop_credential(account_id)
        self._persist_accounts()
        self._persist_cache()

    def move(self, account_id: str, offset: int) -> None:
        """Reorder cards on the dashboard."""
        if account_id not in self._order:
            return
        index = self._order.index(account_id)
        target = max(0, min(len(self._order) - 1, index + offset))
        if target == index:
            return
        self._order.insert(target, self._order.pop(index))
        self._persist_accounts()

    async def reauth(self, account_id: str, *, on_event: EventSink = _noop) -> None:
        state = self._states.get(account_id)
        if state is None:
            return
        credential, identity = await self.sign_in(state.account.provider, on_event=on_event)
        if identity.account_id and not credential.account_hint:
            credential.account_hint = identity.account_id
        self.save_credential(account_id, credential)

        state.account.auth_method = credential.method
        if identity.email or identity.label:
            state.account.identity = identity.email or identity.label
        if identity.plan:
            state.account.plan = identity.plan
        state.account.touch()
        state.status = AccountStatus.UNKNOWN
        state.message = ""
        state.failures = 0
        state.retry_after = None
        self._persist_accounts()

    # -- refreshing ------------------------------------------------------

    async def refresh_account(self, account_id: str) -> AccountState | None:
        state = self._states.get(account_id)
        if state is None:
            return None

        lock = self._locks.setdefault(account_id, asyncio.Lock())
        if lock.locked():
            # Already being refreshed; a second manual press should be a no-op
            # rather than a second round trip.
            return state

        async with lock:
            return await self._refresh_locked(state)

    async def _refresh_locked(self, state: AccountState) -> AccountState:
        account = state.account
        if not account.enabled:
            state.status = AccountStatus.DISABLED
            return state

        provider = get_provider(account.provider)
        if provider is None:
            state.status = AccountStatus.ERROR
            state.message = "This provider is no longer available."
            return state

        state.last_attempt = utcnow()
        if state.status is AccountStatus.UNKNOWN:
            state.status = AccountStatus.LOADING

        try:
            credential = self.load_credential(account.id)
            if credential is None:
                raise ReauthRequired(
                    "no stored credential",
                    friendly="No saved sign-in for this account. Press R to sign in.",
                )

            if credential.expired():
                log.info("refreshing the access token for %s", account.id)
                credential = await provider.refresh_credential(credential)
                self.save_credential(account.id, credential)

            report = await self._fetch_usage_verifying_auth(provider, account.id, credential)

        except VaultLocked:
            state.status = AccountStatus.ERROR
            state.message = "The vault is locked."
            return state

        except ReauthRequired as exc:
            state.status = AccountStatus.NEEDS_AUTH
            state.message = exc.friendly
            state.retry_after = None
            return state

        except RateLimited as exc:
            wait = exc.retry_after or DEFAULT_RATE_LIMIT_BACKOFF
            state.status = AccountStatus.RATE_LIMITED
            state.message = exc.friendly
            state.retry_after = utcnow() + timedelta(seconds=wait)
            log.info("%s is rate limited, backing off %.0fs", account.provider, wait)
            return state

        except UsageUnavailable as exc:
            state.status = AccountStatus.UNKNOWN
            state.message = exc.friendly
            state.failures = 0
            state.retry_after = None
            return state

        except WatchtowerError as exc:
            state.failures += 1
            state.status = AccountStatus.STALE if state.report else AccountStatus.ERROR
            state.message = exc.friendly
            state.retry_after = utcnow() + timedelta(seconds=self._backoff(state.failures))
            log.info("refresh failed for %s (%s failures)", account.id, state.failures)
            return state

        except Exception as exc:
            state.failures += 1
            state.status = AccountStatus.STALE if state.report else AccountStatus.ERROR
            state.message = "Unexpected problem talking to the provider."
            state.retry_after = utcnow() + timedelta(seconds=self._backoff(state.failures))
            log.exception("unhandled error refreshing %s: %s", account.id, type(exc).__name__)
            return state

        state.report = report
        state.status = AccountStatus.OK
        state.message = ""
        state.failures = 0
        state.retry_after = None
        state.last_success = utcnow()

        changed = False
        if report.plan and report.plan != account.plan:
            account.plan = report.plan
            changed = True
        if changed:
            account.touch()
            self._persist_accounts()

        self._persist_cache()
        return state

    async def _fetch_usage_verifying_auth(self, provider, account_id: str, credential: Credential):
        """Fetch usage, but do not take a 401 at face value.

        These usage endpoints are undocumented and at least one of them answers
        401/403 for reasons that have nothing to do with the credential being
        dead. Telling somebody to sign in again when their sign-in is perfectly
        good sends them round a loop that cannot help, so the claim gets
        checked: refresh the token and try once more. If the refresh works and
        usage is still refused, the credential is fine and the endpoint is the
        problem - report that instead.
        """
        try:
            return await provider.fetch_usage(credential)
        except ReauthRequired:
            pass

        log.info("usage endpoint refused %s; checking if the credential is really dead", account_id)
        try:
            refreshed = await provider.refresh_credential(credential)
        except Exception:
            # Could not renew the token, so we cannot show the 401 was the
            # endpoint's fault. Assume the credential really is dead, which is
            # the conservative answer and what the user can actually act on.
            raise ReauthRequired(
                "token refresh failed after a usage 401",
                friendly="This account's sign-in has expired. Press R to sign in again.",
            ) from None

        self.save_credential(account_id, refreshed)
        try:
            return await provider.fetch_usage(refreshed)
        except ReauthRequired as exc:
            raise UsageUnavailable(
                f"usage endpoint refused a freshly refreshed token: {exc}",
                friendly=(
                    "Signed in fine, but the provider would not return usage figures. "
                    "This usually means they changed the endpoint."
                ),
            ) from exc

    @staticmethod
    def _backoff(failures: int) -> int:
        index = min(max(failures, 1) - 1, len(BACKOFF_LADDER) - 1)
        return BACKOFF_LADDER[index]

    async def refresh_all(self, *, force: bool = False) -> list[AccountState]:
        """Poll every account that is due. Runs them concurrently."""
        now = utcnow()
        due = [s for s in self.states if s.account.enabled and (force or s.may_poll(now=now))]
        if not due:
            return []
        results = await asyncio.gather(
            *(self.refresh_account(s.id) for s in due), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                log.error("refresh_all had an unexpected failure: %s", type(result).__name__)
        return due

    # -- shutdown ---------------------------------------------------------

    def close(self) -> None:
        try:
            self._persist_cache()
        except Exception:  # pragma: no cover
            log.exception("could not write the usage cache on shutdown")
        self._store.close()

    # -- display helpers ---------------------------------------------------

    def display_identity(self, state: AccountState) -> str:
        """The subtitle on a card, honouring the 'mask identities' setting."""
        raw = state.account.identity
        if not raw:
            return ""
        if not self.settings.mask_identities:
            return raw
        if "@" in raw:
            name, _, domain = raw.partition("@")
            head = name[:2] if len(name) > 2 else name[:1]
            return f"{head}{'*' * 4}@{domain}"
        return f"{raw[:2]}{'*' * 6}"
