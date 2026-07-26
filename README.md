# Watchtower

A small terminal dashboard for the usage limits on your Codex and Claude accounts.

[![CI](https://github.com/jedrikjames/Watchtower/actions/workflows/ci.yml/badge.svg)](https://github.com/jedrikjames/Watchtower/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

I kept losing an afternoon to hitting a five hour limit I did not know I was
close to, on whichever account I had forgotten I was signed into. So: one
screen, one card per account, a bar each, refreshed every minute.

```
  Watchtower  2 accounts                                              ● updated 12s ago · next in 48s

  ╭───────────────────────────────────────────────╮  ╭───────────────────────────────────────────────╮
  │  ⠢⡀⣿⢀⠔   Personal                          ●  │  │  ⡠⢊⠭⡑⢄   Work                              ●  │
  │  ⠶⢎⣿⡱⠶   Claude · Max 20x                     │  │  ⡇⢇⠀⡸⢸   Codex · Pro                          │
  │  ⠔⠁⣿⠈⠢   me@example.com                       │  │  ⠈⠢⠭⠔⠁   work@example.com                     │
  │                                               │  │                                               │
  │  5-hour   █████████████████████████░░░  91%   │  │  5-hour   ████████████░░░░░░░░░░░░░░░░  42%   │
  │  Weekly   █████████████░░░░░░░░░░░░░░░  46%   │  │  Weekly   ██████░░░░░░░░░░░░░░░░░░░░░░  21%   │
  │                                               │  │                                               │
  │                                               │  │                                               │
  │  resets in 2h 13m · updated 12s ago           │  │  resets in 4h 02m · updated 12s ago           │
  ╰───────────────────────────────────────────────╯  ╰───────────────────────────────────────────────╯

 a Add  r Refresh  e Rename  d Remove  s Settings  ? Help  q Quit
```

Sign-in is the same browser flow the official CLIs use. Tokens go into your
system keychain, or an encrypted file if the machine has not got one. Nothing
leaves your computer except the calls to the providers themselves.

The provider marks are the Bootstrap Icons `openai` and `claude` glyphs. On a
terminal with Sixel or Kitty graphics they are drawn as real images; anywhere
else they fall back to the same glyphs traced onto a braille dot grid, which
gives 2x4 dots per cell — enough for the shapes to survive. Both come out of
the box; `logo_style = "dots"` forces the trace if you prefer it.

## Install

### Windows, no Python needed

Grab `Watchtower.exe` from the
[latest release](https://github.com/jedrikjames/Watchtower/releases/latest)
and run it. It is a single self-contained file — no installer, nothing written
outside your own `%APPDATA%`.

Windows SmartScreen will warn you the first time, because the binary is not
code-signed (certificates cost money and this is a hobby project). "More info"
→ "Run anyway", or build it yourself from source with the instructions below.

### From source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/jedrikjames/Watchtower
cd Watchtower
pip install -e .
watchtower
```

> **Note**
> `pip install watchtower` gets you an unrelated AWS CloudWatch logging
> handler. This project is not on PyPI; install it from this repository. Both
> the distribution and the import package are `watchtower_tui` so the two can
> live side by side — the command is still plain `watchtower`.

First run drops you on an empty dashboard. Press `a`, pick a provider, finish
in the browser window that opens, and the card appears.

If you already use the Codex CLI or Claude Code on this machine, the add screen
also offers to import the credentials they have already stored, which skips the
browser entirely.

## Using it

| Key       | Does |
|-----------|------|
| `↑ ↓ ← →` | move between cards (`h j k l` too) |
| `enter`   | actions for the selected card |
| `a`       | add an account |
| `r`       | refresh everything now |
| `R`       | sign in again for the selected card |
| `e`       | rename |
| `d`       | remove |
| `space`   | pause or resume updates for a card |
| `[` `]`   | move a card left or right |
| `s`       | settings |
| `?`       | help |
| `q`       | quit |

Cards turn amber past 80% and red past 95%; both thresholds are configurable.
A card that needs attention says so, and the count of those sits in the top bar
so you do not have to scan.

There are a few subcommands for when a TUI is the wrong shape:

```bash
watchtower where       # print the data directory and what is in it
watchtower accounts    # list accounts, one per line
watchtower doctor      # check the install and report what it found
watchtower reset       # delete everything local, after asking twice
```

## Where the numbers come from

Neither vendor publishes a documented "how much of my subscription is left"
API. Both of their own CLIs call an internal endpoint for it, and that is what
Watchtower calls too:

| Provider | Sign-in | Usage |
|----------|---------|-------|
| Codex    | `auth.openai.com` OAuth 2.0 + PKCE, loopback on port 1455 | `chatgpt.com/backend-api/codex/usage` |
| Claude   | `claude.ai` OAuth 2.0 + PKCE, loopback on port 54545 | `api.anthropic.com/api/oauth/usage` |

Being undocumented, these can change without notice. The design assumes they
will:

- A provider that cannot answer returns "unavailable" and the card says so.
  It never invents a number.
- Unknown limit windows are displayed anyway, using the raw name, so a new
  limit type appearing server-side does not silently vanish.
- Every URL can be overridden with an environment variable, so you can point a
  build at a new endpoint without waiting for a release. See
  [docs/providers.md](docs/providers.md).

Anthropic's usage endpoint rate limits aggressively
([claude-code#31637](https://github.com/anthropics/claude-code/issues/31637)),
so a 429 is treated as "keep the previous figures and back off", not an error.
This is also why the refresh interval will not go below 30 seconds.

Adding a third provider means writing one class and one line in a registry.
Nothing in the UI or the storage layer knows what a Codex is.

## Where things are stored

```
Windows   %APPDATA%\Watchtower
macOS     ~/Library/Application Support/Watchtower
Linux     ~/.config/Watchtower
```

`watchtower where` will tell you. Set `WATCHTOWER_HOME` to move it, which is
handy for a portable install on a USB stick.

Non-secret metadata (account labels, plan names, last known percentages) lives
in JSON files there. **Tokens do not.** They go to:

1. **Your OS keychain** — Windows Credential Manager, macOS Keychain, or a
   Secret Service provider on Linux. This is the default.
2. **An encrypted file** — only when there is no usable keychain, e.g. a
   headless Linux box. AES-256-GCM with a scrypt-derived key, and it asks for
   a passphrase on startup. There is no recovery if you forget it.

Other things worth knowing:

- The app directory is created with restricted permissions (`chmod 700`, or an
  ACL granting only your user on Windows).
- The log file redacts credentials at two levels: patterns for anything shaped
  like a token, plus a registry of the exact values in play. `Credential`
  objects have a `__repr__` that refuses to print themselves, so a stray
  traceback cannot leak one either.
- Tokens are never rendered in the UI. There is no "show token" button and no
  plans for one.
- Config and account files are treated as untrusted input: size-capped,
  type-checked field by field, and moved aside rather than crashing if they
  are corrupt.

More detail in [SECURITY.md](SECURITY.md).

## Configuration

Settings live in `config.json` next to everything else and are editable from
the settings screen (`s`). Editing the file by hand is fine too — anything
unparseable falls back to the default with a line in the log rather than
refusing to start.

| Setting | Default | Notes |
|---------|---------|-------|
| `refresh_seconds` | `60` | clamped to 30–3600 |
| `secret_backend` | `auto` | `auto`, `keyring`, or `file` |
| `mask_identities` | `false` | blanks out emails, for screen sharing |
| `warn_at_percent` | `80` | amber above this |
| `danger_at_percent` | `95` | red above this |
| `confirm_remove` | `true` | |
| `open_browser` | `true` | turn off on headless machines and copy the URL |
| `theme` | `textual-dark` | any Textual theme name |
| `logo_style` | `image` | real icons where the terminal has Sixel or Kitty graphics; `dots` forces the braille trace |

## Development

```bash
git clone https://github.com/jedrikjames/Watchtower && cd Watchtower
python -m venv .venv && . .venv/bin/activate     # .venv\Scripts\activate on Windows
pip install -e ".[dev]"

pytest              # 123 tests, no network access needed
ruff check src tests
ruff format src tests
```

The test suite runs the whole TUI headless through Textual's `Pilot`, so the
keyboard flows are covered by real key presses rather than method calls. The
sign-in test stands up a local fake token endpoint and drives a genuine
authorisation code exchange through the real loopback listener.

To build the Windows binary yourself:

```bash
pip install -e ".[dev]" pyinstaller
pyinstaller packaging/watchtower.spec
```

The provider icons are rendered from the upstream SVGs by
`packaging/render_icons.py`. The PNGs are committed, so you only need to run it
if the source icons change.

### Layout

```
src/watchtower_tui/
  cli.py             argparse entry point
  models.py          Account, Credential, UsageReport and friends
  settings.py        config.json, defensively parsed
  store.py           accounts.json and the usage cache
  paths.py           where everything lives
  fsutil.py          atomic writes, restrictive permissions
  logging_setup.py   the redaction filter
  auth/              PKCE, the loopback listener, the OAuth client
  secretstore/       keychain backend, encrypted vault, backend selection
  providers/         the adapter interface, Codex, Claude
  service/           account manager and the refresh scheduler
  tui/               the Textual app, widgets, screens, stylesheet
```

The dependency arrow only ever points one way: `tui` → `service` →
`providers`/`secretstore` → core. The service layer has no idea a terminal
exists, which is what makes the headless subcommands and the tests
straightforward.

## Status

Beta, and honest about it. The sign-in, storage and refresh machinery are
solid and well covered. The usage endpoints are the fragile part, for the
reasons above — if a card starts saying "unavailable", that is usually a
vendor change rather than a bug in the storage layer, and
[an issue](https://github.com/jedrikjames/Watchtower/issues/new/choose) with
the response shape is the fastest way to get it fixed.

Not affiliated with, endorsed by, or supported by OpenAI or Anthropic.

## Licence

MIT. See [LICENSE](LICENSE).
