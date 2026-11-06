"""Redaction, table-driven (#131): every container redact_data can meet, and every pattern in
vouch.redaction, so a new container type or a new pattern is untested only if someone writes no
row for it, and then this file fails.

Redaction's past gaps were found by review: redact_data skipped tuples (a leak caught at the M1
merge), and the Bearer pattern missed lower and mixed case and mangled prose."""

import itertools

import pytest

from vouch.redaction import REDACTIONS, REMOVED, redact, redact_data

SECRET = "sk-or-v1-0123456789abcdefabcdef"

# Each pattern in REDACTIONS, by its regex: secrets it must remove (the part that must go), and
# prose it must leave as written. A case-insensitive pattern is also checked in lower, upper and
# mixed case.
CASES: dict[str, tuple[list[tuple[str, str]], list[str]]] = {
    r"\b([A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)@": (
        [("postgresql://vouch:s3cretPass@db.example/vouch", "s3cretPass")],
        ["See https://example.com/path for the policy."],
    ),
    r"(?i)\b(authorization:\s*bearer)\s+[\w.~+/=-]{4,}": (
        [("Authorization: Bearer abcdef", "abcdef")],
        ["The authorization of bearer bonds is a finance matter."],
    ),
    r"(?i)\b(bearer)\s+(?:(?=[\w.~+/=-]*\d)[\w.~+/=-]{4,}|[\w.~+/=-]{16,})": (
        [("token: Bearer abc123def456", "abc123def456")],
        ["Bearer instruments are out of scope for this control."],
    ),
    r"\beyJ[\w-]+\.[\w-]+\.[\w-]+": (
        [("jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl here", "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl")],
        ["The key register lists each key's owner."],
    ),
    (
        r"(?<![A-Za-z0-9])(?:"
        r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_]*"
        r"|(?:sk|pk)[-_][A-Za-z0-9_-]{16,}"
        r"|whsec_[A-Za-z0-9+/=_-]+"
        r")"
    ): (
        [
            ("key sk_live_abc123", "sk_live_abc123"),
            (f"key {SECRET}", SECRET),
            ("hook whsec_abcDEF123=", "whsec_abcDEF123="),
        ],
        ["The task-based desk policy (risk-based) applies."],
    ),
    r"[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}": (
        [("Write to anna.schmidt@example.com today.", "anna.schmidt@example.com")],
        ["Email security@nordlicht.example for reports.", "Meet at 10 @ the office."],
    ),
}


def cased(text: str, pattern: str) -> list[str]:
    """`text`, and for a case-insensitive pattern its lower, upper and mixed-case forms too."""
    if not pattern.startswith("(?i)"):
        return [text]
    mixed = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))
    return [text, text.lower(), text.upper(), mixed]


def test_every_pattern_has_a_case_row():
    patterns = {r.pattern.pattern for r in REDACTIONS}
    assert patterns - set(CASES) == set(), "a pattern in vouch.redaction has no row in CASES"
    assert set(CASES) - patterns == set(), "a row in CASES names no pattern any more"


@pytest.mark.parametrize(
    ("pattern", "text", "at", "length"),
    [
        (p, variant, at, len(secret))
        for p, (secrets, _) in CASES.items()
        for text, secret in secrets
        for at in [text.index(secret)]
        for variant in cased(text, p)
    ],
)
def test_each_pattern_removes_its_secret_in_every_case(pattern, text, at, length):
    secret = text[at : at + length]  # the secret as this variant writes it
    clean, labels = redact(text)
    assert secret not in clean
    assert REMOVED in clean
    assert labels


@pytest.mark.parametrize(
    ("pattern", "prose"),
    [(p, variant) for p, (_, prose) in CASES.items() for t in prose for variant in cased(t, p)],
)
def test_each_pattern_leaves_its_prose_alone(pattern, prose):
    assert redact(prose) == (prose, [])


# Containers redact_data meets, each built around a value: JSON's dict and list, and the tuple
# dataclasses.asdict keeps (the M1 leak). A tuple comes back a list, as JSON has it.
CONTAINERS = {
    "dict": (lambda v: {"k": v}, lambda v: {"k": v}),
    "list": (lambda v: [v, "plain"], lambda v: [v, "plain"]),
    "tuple": (lambda v: (v, "plain"), lambda v: [v, "plain"]),
}


@pytest.mark.parametrize(
    "path",
    [(a,) for a in CONTAINERS] + list(itertools.product(CONTAINERS, repeat=2)),
    ids=lambda path: "-in-".join(reversed(path)),
)
def test_no_secret_survives_any_container_and_the_shape_is_kept(path):
    value, expected = f"key {SECRET}", f"key {REMOVED}"
    for name in path:
        build, shape = CONTAINERS[name]
        value, expected = build(value), shape(expected)

    clean, labels = redact_data(value)

    assert clean == expected
    assert SECRET not in repr(clean)
    assert labels == ["a key"]
