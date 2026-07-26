"""PKCE, the loopback listener, and a full sign-in against a local fake provider.

The end-to-end test is worth the setup: it exercises the redirect listener, the
state check, the code exchange and the credential write in one go, which is the
sequence most likely to break when a dependency changes underneath us.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.helpers import FakeTokenServer
from watchtower_tui.auth import LoopbackReceiver, new_pkce, new_state
from watchtower_tui.auth.oauth import OAuthClient, OAuthEndpoints, decode_jwt_claims
from watchtower_tui.errors import AuthCancelled, AuthError, AuthTimeout, ReauthRequired

CALLBACK_PORT = 47311


# --------------------------------------------------------------------- PKCE


def test_challenge_is_the_sha256_of_the_verifier():
    pair = new_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
    assert pair.challenge == expected.decode().rstrip("=")
    assert pair.method == "S256"


def test_verifier_meets_the_rfc_length_rule():
    verifier = new_pkce().verifier
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier


def test_each_pkce_pair_is_unique():
    assert len({new_pkce().verifier for _ in range(50)}) == 50
    assert len({new_state() for _ in range(50)}) == 50


# ----------------------------------------------------------------- loopback


async def hit(query: str, port: int = CALLBACK_PORT, path: str = "/callback") -> int:
    await asyncio.sleep(0.05)
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"http://127.0.0.1:{port}{path}?{query}")
        return response.status_code


async def test_callback_delivers_the_code():
    async with LoopbackReceiver(CALLBACK_PORT, timeout=5) as receiver:
        task = asyncio.create_task(hit("code=THECODE&state=STATE"))
        result = await receiver.wait("STATE")
        assert result.code == "THECODE"
        assert await task == 200
        assert "THECODE" not in repr(result)


async def test_state_mismatch_is_rejected():
    async with LoopbackReceiver(CALLBACK_PORT, timeout=5) as receiver:
        task = asyncio.create_task(hit("code=THECODE&state=FORGED"))
        with pytest.raises(AuthError):
            await receiver.wait("STATE")
        assert await task == 400


async def test_user_denial_reads_as_cancelled():
    async with LoopbackReceiver(CALLBACK_PORT, timeout=5) as receiver:
        asyncio.create_task(hit("error=access_denied"))
        with pytest.raises(AuthCancelled):
            await receiver.wait("STATE")


async def test_missing_code_is_an_error():
    async with LoopbackReceiver(CALLBACK_PORT, timeout=5) as receiver:
        asyncio.create_task(hit("state=STATE"))
        with pytest.raises(AuthError):
            await receiver.wait("STATE")


async def test_other_paths_do_not_complete_the_flow():
    """Browsers ask for /favicon.ico; that must not be taken for the callback."""
    async with LoopbackReceiver(CALLBACK_PORT, timeout=0.6) as receiver:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{CALLBACK_PORT}/favicon.ico")
        assert response.status_code == 404
        with pytest.raises(AuthTimeout):
            await receiver.wait("STATE")


async def test_timeout():
    async with LoopbackReceiver(CALLBACK_PORT, timeout=0.3) as receiver:
        with pytest.raises(AuthTimeout):
            await receiver.wait("STATE")


async def test_busy_port_gives_a_useful_message():
    async with LoopbackReceiver(CALLBACK_PORT, timeout=1):
        with pytest.raises(AuthError) as caught:
            async with LoopbackReceiver(CALLBACK_PORT, timeout=1):
                pass
    assert "already in use" in caught.value.friendly


# -------------------------------------------------------------- token server


def endpoints(token_url: str, style: str = "form") -> OAuthEndpoints:
    return OAuthEndpoints(
        client_id="test-client",
        authorize_url="https://provider.test/authorize",
        token_url=token_url,
        scopes=("openid", "email"),
        redirect_port=CALLBACK_PORT,
        token_request_style=style,
    )


async def test_code_exchange_sends_the_verifier():
    async with FakeTokenServer() as server:
        client = OAuthClient(endpoints(server.url))
        pkce = new_pkce()
        tokens = await client.exchange_code(
            code="THECODE", pkce=pkce, redirect_uri="http://localhost:1/cb", state="STATE"
        )

    assert tokens.access_token == "at-11111111"
    assert tokens.refresh_token == "rt-22222222"
    assert tokens.expires_at is not None
    assert tokens.scopes == ("user:profile", "user:inference")
    assert tokens.extras["account_id"] == "acct-1"

    sent = server.requests[0]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code_verifier"] == pkce.verifier
    assert sent["code"] == "THECODE"


async def test_json_style_token_request():
    async with FakeTokenServer() as server:
        client = OAuthClient(endpoints(server.url, style="json"))
        await client.exchange_code(code="C", pkce=new_pkce(), redirect_uri="http://localhost:1/cb")
    assert server.requests[0]["grant_type"] == "authorization_code"


async def test_refresh_keeps_the_old_token_when_none_is_returned():
    response = {"access_token": "at-new-value", "expires_in": 60}
    async with FakeTokenServer(response) as server:
        client = OAuthClient(endpoints(server.url))
        tokens = await client.refresh("rt-original-value")
    assert tokens.access_token == "at-new-value"
    assert tokens.refresh_token == "rt-original-value"


async def test_invalid_grant_asks_for_reauth():
    async with FakeTokenServer({"error": "invalid_grant"}, status=400) as server:
        client = OAuthClient(endpoints(server.url))
        with pytest.raises(ReauthRequired):
            await client.refresh("rt-dead-token")


async def test_refresh_without_a_token_fails_fast():
    client = OAuthClient(endpoints("http://127.0.0.1:1/token"))
    with pytest.raises(ReauthRequired):
        await client.refresh("")


def test_authorization_url_carries_the_challenge():
    client = OAuthClient(endpoints("http://x/token"))
    pkce = new_pkce()
    url = client.authorization_url(pkce=pkce, state="STATE", redirect_uri="http://localhost:1/cb")
    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge"] == [pkce.challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == ["STATE"]
    assert params["response_type"] == ["code"]
    assert pkce.verifier not in url, "the verifier must never leave the machine in a URL"


def test_jwt_claims_are_decoded_without_verification():
    payload = base64.urlsafe_b64encode(b'{"email":"a@b.co"}').decode().rstrip("=")
    assert decode_jwt_claims(f"eyJhbGciOiJub25lIn0.{payload}.sig") == {"email": "a@b.co"}


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b", "eyJ.!!!!.c"])
def test_bad_jwts_return_nothing(bad):
    assert decode_jwt_claims(bad) == {}


# -------------------------------------------------- provider quirks (regressions)


async def test_state_is_not_sent_to_the_token_endpoint_by_default():
    """RFC 6749 has no state parameter on the token request.

    OpenAI rejects it outright with "Unknown parameter: 'state'", which broke
    Sign in with ChatGPT entirely.
    """
    async with FakeTokenServer() as server:
        client = OAuthClient(endpoints(server.url))
        await client.exchange_code(
            code="C", pkce=new_pkce(), redirect_uri="http://localhost:1/cb", state="STATE"
        )
    assert "state" not in server.requests[0]


async def test_state_is_sent_when_a_provider_asks_for_it():
    """Anthropic's endpoint expects it, so it stays opt-in per provider."""
    async with FakeTokenServer() as server:
        quirky = replace(endpoints(server.url, style="json"), send_state_with_code=True)
        client = OAuthClient(quirky)
        await client.exchange_code(
            code="C", pkce=new_pkce(), redirect_uri="http://localhost:1/cb", state="STATE"
        )
    assert server.requests[0]["state"] == "STATE"


