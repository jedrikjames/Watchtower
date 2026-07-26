# Changelog

Notable changes. Follows [Keep a Changelog](https://keepachangelog.com/)
loosely and [semver](https://semver.org/) properly.

## [Unreleased]

## [0.1.0] - 2026-07-26

First public release.

### Added
- One card per account showing the provider logo, plan, usage bars and
  percentages, refreshed every minute.
- Browser sign-in for Codex ("Sign in with ChatGPT") and Claude, using OAuth
  2.0 with PKCE and a loopback redirect.
- Import credentials from a local Codex CLI or Claude Code install, so you can
  add an account without going through the browser at all.
- Tokens stored in the OS keychain, falling back to an AES-256-GCM file vault
  with a scrypt-derived key on machines without one.
- Keyboard-driven account management: add, rename, remove, re-authenticate,
  pause and reorder.
- Settings screen for the refresh interval, warning thresholds, identity
  masking and theme.
- `watchtower where`, `watchtower accounts`, `watchtower doctor` and
  `watchtower reset` for the cases where a TUI is the wrong shape.
- Environment overrides for every provider URL and loopback port, so an
  endpoint moving does not have to mean waiting for a release.
- Single-file Windows binary built with PyInstaller.

### Notes on things that were awkward

Recorded here because they are the kind of thing that gets rediscovered
painfully. All were found and fixed before this release.

- Windows file permission hardening must not apply `(OI)(CI)` inheritance
  flags to files, only to directories. On a file those produce an
  inherit-only ACE granting nothing, which combined with `/inheritance:r`
  locks the application out of its own `accounts.json`.
- Windows Credential Manager caps a credential blob at 2560 bytes
  (`CRED_MAX_CREDENTIAL_BLOB_SIZE`). An OAuth credential holding two JWTs
  exceeds it and fails with an opaque "Stub received bad data", so values are
  split across several entries with an index.
- Anthropic's usage endpoint rate limits hard enough
  ([claude-code#31637](https://github.com/anthropics/claude-code/issues/31637))
  that a 429 has to be treated as "keep the previous figures and back off"
  rather than an error. This is why the refresh interval will not go below
  30 seconds.
- Textual resolves a relative `CSS_PATH` against the module directory, which
  does not survive being frozen into a single-file binary. It is derived from
  `__file__` instead.
- PyInstaller runs its entry script as a top-level module, so a package's
  `__main__.py` cannot be used as the entry point; the relative import fails.
  There is a separate launcher in `packaging/entry.py`.

[Unreleased]: https://github.com/jedrikjames/Watchtower/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jedrikjames/Watchtower/releases/tag/v0.1.0
