"""The OAuth client itself.

Deliberately small. It does the authorisation code grant with PKCE and a
refresh grant, and nothing else. Providers differ in one annoying way - some
want the token request form-encoded and some want JSON - so that is a knob.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from ..errors import AuthError, NetworkError, ReauthRequired
from ..logging_setup import get_logger, register_secret
from ..timefmt import utcnow
from .pkce import PkcePair

log = get_logger("auth.oauth")

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class OAuthEndpoints:
    client_id: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...] = ()
    redirect_port: int = 0
    redirect_path: str = "/callback"
    #: "form" for application/x-www-form-urlencoded, "json" for a JSON body.
    token_request_style: str = "form"
    #: Anything else the provider insists on in the authorize URL.
    extra_authorize_params: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, repr=False)
class TokenResponse:
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    token_type: str = "Bearer"
    expires_at: Any = None
    scopes: tuple[str, ...] = ()
    #: Non-secret extras the provider sent alongside, e.g. an account id.
    extras: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<TokenResponse {'with' if self.refresh_token else 'without'} refresh>"

    __str__ = __repr__

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TokenResponse:
        access = str(payload.get("access_token") or "")
        refresh = str(payload.get("refresh_token") or "")
        id_token = str(payload.get("id_token") or "")

        for secret in (access, refresh, id_token):
            register_secret(secret)

        expires_at = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            expires_at = utcnow() + timedelta(seconds=float(expires_in))

        scope = payload.get("scope")
        if isinstance(scope, str):
            scopes = tuple(s for s in scope.split() if s)
        elif isinstance(scope, list):
            scopes = tuple(str(s) for s in scope)
        else:
            scopes = ()

        known = {"access_token", "refresh_token", "id_token", "expires_in", "scope", "token_type"}
        extras = {k: v for k, v in payload.items() if k not in known and _is_plain(v)}

        return cls(
            access_token=access,
            refresh_token=refresh,
            id_token=id_token,
            token_type=str(payload.get("token_type") or "Bearer"),
            expires_at=expires_at,
            scopes=scopes,
            extras=extras,
        )


def _is_plain(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Read the claims out of a JWT **without verifying the signature**.

    This is only ever used to put an email address and plan name on a card. It
    must never gate access to anything: the token came from the provider over
    TLS and is handed straight back to them, so we are reading it for display,
    not trusting it for authorisation.
    """
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        claims = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


class OAuthClient:
    def __init__(self, endpoints: OAuthEndpoints, *, user_agent: str = "watchtower"):
        self.endpoints = endpoints
        self._user_agent = user_agent

    # -- step 1 ----------------------------------------------------------

    def authorization_url(self, *, pkce: PkcePair, state: str, redirect_uri: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.endpoints.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
        }
        if self.endpoints.scopes:
            params["scope"] = " ".join(self.endpoints.scopes)
        params.update(self.endpoints.extra_authorize_params)
        return f"{self.endpoints.authorize_url}?{urlencode(params)}"

    # -- step 2 ----------------------------------------------------------

    async def exchange_code(
        self, *, code: str, pkce: PkcePair, redirect_uri: str, state: str = ""
    ) -> TokenResponse:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.endpoints.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": pkce.verifier,
        }
        if state:
            data["state"] = state
        return await self._token_request(data)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        if not refresh_token:
            raise ReauthRequired("no refresh token stored")
        data = {
            "grant_type": "refresh_token",
            "client_id": self.endpoints.client_id,
            "refresh_token": refresh_token,
        }
        if self.endpoints.scopes:
            data["scope"] = " ".join(self.endpoints.scopes)
        response = await self._token_request(data)
        if not response.refresh_token:
            # Providers that do not rotate refresh tokens just omit the field.
            response.refresh_token = refresh_token
        return response

    # -- transport -------------------------------------------------------

    async def _token_request(self, data: dict[str, str]) -> TokenResponse:
        headers = {"Accept": "application/json", "User-Agent": self._user_agent}
        json_style = self.endpoints.token_request_style == "json"
        kwargs: dict[str, Any] = {"json": data} if json_style else {"data": data}

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
                response = await client.post(self.endpoints.token_url, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise NetworkError(
                "token endpoint timed out", friendly="The sign-in server timed out."
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"token endpoint unreachable: {type(exc).__name__}",
                friendly="Could not reach the sign-in server. Check your connection.",
            ) from exc

        if response.status_code >= 400:
            raise self._token_error(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthError(
                "token endpoint did not return JSON",
                friendly="The sign-in server sent a response we could not read.",
            ) from exc

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise AuthError(
                "token response had no access_token",
                friendly="The sign-in server did not return a token.",
            )
        return TokenResponse.from_payload(payload)

    @staticmethod
    def _token_error(response: httpx.Response) -> AuthError:
        code = ""
        description = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                code = str(payload.get("error") or "")
                description = str(payload.get("error_description") or "")
        except ValueError:
            pass

        # Never log the body: a failed refresh response can echo the token back.
        log.warning(
            "token request failed with HTTP %s (%s)", response.status_code, code or "no code"
        )

        if (
            code in {"invalid_grant", "expired_token", "invalid_request"}
            or response.status_code == 401
        ):
            return ReauthRequired(
                f"token rejected: {code or response.status_code}",
                friendly="This account's sign-in has expired. Press R on the card to sign in.",
            )
        detail = description[:200] or code or f"HTTP {response.status_code}"
        return AuthError(
            f"token request failed: {code or response.status_code}",
            friendly=f"Sign-in failed: {detail}",
        )
