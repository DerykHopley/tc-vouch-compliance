"""The control's roll-up and should score, from its requirements' verdicts."""

import pytest

from vouch.graph import RequirementVerdict, ShouldScore, roll_up, should_score

COLUMNS = {"M": "must", "S": "should", "H": "high", "V": "very high", "O": "overlay"}
E, P, N, NA = "evidenced", "partly-evidenced", "not-evidenced", "not-applicable"


def req(rid: str, verdict: str) -> RequirementVerdict:
    return RequirementVerdict(id=rid, column=COLUMNS[rid[0]], text="", verdict=verdict, reason="")


@pytest.mark.parametrize(
    ("requirements", "expected"),
    [
        ([req("M1", E), req("M2", E)], E),
        ([req("M1", N), req("M2", N)], N),
        ([req("M1", E), req("M2", N)], P),
        ([req("M1", P), req("M2", P)], N),
        ([req("M1", P), req("M2", N)], N),
        ([req("M1", E), req("H1", E), req("V1", E), req("O1", E)], E),
        ([req("M1", E), req("H1", N)], P),
        ([req("M1", E), req("O1", N)], P),
        ([req("M1", N), req("V1", E)], P),
        ([req("M1", E), req("S1", N), req("S2", N)], E),
        ([req("M1", N), req("S1", E)], N),
        ([req("M1", E), req("M2", NA)], E),
        ([req("M1", N), req("M2", NA)], N),
        ([req("M1", P), req("M2", E), req("S1", N)], P),
        ([req("M1", NA), req("S1", E)], NA),
    ],
    ids=[
        "all-evidenced",
        "none-evidenced",
        "mixed",
        "all-partly-is-none-evidenced",
        "partly-and-not-is-none-evidenced",
        "high-very-high-overlay-count",
        "high-not-evidenced",
        "overlay-not-evidenced",
        "very-high-evidenced",
        "should-ignored-when-musts-evidenced",
        "should-ignored-when-musts-not",
        "not-applicable-ignored",
        "not-applicable-ignored-none-evidenced",
        "one-evidenced-one-partly",
        "nothing-counted",
    ],
)
def test_the_roll_up_rule(requirements, expected):
    assert roll_up(requirements) == expected


def test_the_should_score_counts_should_requirements_only():
    requirements = [
        req("M1", E),
        req("H1", E),
        req("O1", E),
        req("S1", E),
        req("S2", P),
        req("S3", N),
        req("S4", E),
        req("S5", NA),
    ]

    assert should_score(requirements) == ShouldScore(evidenced=2, total=4)
