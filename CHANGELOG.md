# Changelog

Notable changes. Follows [Keep a Changelog](https://keepachangelog.com/)
loosely and [semver](https://semver.org/) properly.

## [Unreleased]

## [0.2.0] - 2026-07-26

### Added
- **Real provider icons.** On a terminal with Sixel or Kitty graphics the
  Bootstrap Icons `openai` and `claude` glyphs are drawn as actual images.
  Anywhere else they fall back to the same glyphs traced onto a braille dot
  grid, so nothing is lost where they cannot be drawn. This is the default.
  The SVGs are rasterised by `packaging/render_icons.py`, which carries its
  own scanline fill with nonzero winding — cairosvg and reportlab both want a
  native cairo library, which is a reliable way to make a build script fail on
  Windows. The Windows binary bundles the image support.
- `watchtower doctor` reports the configured logo style, whether the image
  extra is present, whether the terminal answered the graphics query, and
  whether the icon assets were bundled. Running one build while editing
  another is the obvious way to be confused about missing icons.
- `logo_style` is in the settings screen.

### Changed
- **Textual's own chrome is gone**: no `ctrl+p` command palette, `ctrl+q` no
  longer quits (`q` still does), and the docked footer is replaced by a
  centred hint line with reversed key caps. Rename, remove and refresh are no
  longer advertised there; the keys still work and the actions live behind
  enter on a card.
- Toasts are disabled. `notify()` puts the message in the status line instead,
  because errors still have to reach the user.
- Everything but the modals is transparent, so the terminal's own background
  shows through.
- The cards no longer repaint every second. The tick existed to keep
  "updated .. ago" honest, but repainting a card redraws its mark, and
  re-emitting a Sixel image at 1Hz is visible as flicker. Card bodies refresh
  when a refresh pass produces something new, and "updated .. ago" has been
  dropped from the card entirely.
- Two logo styles instead of three: `image` and `dots`. Box drawing is gone and
  braille is renamed to dots. Existing configs are migrated, not rejected.

### Fixed
- **`[` and `]` never reordered cards.** The bindings used `bracketleft` and
  `bracketright`; Textual calls those keys `left_square_bracket` and
  `right_square_bracket`, so nothing a keyboard produces ever matched. The test
  that covered it pressed the same wrong names — Pilot synthesises an event of
  whatever name you give it — so it asserted the typo and passed against a
  broken feature. The replacement presses the real characters.
- The image mark was invisible on terminals that support Sixel perfectly well,
  for two independent reasons. The support probe ran from `compose()`, after
  Textual had started, where Textual's input thread eats the terminal's reply
  and every terminal looks incapable. And the stylesheet matched the widget by
  library type name, which textual-image swaps depending on the protocol — with
  Sixel active the class is literally called `Image`, so no rule matched and the
  mark got no size and no layer.
- Image marks were stretched vertically: the slot was a fixed five cells, but
  cells are not square, so three rows at a typical 10x20 needed six columns to
  stay square. It is measured now.
- The gutter is sized from the mark that was actually built rather than the one
  that was asked for, so a fallback no longer leaves a stray column of padding.

## [0.1.2] - 2026-07-26

### Fixed
- **Claude cards showed "Stripe Subscription" instead of the plan.** The
  profile's `billing_type` was first in the fallback chain, but it describes
  how the account is paid for, not what it bought — it reads
  `stripe_subscription` on Pro and on Max alike. The tier is now resolved from
  `rate_limit_tier`, `subscription_type` and the `has_claude_*` flags, with
  billing mechanisms filtered out entirely. A blank plan is shown rather than
  a wrong one.
- **The status line always read "next in now".** The scheduler set the next
  deadline in its sleep step, which runs *after* the completion callback the UI
  listens to, so the dashboard was always reading the previous cycle's
  deadline — already in the past. It is computed before the callback fires now,
  and the status line re-reads it every second.
- **Codex reported "This account's sign-in is no longer accepted" for a
  perfectly good sign-in.** A 401 from an undocumented usage endpoint was taken
  as proof the credential was dead. The claim is now checked: the token is
  refreshed and the call retried, and only a failed refresh counts as expired.
  If the refresh works and usage is still refused, the card says the provider
  would not return figures, which is what actually happened.
- Codex sends the `originator` header its own CLI uses, which the ChatGPT
  backend appears to want.

### Changed
- Provider marks are now the Bootstrap Icons `openai` and `claude` glyphs
  traced onto a braille dot grid. Braille packs 2x4 dots into a cell, so the
  same five-by-three space carries eight times the detail of box-drawing
  characters. Set `logo_style = "blocks"` for the old marks if your terminal
  font has no braille coverage.
- New `watchtower doctor` subcommand: reports the data directory, the secret
  backend actually in use, whether the stylesheet loaded and which providers
  are registered. Mostly for diagnosing a packaged build.
- The Claude adapter logs the *field names* returned by the profile endpoint
  (never the values) at INFO, so a tier field moving again is a five minute fix
  rather than guesswork.

## [0.1.1] - 2026-07-26

### Fixed
- **Sign in with ChatGPT failed outright.** The token exchange sent `state` in
  the request body. RFC 6749 does not define `state` as a token endpoint
  parameter — it belongs to the authorization request and comes back on the
  callback — and OpenAI rejects it with `Unknown parameter: 'state'`. It is
  now opt-in per provider and off by default; Anthropic's endpoint does expect
  it, so Claude keeps sending it.
- Error bodies with a nested `error` object (OpenAI's shape) were read as if
  the field were a flat string, so a raw Python dict was quoted back at the
  user instead of a sentence. Both the RFC shape and the nested one are now
  handled.
- `invalid_request` no longer maps to "your sign-in has expired". That code
  means the request we built was wrong, and sending someone round the
  re-authentication loop over our own bug wastes their time.

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

[Unreleased]: https://github.com/jedrikjames/Watchtower/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jedrikjames/Watchtower/releases/tag/v0.2.0
[0.1.2]: https://github.com/jedrikjames/Watchtower/releases/tag/v0.1.2
[0.1.1]: https://github.com/jedrikjames/Watchtower/releases/tag/v0.1.1
[0.1.0]: https://github.com/jedrikjames/Watchtower/releases/tag/v0.1.0
