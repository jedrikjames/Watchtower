"""PKCE, per RFC 7636.

We only ever use S256. "plain" is still in the spec but there is no reason to
offer it, and a provider that demands it is one we should not be talking to.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

VERIFIER_BYTES = 32  # -> 43 characters, the minimum the RFC allows
STATE_BYTES = 24


def _b64url(raw: bytes) -> str:
    """base64url with the padding stripped, which is what the RFC asks for."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(slots=True, repr=False)
class PkcePair:
    verifier: str
    challenge: str
    method: str = "S256"

    def __repr__(self) -> str:
        # The verifier is as good as a password until the flow completes.
        return f"<PkcePair {self.method}>"

    __str__ = __repr__


def new_pkce() -> PkcePair:
    verifier = _b64url(secrets.token_bytes(VERIFIER_BYTES))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return PkcePair(verifier=verifier, challenge=_b64url(digest))


def new_state() -> str:
    """Anti-CSRF value tying the callback back to the request we started."""
    return _b64url(secrets.token_bytes(STATE_BYTES))
