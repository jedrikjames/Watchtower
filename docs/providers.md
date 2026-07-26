# Writing a provider adapter

Everything provider-specific lives in `src/watchtower/providers/`. The rest of the
app talks to providers through one small interface and looks them up by id, so
adding one does not touch the UI, the storage layer or the refresh loop.

## The interface

Subclass `Provider` and fill in the parts you need.

```python
from watchtower.auth.oauth import OAuthEndpoints
from watchtower.models import Credential, UsageReport, UsageWindow
from watchtower.providers.base import Identity, Provider, ProviderInfo
from watchtower.providers.http import client, get_json


class AcmeProvider(Provider):
    info = ProviderInfo(
        id="acme",
        display_name="Acme",
        accent="#5b8def",              # colours the logo on the card
        logo=("╱▔▔▔╲", "▏ A ▕", "╲▁▁▁╱"),   # three lines, same width
        signin_label="Sign in with Acme",
    )

    def oauth_endpoints(self) -> OAuthEndpoints:
        return OAuthEndpoints(
            client_id=self.env_override("WATCHTOWER_ACME_CLIENT_ID", CLIENT_ID),
            authorize_url="https://acme.example/oauth/authorize",
            token_url="https://acme.example/oauth/token",
            scopes=("openid", "email"),
            redirect_port=self.env_port("WATCHTOWER_ACME_REDIRECT_PORT", 1456),
            token_request_style="form",   # or "json"
        )

    async def fetch_usage(self, credential: Credential) -> UsageReport:
        url = self.env_override("WATCHTOWER_ACME_USAGE_URL", USAGE_URL)
        headers = {"Authorization": f"Bearer {credential.bearer}"}
        async with client() as http:
            payload = await get_json(http, url, headers=headers, label="acme usage")
        return self.parse_usage(payload)
```

Then register it in `providers/__init__.py`:

```python
_register(AcmeProvider())
```

That is the whole integration. The card, the keyboard flows, the credential
storage and the refresh scheduling all come for free.

## Required

**`info`** — a `ProviderInfo`. The logo is three strings of equal width; five
or six columns looks right against the card layout.

**`fetch_usage(credential) -> UsageReport`** — the only method you must write.
Return a `UsageReport` holding one `UsageWindow` per limit bucket. Raise
`UsageUnavailable` if the provider is reachable but will not say.

## Optional

**`oauth_endpoints()`** — needed if `supports_oauth` is true (the default).

**`identify(credential) -> Identity`** — the email and plan shown under the
account name. Best effort: it is called inside a `try` and a card is perfectly
happy without it.

**`finalise_credential(credential, tokens)`** — a hook for pulling
provider-specific bits out of a token response. Codex uses it to keep the
`id_token`, because the account id and plan name only exist in its claims.

**`import_candidates()`** — if the provider has an official CLI that stores
credentials on disk, return them here and the add screen will offer to adopt
them. Read defensively; the file belongs to another program.

**`refresh_credential(credential)`** — the base class already does a standard
refresh grant. Override only for an unusual flow.

**`credential_from_api_key(key)`** — implement alongside
`supports_api_key = True` if an API key is genuinely useful. Neither shipping
provider does this, because a platform API key is billed separately from a
subscription and says nothing about its limits.

## Parsing usage well

Write `parse_usage` as a `classmethod` with no I/O. It makes the interesting
half testable against a captured payload, which is what
`tests/test_providers.py` does.

Four rules, learned from both shipping adapters:

**Read the window length, do not assume it.** Codex reports
`window_minutes`; deriving the label from it means the card stays correct when
a vendor changes a five hour window to six.

**Show windows you do not recognise.** Keep a map of known keys for pretty
names, then fall through to the raw key title-cased. A new limit type
appearing server-side should show up as an ugly label, not disappear.

**Clamp and type-check every number.** A percentage arriving as `"high"`, or
as `999`, must not draw a bar off the edge of the card.

**Fail as `UsageUnavailable`, not as an exception.** The card then says
"unavailable" and the app carries on. Anything raised out of `fetch_usage`
that is not an `WatchtowerError` gets logged and shown as a generic message,
which is a worse experience for everyone.

## Errors

Use the helpers in `providers/http.py` and the exceptions in `errors.py`.
`get_json` already maps the transport layer onto them:

| Situation | Raise | The refresh loop then |
|-----------|-------|-----------------------|
| 429 | `RateLimited(retry_after=…)` | keeps the last figures, backs off |
| 401 / 403 | `ReauthRequired` | marks the card "sign-in needed" |
| 5xx, timeout, DNS | `ProviderError` / `NetworkError` | marks it stale, backs off |
| reachable but no usage | `UsageUnavailable` | shows the message, no backoff |

Every exception carries a `friendly` string, and that string is the only thing
the user sees. Keep it a sentence, keep it free of URLs and identifiers, and
say what to do next where you can.

## Environment overrides

Use `env_override` for every URL and `env_port` for the loopback port. These
are the escape hatch for an undocumented endpoint moving: someone can point
their install at the new URL the same day rather than waiting for a release.
They are also what makes the end-to-end sign-in test possible, since it points
a real provider at a local fake token server.

## Testing

`tests/test_providers.py` is the pattern to copy: pin the happy path, then
assert that malformed, partial and hostile payloads degrade instead of raising.
If you want an end-to-end sign-in test, `tests/test_auth.py::FakeTokenServer`
plus an `WATCHTOWER_<ID>_REDIRECT_PORT` override gives you one without touching
the network.
