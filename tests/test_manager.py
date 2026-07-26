"""AccountManager: the CRUD, the failure handling, and one full sign-in."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import watchtower_tui.providers as registry
from tests.helpers import FakeTokenServer
from watchtower_tui.errors import NetworkError, RateLimited, ReauthRequired, UsageUnavailable
from watchtower_tui.models import AccountStatus, AuthMethod, Credential, UsageReport, UsageWindow
from watchtower_tui.providers.base import Identity, Provider, ProviderInfo
from watchtower_tui.secretstore.vault import FileVault
from watchtower_tui.service import AccountManager


class StubProvider(Provider):
    info = ProviderInfo(
        id="stub",
        display_name="Stub",
        accent="#888888",
        logo=("a", "b", "c"),
        signin_label="Sign in",
    )

    def __init__(self):
        self.mode = "ok"
        self.calls = 0

    async def fetch_usage(self, credential):
        self.calls += 1
        if self.mode == "rate-limited":
            raise RateLimited("slow down", retry_after=42)
        if self.mode == "reauth":
            raise ReauthRequired("token is dead")
        if self.mode == "network":
            raise NetworkError("no route")
        if self.mode == "no-usage":
            raise UsageUnavailable("not for this plan")
        if self.mode == "boom":
            raise RuntimeError("something nobody predicted")
        return UsageReport(windows=[UsageWindow("5h", "5-hour", 33.0)], plan="Pro")

    async def identify(self, credential):
        return Identity(label="Stubby", email="stub@example.test", plan="Pro")


@pytest.fixture
def stub():
    provider = StubProvider()
    original = registry._PROVIDERS.get("stub")
    registry._PROVIDERS["stub"] = provider
    yield provider
    if original is None:
        registry._PROVIDERS.pop("stub", None)
    else:  # pragma: no cover
        registry._PROVIDERS["stub"] = original


@pytest.fixture
def manager(settings, stub):
    vault = FileVault()
    vault.create("test passphrase")
    instance = AccountManager(settings, store=vault)
    instance.load()
    return instance


def add(manager, label="Account"):
    return manager.create_account(
        "stub",
        label,
        Credential(method=AuthMethod.OAUTH, access_token="at-value", refresh_token="rt-value"),
        Identity(email="stub@example.test", plan="Pro"),
    )


# ------------------------------------------------------------------- CRUD


def test_create_stores_credential_out_of_the_account_file(manager):
    state = add(manager)
    raw = manager._repo.path.read_text()
    assert "at-value" not in raw
    assert "rt-value" not in raw
    assert manager.load_credential(state.id).access_token == "at-value"


def test_duplicate_labels_are_disambiguated(manager):
    add(manager, "Work")
    assert add(manager, "Work").account.label == "Work (2)"
    assert add(manager, "Work").account.label == "Work (3)"


def test_rename_and_reorder(manager):
    first, second = add(manager, "A"), add(manager, "B")
    manager.rename(first.id, "Renamed")
    assert manager.get(first.id).account.label == "Renamed"

    manager.move(second.id, -1)
    assert [s.account.label for s in manager.states] == ["B", "Renamed"]

    manager.move(second.id, -5)  # clamps rather than wrapping
    assert [s.account.label for s in manager.states] == ["B", "Renamed"]


def test_remove_deletes_the_credential(manager):
    state = add(manager)
    manager.remove(state.id)
    assert manager.get(state.id) is None
    assert manager.load_credential(state.id) is None


def test_state_survives_a_restart(manager, settings, stub):
    state = add(manager, "Persistent")
    asyncio.run(manager.refresh_account(state.id))
    manager.close()

    vault = FileVault()
    vault.unlock("test passphrase")
    reopened = AccountManager(settings, store=vault)
    states = reopened.load()

    assert [s.account.label for s in states] == ["Persistent"]
    assert states[0].report.headline.value == 33.0
    assert states[0].status is AccountStatus.STALE, "cached figures start out marked stale"


def test_accounts_with_an_unknown_provider_are_skipped(manager, settings):
    state = add(manager)
    manager._states[state.id].account.provider = "provider-that-was-uninstalled"
    manager._persist_accounts()

    reopened = AccountManager(settings, store=manager.store)
    assert reopened.load() == []


# --------------------------------------------------------------- refreshing


async def test_successful_refresh(manager, stub):
    state = add(manager)
    await manager.refresh_account(state.id)
    assert state.status is AccountStatus.OK
    assert state.report.headline.value == 33.0
    assert state.account.plan == "Pro"
    assert state.last_success is not None


@pytest.mark.parametrize(
    "mode,expected,keeps_report",
    [
        ("rate-limited", AccountStatus.RATE_LIMITED, True),
        ("reauth", AccountStatus.NEEDS_AUTH, True),
        ("network", AccountStatus.STALE, True),
        ("no-usage", AccountStatus.UNKNOWN, True),
        ("boom", AccountStatus.STALE, True),
    ],
)
async def test_failures_do_not_lose_the_last_good_reading(
    manager, stub, mode, expected, keeps_report
):
    state = add(manager)
    await manager.refresh_account(state.id)
    good = state.report

    stub.mode = mode
    state.retry_after = None
    await manager.refresh_account(state.id)

    assert state.status is expected
    assert (state.report is good) is keeps_report
    assert state.message, "the user should always be told something"


async def test_rate_limit_honours_retry_after(manager, stub):
    state = add(manager)
    stub.mode = "rate-limited"
    await manager.refresh_account(state.id)
    assert state.retry_after is not None
    assert not state.may_poll()

    before = stub.calls
    await manager.refresh_all()
    assert stub.calls == before, "a backed-off account must not be polled"

    await manager.refresh_all(force=True)
    assert stub.calls == before + 1, "an explicit refresh overrides the backoff"


async def test_failures_back_off_further_each_time(manager, stub):
    state = add(manager)
    stub.mode = "network"
    delays = []
    for _ in range(4):
        state.retry_after = None
        await manager.refresh_account(state.id)
        delays.append(manager._backoff(state.failures))
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


async def test_recovery_clears_the_failure_count(manager, stub):
    state = add(manager)
    stub.mode = "network"
    await manager.refresh_account(state.id)
    assert state.failures == 1

    stub.mode = "ok"
    state.retry_after = None
    await manager.refresh_account(state.id)
    assert state.failures == 0
    assert state.retry_after is None
    assert state.status is AccountStatus.OK


async def test_expired_tokens_are_refreshed_before_use(manager, stub, monkeypatch):
    state = add(manager)
    manager.save_credential(
        state.id, Credential(method=AuthMethod.OAUTH, access_token="", refresh_token="rt-value")
    )

    refreshed = []

    async def fake_refresh(credential):
        refreshed.append(credential)
        return Credential(
            method=AuthMethod.OAUTH, access_token="at-fresh", refresh_token="rt-value"
        )

    monkeypatch.setattr(stub, "refresh_credential", fake_refresh)
    await manager.refresh_account(state.id)

    assert len(refreshed) == 1
    assert manager.load_credential(state.id).access_token == "at-fresh"
    assert state.status is AccountStatus.OK


async def test_paused_accounts_are_not_polled(manager, stub):
    state = add(manager)
    manager.set_enabled(state.id, False)
    before = stub.calls
    await manager.refresh_all(force=True)
    assert stub.calls == before
    assert state.status is AccountStatus.DISABLED


def test_identity_masking(manager, settings):
    state = add(manager)
    assert manager.display_identity(state) == "stub@example.test"
    settings.mask_identities = True
    masked = manager.display_identity(state)
    assert masked == "st****@example.test"
    assert "stub@" not in masked


# ------------------------------------------------------- full sign-in flow


async def test_sign_in_end_to_end(settings, monkeypatch):
    """Loopback listener, state check, code exchange and credential write."""
    from watchtower_tui.providers.claude import ClaudeProvider

    port = 47399
    monkeypatch.setenv("WATCHTOWER_CLAUDE_REDIRECT_PORT", str(port))
    monkeypatch.setattr(ClaudeProvider, "identify", lambda self, cred: _identity())
    settings.open_browser = False  # so the URL comes back to us in an event

    async with FakeTokenServer() as server:
        monkeypatch.setenv("WATCHTOWER_CLAUDE_TOKEN_URL", server.url)

        vault = FileVault()
        vault.create("test passphrase")
        manager = AccountManager(settings, store=vault)
        manager.load()

        events = []
        flow = asyncio.create_task(manager.sign_in("claude", on_event=events.append))

        authorize_url = await _wait_for_url(events)
        state = parse_qs(urlsplit(authorize_url).query)["state"][0]

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback", params={"code": "THECODE", "state": state}
            )
        assert response.status_code == 200

        credential, identity = await asyncio.wait_for(flow, timeout=10)

    assert credential.access_token == "at-11111111"
    assert credential.refresh_token == "rt-22222222"
    assert credential.method is AuthMethod.OAUTH
    assert identity.email == "signed-in@example.test"

    # and the exchange really did carry a verifier matching the challenge
    sent = server.requests[0]
    assert sent["code"] == "THECODE"
    assert sent["code_verifier"]

    state_obj = manager.create_account("claude", "Signed in", credential, identity)
    assert manager.load_credential(state_obj.id).access_token == "at-11111111"
    assert "at-11111111" not in manager._repo.path.read_text()


async def _identity():
    return Identity(label="Signed in", email="signed-in@example.test", plan="Max")


async def _wait_for_url(events, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for event in events:
            if event.url:
                return event.url
        await asyncio.sleep(0.05)
    raise AssertionError(f"no authorize URL was reported; events={[e.message for e in events]}")
