"""Redaction on its own (security layer 4): what is removed, what stays, and log lines."""

import io
import logging

import pytest
from pydantic import SecretStr
from uvicorn.logging import AccessFormatter

from vouch.redaction import (
    RedactingFormatter,
    install_log_redaction,
    redact,
    redact_data,
    remember_secrets,
)
from vouch.settings import ClerkKeys, Settings

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyXzEifQ.c2lnbmF0dXJl"


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("key sk-or-v1-abcdefghijklmnop0123", ["a key"]),
        ("key sk_live_51H", ["a key"]),  # a live or test key of any length
        ("key sk_test_x", ["a key"]),
        ("key pk_test_Y2xlcmsuZXhhbXBsZS5jb20k", ["a key"]),
        ("signing secret whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", ["a key"]),
        ("key sk-abc123", ["a key"]),  # an OpenAI-style key under 16 characters (#115)
        ("token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", ["a key"]),  # GitHub classic
        ("token github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnop", ["a key"]),  # fine-grained
        ("aws AKIA" + "IOSFODNN7EXAMPLE", ["a key"]),  # an AWS access key id
        ("Authorization: Bearer abcd", ["a token"]),  # any bearer token
        (f"Authorization: Bearer {JWT}", ["a token"]),
        ("authorization: bearer abcd", ["a token"]),  # the scheme name is case-insensitive
        ("token: bearer abcdefghijklmnop", ["a token"]),  # 16+ characters
        ("token: BEARER a1b2", ["a token"]),  # has a digit
        (f"the session token {JWT} expired", ["a token"]),  # a bare JWT
        ("postgresql://vouch:s3cret@db/vouch", ["a password"]),  # no TLD on the host
        ("redis://default:pa55@cache.internal:6379", ["a password"]),
        ("write to jane.doe@gmail.com", ["an e-mail address"]),
        ("write to jane.doe@nordlicht.example", ["an e-mail address"]),  # not a listed address
    ],
)
def test_redact_removes_secrets_and_personal_data(text, kinds):
    clean, removed = redact(text)

    assert removed == kinds
    assert "[removed]" in clean


def test_a_password_in_a_url_is_removed_and_the_rest_kept():
    assert redact("postgresql://vouch:s3cret@db:5432/vouch") == (
        "postgresql://vouch:[removed]@db:5432/vouch",
        ["a password"],
    )


@pytest.mark.parametrize(
    "text",
    [
        "Report to security@nordlicht.example, the profile's own address.",
        "Report to SECURITY@nordlicht.example.",
        "Reviewed on 2026-11-04 for NK-ENC-01 under ISA 5.2.4, version 2.1.0.",
        "The task-force asks the security hotline on extension 4400.",
        "Disks use AES-256 and keys rotate every 90 days; risk-based, see sk-1.",
        "Bearer of the badge must show it.",
        "Bearer instruments are rare.",  # a prose word after Bearer isn't a token
        "The bearer token is checked offline.",
        "Valid 01/2024-12/2024, see doc 0815/2023, staffed 0800-1700.",
        "Call +49 30 12345678.",  # phone numbers aren't redacted (the owner, #36 review)
        "The sk-learning module and the risk-based sk-12 control stay.",  # no key shape (#115)
        "AKIA is not a key on its own; nor is ghp_ or github_pat_ alone.",
    ],
)
def test_redact_keeps_ordinary_compliance_text(text):
    assert redact(text) == (text, [])


def test_redact_data_reaches_every_string_and_names_each_kind_once():
    data = {
        "answer": "sk_live_abc and jane@gmail.com",
        "sources": [{"heading": "jo@gmail.com"}, {"heading": "fine"}],
        "count": 3,
    }

    clean, removed = redact_data(data)

    assert clean == {
        "answer": "[removed] and [removed]",
        "sources": [{"heading": "[removed]"}, {"heading": "fine"}],
        "count": 3,
    }
    assert removed == ["a key", "an e-mail address"]


def test_redact_data_reaches_strings_inside_tuples():
    """dataclasses.asdict keeps tuples: a requirement's citations reach the stream as one."""
    data = {"citations": ({"section": "Contact jo@gmail.com"},)}

    clean, removed = redact_data(data)

    assert clean == {"citations": [{"section": "Contact [removed]"}]}
    assert removed == ["an e-mail address"]


def test_the_apps_own_secret_values_are_removed_exactly():
    settings = Settings(
        clerk=ClerkKeys(
            secret_key=SecretStr("clerk-secret-0001"),
            publishable_key="pk_test_public",
            jwt_key=SecretStr("-----BEGIN PUBLIC KEY-----MIIBIjAN"),
            authorized_parties=("http://localhost:5173",),
        ),
        openrouter_api_key=SecretStr("or-key-0002"),
        database_url=SecretStr("postgresql://vouch:p%40ss%2Fword@db/vouch"),
    )
    remember_secrets(settings)

    text = (
        "clerk-secret-0001 -----BEGIN PUBLIC KEY-----MIIBIjAN or-key-0002 p@ss/word p%40ss%2Fword"
    )
    clean, removed = redact(text)

    assert clean == " ".join(["[removed]"] * 5)
    assert removed == ["a key"]


def _handler(formatter: logging.Formatter) -> tuple[logging.Handler, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    return handler, stream


def test_uvicorns_access_line_is_formatted_then_redacted():
    """The access formatter reads the record's args: they must reach it as uvicorn made them."""
    handler, stream = _handler(
        RedactingFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    )
    logger = logging.getLogger("test.uvicorn.access")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        args = ("127.0.0.1:5000", "GET", "/api/x?token=sk_live_abc123", "1.1", 200)
        logger.warning('%s - "%s %s HTTP/%s" %d', *args)
    finally:
        logger.removeHandler(handler)

    line = stream.getvalue()
    assert '127.0.0.1:5000 - "GET /api/x?token=[removed] HTTP/1.1" 200' in line
    assert "sk_live_abc123" not in line


def test_an_extra_field_and_an_exception_message_are_redacted_and_the_record_left_alone():
    handler, stream = _handler(RedactingFormatter(logging.Formatter("%(message)s user=%(user)s")))
    logger = logging.getLogger("test.extra")
    logger.addHandler(handler)
    logger.propagate = False
    records: list[logging.LogRecord] = []
    logger.addFilter(lambda record: records.append(record) or True)
    try:
        try:
            raise RuntimeError("leaked whsec_abcdef123")
        except RuntimeError:
            logger.exception("failed for %s", "jane@gmail.com", extra={"user": "sk_test_1"})
    finally:
        logger.removeHandler(handler)

    line = stream.getvalue()
    assert line.startswith("failed for [removed] user=[removed]")
    assert "RuntimeError: leaked [removed]" in line
    for planted in ("jane@gmail.com", "sk_test_1", "whsec_abcdef123"):
        assert planted not in line
    assert records[0].args == ("jane@gmail.com",)  # the record itself isn't rewritten


def test_install_wraps_the_handlers_in_use_once():
    handler, stream = _handler(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install_log_redaction()
        install_log_redaction()
        root.warning("key sk_live_twice")
    finally:
        root.removeHandler(handler)

    assert stream.getvalue() == "key [removed]\n"
    assert isinstance(handler.formatter, RedactingFormatter)
    assert not isinstance(handler.formatter.inner, RedactingFormatter)
    assert isinstance(logging.lastResort.formatter, RedactingFormatter)
