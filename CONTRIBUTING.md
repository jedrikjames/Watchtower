# Contributing

Bug reports and pull requests are welcome. It is a small project, so nothing
here is heavyweight.

## Getting set up

```bash
git clone https://github.com/jedrikjames/Watchtower && cd Watchtower
python -m venv .venv && . .venv/bin/activate     # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest
```

The tests need no network access and never touch your real app directory —
`conftest.py` points `WATCHTOWER_HOME` at a temporary directory for every test.

## Before opening a pull request

```bash
ruff format src tests
ruff check src tests
pytest
```

CI runs the same three on Linux, macOS and Windows.

## The one rule

**Nothing that can lead to a token being written somewhere it should not be.**
Concretely:

- Do not log a request body, a response body, or a full URL with a query
  string. `providers/http.py` logs status codes and error codes on purpose.
- Do not add a `__repr__` or an f-string that could interpolate a credential.
  `Credential` and `PkcePair` refuse to print themselves; keep it that way.
- Do not render a token in the UI, even truncated.
- If you add a field that holds a secret, register it with `register_secret`
  and add a case to `tests/test_redaction.py`.

If a change touches `secretstore/`, `auth/` or `logging_setup.py`, say so in
the PR description and expect the review to be slower.

## What makes a good bug report

For a card showing the wrong number or "unavailable", the most useful thing by
far is the **shape** of the provider's response — key names and value types.
Redact the values. `WATCHTOWER_LOG_LEVEL=DEBUG` gives more detail in
`watchtower.log`; the redaction filter runs at every level, but read it before you
paste it anyway.

Otherwise: what you did, what happened, what you expected, and the output of
`watchtower where`.

## Things I would happily merge

- **Another provider.** See [docs/providers.md](docs/providers.md). One class
  and one line in a registry.
- **Fixes for a vendor changing an endpoint.** These will keep happening.
- **Better platform coverage for the file permission hardening.** The Windows
  path shells out to `icacls`, which works but is not elegant.
- **Accessibility work.** The colour thresholds carry meaning; a shape or
  label alternative for colour-blind users would be a genuine improvement.

## Things I would probably say no to

- A daemon, a tray icon, or anything that runs when the TUI is not open.
- Telemetry of any kind.
- Automatic migration of credentials between storage backends.
- Displaying, exporting or copying raw tokens.
- Dependencies. There are five, they are all load-bearing, and I would like to
  keep it that way.

## Style

Ruff handles formatting, so do not hand-wrap. Beyond that: comments should
explain why rather than what, and the codebase leans on that fairly heavily in
the crypto and OAuth paths — if you find yourself deleting one of those
comments, please check you are not also deleting the reason the code is shaped
the way it is.
