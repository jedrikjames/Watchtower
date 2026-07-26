"""The every-minute poll.

One asyncio task. It refreshes, sleeps until the next tick, and repeats. A
manual refresh just wakes the sleep early rather than starting a second loop,
so pressing r fifteen times does not produce fifteen concurrent polls.

The interval is read fresh on every tick, so changing it in settings takes
effect on the next cycle without a restart.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import timedelta

from ..logging_setup import get_logger
from ..models import AccountState
from ..timefmt import utcnow
from .manager import AccountManager

log = get_logger("service.refresh")

#: +/- this fraction of the interval, so a room full of these does not all
#: hit the provider on the same second.
JITTER = 0.08

Callback = Callable[[list[AccountState]], Awaitable[None] | None]


class RefreshScheduler:
    def __init__(
        self,
        manager: AccountManager,
        *,
        on_start: Callable[[], None] | None = None,
        on_complete: Callback | None = None,
    ):
        self._manager = manager
        self._on_start = on_start
        self._on_complete = on_complete
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._force_next = False
        self._in_flight = False
        self._last_run = None
        self._next_run = None
        self._next_delay: float | None = None

    # -- observable state -------------------------------------------------

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    @property
    def last_run(self):
        return self._last_run

    @property
    def next_run(self):
        return self._next_run

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- control ----------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name="watchtower-refresh")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def request_now(self, *, force: bool = True) -> None:
        """Refresh as soon as the loop comes round, ignoring any backoff."""
        self._force_next = self._force_next or force
        self._wake.set()

    # -- the loop ---------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while True:
                await self._tick()
                await self._sleep_until_next()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let the dashboard silently stop updating.
            log.exception("the refresh loop stopped unexpectedly")
            raise

    async def _tick(self) -> None:
        force, self._force_next = self._force_next, False
        self._in_flight = True
        if self._on_start is not None:
            self._on_start()
        try:
            updated = await self._manager.refresh_all(force=force)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("refresh pass failed: %s", type(exc).__name__)
            updated = []
        finally:
            self._in_flight = False
            self._last_run = utcnow()
            # Work out when the next pass is due *before* telling anyone this
            # one finished. on_complete is what makes the UI read next_run, and
            # if the sleep set it afterwards the dashboard would always be
            # looking at the previous cycle's deadline - which has just passed,
            # so it rendered a permanent "next in now".
            self._next_delay = self._pick_delay()
            self._next_run = self._last_run + timedelta(seconds=self._next_delay)

        if self._on_complete is not None:
            result = self._on_complete(updated)
            if asyncio.iscoroutine(result):
                await result

    def _pick_delay(self) -> float:
        base = max(5, int(self._manager.settings.refresh_seconds))
        return base * (1 + random.uniform(-JITTER, JITTER))

    async def _sleep_until_next(self) -> None:
        delay = self._next_delay if self._next_delay is not None else self._pick_delay()
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        finally:
            self._wake.clear()