def test_shipping_providers_agree_with_their_endpoints():
    from watchtower_tui.providers.claude import ClaudeProvider
    from watchtower_tui.providers.codex import CodexProvider

    assert ClaudeProvider().oauth_endpoints().send_state_with_code is True
    assert CodexProvider().oauth_endpoints().send_state_with_code is False


async def test_nested_error_objects_produce_a_readable_message():
    """OpenAI nests the error; reading it as a flat string leaked a raw dict."""
    body = {
        "error": {
            "message": "Unknown parameter: 'state'.",
            "type": "invalid_request_error",
            "param": "state",
            "code": "unknown_parameter",
        }
    }
    async with FakeTokenServer(body, status=400) as server:
        client = OAuthClient(endpoints(server.url))
        with pytest.raises(AuthError) as caught:
            await client.exchange_code(code="C", pkce=new_pkce(), redirect_uri="http://x/cb")

    friendly = caught.value.friendly
    assert friendly == "Sign-in failed: Unknown parameter: 'state'."
    assert "{" not in friendly and "'type'" not in friendly


async def test_flat_rfc_error_bodies_still_work():
    body = {"error": "invalid_scope", "error_description": "That scope is not allowed."}
    async with FakeTokenServer(body, status=400) as server:
        client = OAuthClient(endpoints(server.url))
        with pytest.raises(AuthError) as caught:
            await client.exchange_code(code="C", pkce=new_pkce(), redirect_uri="http://x/cb")
    assert caught.value.friendly == "Sign-in failed: That scope is not allowed."


async def test_a_malformed_request_does_not_tell_the_user_to_re_authenticate():
    """invalid_request is our bug, not a dead credential."""
    body = {"error": "invalid_request", "error_description": "Bad parameter."}
    async with FakeTokenServer(body, status=400) as server:
        client = OAuthClient(endpoints(server.url))
        with pytest.raises(AuthError) as caught:
            await client.exchange_code(code="C", pkce=new_pkce(), redirect_uri="http://x/cb")
    assert not isinstance(caught.value, ReauthRequired)
