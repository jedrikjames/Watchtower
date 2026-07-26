"""Logging that will not leak a token.

Everything goes to a rotating file in the app directory. Nothing goes to
stdout or stderr, because writing to either would tear a hole in the TUI.

Two layers of defence:

* A set of patterns for things that look like credentials (JWTs, sk- keys,
  Bearer headers, common JSON key names).
* A registry of exact secret values. Anything handed to ``register_secret``
  is replaced wherever it turns up, including inside tracebacks.

Substring replacement means the registry has to hold the real values in
memory, so it is deliberately short lived - call ``forget_secrets`` when an
account is removed.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path

REDACTED = "[redacted]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Key/value form first. It swallows the whole value, which stops the
    # narrower patterns below from redacting a substring and leaving the
    # surrounding punctuation behind as "[redacted]]".
    # "access_token": "...", access_token=..., refresh_token: ...
    re.compile(
        r"(?i)\b(access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret"
        r"|code[_-]?verifier|authorization[_-]?code|passphrase|password|api[_-]?key)"
        r"(\"?\s*[:=]\s*\"?)([^\"'\s,;}\])]+)"
    ),
    # JWT-ish: header.payload.signature
    re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"),
    # OpenAI style keys, including the sk-ant- and sk-proj- variants
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bsess-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
)

# Exact values we have been told are secret. Kept as a plain set of strings
# only long enough to build the substitution table; see _Registry below.
_exact: set[str] = set()


def register_secret(value: str | None) -> None:
    """Mark an exact string as never-to-be-logged."""
    if value and len(value) >= 8:
        _exact.add(value)


def forget_secrets() -> None:
    _exact.clear()


def scrub(text: str) -> str:
    """Remove anything that looks like, or is known to be, a credential."""
    if not text:
        return text
    for secret in _exact:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = scrub(record.getMessage())
        except Exception:  # a broken __str__ on an argument should not kill logging
            record.msg = "<unformattable log record>"
        record.args = ()

        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = scrub(formatter.formatException(record.exc_info))
            record.exc_info = None
        if getattr(record, "stack_info", None):
            record.stack_info = scrub(record.stack_info)
        return True


_configured = False


def configure(log_path: Path | None = None, *, level: str | None = None) -> None:
    """Install the file handler. Safe to call more than once."""
    global _configured
    if _configured:
        return

    if log_path is None:
        from .paths import log_file

        log_path = log_file()

    resolved = (level or os.environ.get("WATCHTOWER_LOG_LEVEL") or "WARNING").upper()
    if resolved not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        resolved = "WARNING"

    root = logging.getLogger("watchtower")
    root.setLevel(resolved)
    root.propagate = False

    try:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=512_000, backupCount=2, encoding="utf-8", delay=True
        )
    except OSError:
        # No writable app directory. Better to run without a log than not run.
        handler = logging.NullHandler()

    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)

    # httpx logs full request URLs at INFO, and OAuth URLs carry the auth code.
    for noisy in ("httpx", "httpcore", "urllib3", "markdown_it"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"watchtower.{name}")
