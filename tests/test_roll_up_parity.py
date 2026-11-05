"""graph.roll_up against the shared cases the working paper's copy (review.ts rollUp) is also
checked against: the two must agree (#42)."""

import json
from pathlib import Path

import pytest

from vouch.graph import RequirementVerdict, roll_up

CASES = json.loads((Path(__file__).parent / "roll_up_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["verdict"])
def test_roll_up_matches_the_shared_cases(case):
    requirements = [
        RequirementVerdict(f"R{n}", column, "A requirement.", verdict, "Why.")
        for n, (column, verdict) in enumerate(case["requirements"])
    ]
    assert roll_up(requirements) == case["verdict"]
