"""If one of these ever fails, assume a token has been written to a log file."""

from __future__ import annotations

import logging

import pytest

from watchtower_tui.logging_setup import RedactingFilter, register_secret, scrub
from watchtower_tui.models import AuthMethod, Credential


@pytest.mark.parametrize(
    "text",
    [
        "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF",
        "sk-proj-0123456789abcdefghij",
        "sess-0123456789abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcdefgh",
        "Authorization: Bearer abcdefghijklmnop",
        '{"access_token": "abcdefghijklmnop"}',
        "refresh_token=abcdefghijklmnop",
        '{"code_verifier": "abcdefghijklmnop"}',
    ],
)
def test_credential_shapes_are_scrubbed(text):
    cleaned = scrub(text)
    assert "[redacted]" in cleaned
    for fragment in ("abcdefghijklmnop", "AAAABBBBCCCCDDDD", "0123456789abcdefghij"):
        assert fragment not in cleaned


def test_registered_secrets_are_scrubbed_anywhere():
    register_secret("a-completely-ordinary-looking-value")
    assert "a-completely-ordinary-looking-value" not in scrub(
        "the value was a-completely-ordinary-looking-value, apparently"
    )


def test_short_values_are_not_registered():
    """Registering 'abc' would redact every occurrence of it in every message."""
    register_secret("abc")
    assert scrub("abc def") == "abc def"


def test_scrub_leaves_ordinary_text_alone():
    text = "refreshed 2 accounts in 431ms"
    assert scrub(text) == text


def test_no_double_bracket_when_patterns_overlap():
    cleaned = scrub('{"access_token": "eyJhbGciOi.eyJzdWIi.sig"}')
    assert "[redacted]]" not in cleaned
    assert cleaned == '{"access_token": "[redacted]"}'


def test_filter_scrubs_message_and_traceback():
    record = logging.LogRecord(
        "watchtower.test",
        logging.ERROR,
        __file__,
        1,
        "token is %s",
        ("sk-ant-abcdefghijklmnop",),
        None,
    )
    assert RedactingFilter().filter(record) is True
    assert "sk-ant-abcdefghijklmnop" not in record.getMessage()


def test_filter_scrubs_exception_text():
    try:
        raise ValueError("failed with sk-ant-abcdefghijklmnop")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "watchtower.test", logging.ERROR, __file__, 1, "boom", (), sys.exc_info()
        )
    RedactingFilter().filter(record)
    assert "sk-ant-abcdefghijklmnop" not in (record.exc_text or "")


def test_credential_repr_hides_everything():
    credential = Credential(
        method=AuthMethod.OAUTH,
        access_token="sk-ant-secret-access-token",
        refresh_token="secret-refresh-token",
    )
    for rendered in (repr(credential), str(credential), f"{credential}", format(credential)):
        assert "secret" not in rendered
        assert rendered == "<Credential oauth valid>"


def test_pkce_verifier_is_not_in_its_repr():
    from watchtower_tui.auth.pkce import new_pkce

    pair = new_pkce()
    assert pair.verifier not in repr(pair)
