# Security

## Reporting a vulnerability

Please do not open a public issue for anything that could expose someone's
tokens. Use GitHub's private vulnerability reporting (Security → Report a
vulnerability) and give me a couple of weeks before disclosing.

Things I would very much like to hear about: a way to get a token into the
log file, into the terminal, or onto disk unencrypted; a way to make the
loopback listener accept a callback it should not; anything that widens the
permissions on the app directory.

## What the threat model is

Watchtower holds live OAuth tokens for your Codex and Claude accounts. Those
tokens can spend your subscription. The design assumes:

- **Other users on the same machine are not trusted.** Hence the keychain, the
  encrypted fallback, and the restrictive permissions on the app directory.
- **Anything that reads your logs is not trusted.** Support bundles, crash
  reporters, someone looking over your shoulder. Nothing credential-shaped
  should survive the redaction filter.
- **Your own terminal scrollback is not trusted.** Tokens are never rendered,
  and there is no command to print one.
- **A local process with your privileges is out of scope.** It can read your
  keychain, your memory, and the vault key once you have typed the passphrase.
  No local application can defend against that.

## How tokens are stored

The default is your operating system's keychain: Windows Credential Manager,
macOS Keychain, or a Secret Service provider on Linux. Watchtower probes it with a
real write/read/delete round trip at startup rather than trusting the backend
list, because a headless Linux box will happily advertise a Secret Service
backend that then fails for want of a D-Bus session.

Windows caps a credential blob at 2560 bytes and an OAuth credential holding
two JWTs goes past that, so values are split across several entries with an
index. This is transparent to the rest of the app.

When there is no usable keychain, Watchtower falls back to an encrypted file:

- **Cipher** — AES-256-GCM, one ciphertext for the whole key/value map, so the
  file does not even reveal how many accounts you have.
- **KDF** — scrypt, n=2^15, r=8, p=1, 16-byte random salt.
- **Integrity** — the KDF parameters are fed to GCM as additional
  authenticated data, so a file claiming n=2 is rejected rather than opened
  cheaply. Parameters below a floor are refused outright.
- **Nonces** — 96 bits, fresh on every write. Never reused with a key.
- **Recovery** — none. No escrow, no hint, no backdoor. Forget the passphrase
  and you re-add your accounts, which takes about a minute.

The passphrase-derived key is held in a `bytearray` and zeroed when the vault
is closed. This is best effort: Python may have copied it during derivation
and there is nothing portable to be done about that.

### A note on roaming profiles

On Windows the app directory sits under `%APPDATA%`, which is the roaming
profile. On a domain-joined machine that directory may be synchronised to a
server. That is a large part of why the file vault is encrypted rather than
merely permission-protected — but if you are on a managed machine and would
rather it stayed local, set `WATCHTOWER_HOME` to somewhere under `%LOCALAPPDATA%`.

## How secrets are kept out of logs

Two independent layers, because one is not enough:

1. **Pattern matching** — JWTs, `sk-` and `sess-` prefixed keys, `Bearer`
   headers, and the usual JSON key names (`access_token`, `refresh_token`,
   `code_verifier`, `client_secret`, `password`, …). The key/value pattern runs
   first so it swallows a whole value rather than letting a narrower pattern
   redact part of it.
2. **An exact-value registry** — every token that passes through the app is
   registered and substituted wherever it appears, including inside formatted
   tracebacks.

Both run inside a `logging.Filter`, so they apply to every record regardless of
which module emitted it. `httpx` is pinned to WARNING because it logs full
request URLs at INFO and an OAuth callback URL contains an authorisation code.

Separately, `Credential` and `PkcePair` define `__repr__` to describe
themselves without their contents, so an f-string or a traceback frame cannot
leak one by accident.

## The OAuth flow

- Authorisation code with PKCE (S256 only; `plain` is not offered).
- `state` is generated from `secrets.token_bytes` and compared with
  `hmac.compare_digest`.
- The loopback listener binds `127.0.0.1`, never `0.0.0.0`.
- It is single-shot, times out after five minutes, and caps how much of a
  request it will read so a local process cannot hold the flow open.
- Requests to any path other than the callback (browsers ask for
  `/favicon.ico`) get a 404 and do not complete the flow.
- The success page is fully self-contained: no fonts, no scripts, no requests
  back out. At that moment the browser has an authorisation code in its
  address bar.
- The code verifier is never put in a URL, only in the POST body of the token
  exchange.

## Reading files

`config.json`, `accounts.json` and the usage cache are user-editable, so they
are treated as untrusted:

- size-capped at 2 MB before parsing,
- validated field by field with the wrong type falling back to the default,
- and moved aside to `*.corrupt` rather than crashing the app.

`WATCHTOWER_HOME` is expanded and resolved before use, and rejected if it points
at a file.

## What is deliberately not done

- **No telemetry.** No analytics, no crash reporting, no update check.
- **No token display.** There is no "reveal" button.
- **No credential migration between backends.** Switching `secret_backend`
  does not move your tokens; you re-add the accounts. Copying secrets around
  automatically is exactly the kind of convenience that turns into a CVE.
- **Unverified JWT decoding, in one place only.** The Codex adapter reads the
  `id_token` claims without checking the signature to get an email address and
  plan name onto a card. That token came from the provider over TLS and is
  handed straight back to them; it is never used to make an authorisation
  decision. The function that does it says so in its docstring.
