"""Shared HTTP plumbing for provider adapters.

The point of this module is that every provider maps transport failures onto
the same small set of exceptions, so the refresh loop only has to understand
one vocabulary.
"""

from __future__ import annotations

from typing import Any

import httpx

from .. import __version__
from ..errors import NetworkError, ProviderError, RateLimited, ReauthRequired
from ..logging_setup import get_logger

log = get_logger("providers.http")

USER_AGENT = f"watchtower/{__version__} (+https://github.com/jedrikjames/Watchtower)"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        # It can also be an HTTP date. Not worth parsing; the caller has a
        # sensible default backoff anyway.
        return None


async def get_json(
    http: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None, label: str = ""
) -> Any:
    """GET a JSON document, translating everything that can go wrong."""
    what = label or url
    try:
        response = await http.get(url, headers=headers or {})
    except httpx.TimeoutException as exc:
        raise NetworkError(
            f"{what} timed out", friendly="The provider took too long to respond."
        ) from exc
    except httpx.HTTPError as exc:
        raise NetworkError(
            f"{what} unreachable: {type(exc).__name__}",
            friendly="Could not reach the provider. Check your connection.",
        ) from exc

    return handle_response(response, label=what)


def handle_response(response: httpx.Response, *, label: str = "") -> Any:
    status = response.status_code

    if status == 429:
        raise RateLimited(
            f"{label} rate limited",
            retry_after=_retry_after(response),
            friendly="The provider is rate limiting us. Showing the last known figures.",
        )

    if status in (401, 403):
        raise ReauthRequired(
            f"{label} returned {status}",
            friendly="This account's sign-in is no longer accepted. Press R to sign in again.",
        )

    if status >= 500:
        raise ProviderError(
            f"{label} returned {status}",
            friendly="The provider is having trouble right now. Will retry shortly.",
        )

    if status >= 400:
        raise ProviderError(
            f"{label} returned {status}",
            friendly=f"The provider rejected the request (HTTP {status}).",
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            f"{label} did not return JSON",
            friendly="The provider sent a response we could not read.",
        ) from exc
