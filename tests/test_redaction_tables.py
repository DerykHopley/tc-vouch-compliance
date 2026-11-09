"""Redaction, table-driven (#131): every container redact_data can meet, and every pattern in
vouch.redaction, so a new container type or a new pattern is untested only if someone writes no
row for it, and then this file fails.

Redaction's past gaps were found by review: redact_data skipped tuples (a leak caught at the M1
merge), and the Bearer pattern missed lower and mixed case and mangled prose."""

import itertools
import re

import pytest

from vouch.redaction import EMAIL, KEY, PASSWORD, REDACTIONS, REMOVED, TOKEN, redact, redact_data

SECRET = "sk-or-v1-0123456789abcdefabcdef"

# Each pattern in REDACTIONS, by its regex: secrets it must remove (the text, the part that must
# go, and the label it's removed as), and prose it must leave as written, near misses where it
# can. Each is checked in lower, upper and mixed case too, but the key and JWT shapes, which are
# lower case by their issuers' design (LOWER_ONLY): their upper case is pinned as left alone.
CASES: dict[str, tuple[list[tuple[str, str, str]], list[str]]] = {
    r"\b([A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)@": (
        [("postgresql://vouch:s3cretPass@db.example/vouch", "s3cretPass", PASSWORD)],
        ["See https://example.com/path for the policy."],
    ),
    r"(?i)\b(authorization:\s*bearer)\s+[\w.~+/=-]{4,}": (
        [("Authorization: Bearer abcdef", "abcdef", TOKEN)],
        ["Authorization: bearer of the risk is the owner."],
    ),
    r"(?i)\b(bearer)\s+(?:(?=[\w.~+/=-]*\d)[\w.~+/=-]{4,}|[\w.~+/=-]{16,})": (
        [("token: Bearer abc123def456", "abc123def456", TOKEN)],
        ["Bearer instruments are out of scope for this control."],
    ),
    r"\beyJ[\w-]+\.[\w-]+\.[\w-]+": (
        [
            (
                "jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl here",
                "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
                TOKEN,
            )
        ],
        ["The field eyJob.title is not a token."],
    ),
    (
        r"(?<![A-Za-z0-9])(?:"
        r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_]*"
        r"|(?:sk|pk)[-_][A-Za-z0-9_-]{16,}"
        r"|sk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{6,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}"
        r"|whsec_[A-Za-z0-9+/=_-]+"
        r")"
    ): (
        [
            ("key sk_live_abc123", "sk_live_abc123", KEY),
            (f"key {SECRET}", SECRET, KEY),
            ("key sk-abc123 here", "sk-abc123", KEY),  # #115
            ("token ghp_a1B2c3D4e5F6g7H8i9J0k1L2", "ghp_a1B2c3D4e5F6g7H8i9J0k1L2", KEY),
            ("pat github_pat_11ABCDEFG0123456789_ab", "github_pat_11ABCDEFG0123456789_ab", KEY),
            ("hook whsec_abcDEF123=", "whsec_abcDEF123=", KEY),
        ],
        [
            "The policy covers the risk-assessment-for-desk-work process.",
            "The sk-learning module and control sk-12 stay; so does ghp_short.",
        ],
    ),
    r"(?i)(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])": (
        [("aws AKIAIOSFODNN7EXAMPLE now", "AKIAIOSFODNN7EXAMPLE", KEY)],  # #115
        ["AKIA alone is no key, nor is AKIAIOSFODNN7 or AKIAIOSFODNN7EXAMPLE2X."],
    ),
    r"[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}": (
        [("Write to anna.schmidt@example.com today.", "anna.schmidt@example.com", EMAIL)],
        ["Email security@nordlicht.example for reports.", "Meet at 10 @ the office."],
    ),
}


LOWER_ONLY = {p for p in CASES if p.startswith((r"\beyJ", r"(?<![A-Za-z0-9])"))}
FLAGS = {r.pattern.pattern: r.pattern.flags for r in REDACTIONS}


def cased(text: str, pattern: str) -> list[str]:
    """`text`, and but for a LOWER_ONLY pattern its lower, upper and mixed-case forms too."""
    if pattern in LOWER_ONLY:
        return [text]
    mixed = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))
    return [text, text.lower(), text.upper(), mixed]


def test_only_the_key_and_jwt_shapes_are_lower_case_only_and_neither_is_case_insensitive():
    assert len(LOWER_ONLY) == 2
    assert not any(FLAGS[p] & re.IGNORECASE for p in LOWER_ONLY)


@pytest.mark.parametrize(
    "text", [text.upper() for p in sorted(LOWER_ONLY) for text, _, _ in CASES[p][0]]
)
def test_a_lower_case_only_shape_in_upper_case_is_left_alone(text):
    assert redact(text) == (text, [])


def test_every_pattern_has_a_case_row():
    patterns = {r.pattern.pattern for r in REDACTIONS}
    assert patterns - set(CASES) == set(), "a pattern in vouch.redaction has no row in CASES"
    assert set(CASES) - patterns == set(), "a row in CASES names no pattern any more"


@pytest.mark.parametrize(
    ("text", "at", "length", "label"),
    [
        pytest.param(variant, at, len(secret), label, id=f"{label}:{variant}")
        for p, (secrets, _) in CASES.items()
        for text, secret, label in secrets
        for at in [text.index(secret)]
        for variant in cased(text, p)
    ],
)
def test_each_pattern_removes_its_secret_in_every_case(text, at, length, label):
    secret = text[at : at + length]  # the secret as this variant writes it
    clean, labels = redact(text)
    assert secret not in clean
    assert REMOVED in clean
    assert labels == [label]  # removed by its own pattern, not another one by chance


@pytest.mark.parametrize(
    "prose",
    [variant for p, (_, prose) in CASES.items() for t in prose for variant in cased(t, p)],
)
def test_each_pattern_leaves_its_prose_alone(prose):
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


# #298: a set or frozenset holds only hashable things, strings among them, so it gets its own rows;
# it comes back a list (as JSON has it), in no set order.
@pytest.mark.parametrize("kind", [set, frozenset])
def test_no_secret_survives_a_set_and_it_comes_back_a_list(kind):
    clean, labels = redact_data(kind({f"key {SECRET}", "plain"}))

    assert isinstance(clean, list)
    assert sorted(clean) == sorted([f"key {REMOVED}", "plain"])
    assert labels == ["a key"]


@pytest.mark.parametrize("kind", [set, frozenset])
def test_a_set_inside_a_dict_or_list_is_reached_too(kind):
    clean, labels = redact_data({"items": [kind({f"key {SECRET}"})]})

    assert clean == {"items": [[f"key {REMOVED}"]]}
    assert labels == ["a key"]


def test_no_secret_survives_in_a_dict_key():
    clean, labels = redact_data({f"key {SECRET}": "value", "plain": {f"key {SECRET}": 1}})

    assert clean == {f"key {REMOVED}": "value", "plain": {f"key {REMOVED}": 1}}
    assert SECRET not in repr(clean)
    assert labels == ["a key"]
