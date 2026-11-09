"""What each protection level brings into scope (#403): `protection_scope()` and the developer
route that sends it to company setup's step 3."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_auth import FakeClerk, bearer, clerk_app, token

from vouch import profile
from vouch.api import create_app
from vouch.isa import BaseFrameworkError, load_base_framework
from vouch.profile import PROTECTION_COLUMNS, protection_scope
from vouch.settings import Settings

PATH = "/api/developer/protection-levels"
LOWER = {"normal": None, "high": "normal", "very high": "high"}


def expected(level: str) -> dict:
    """The level's scope worked out here from the base framework and PROTECTION_COLUMNS."""
    controls = load_base_framework().controls
    columns = PROTECTION_COLUMNS[level]  # type: ignore[index]
    lower = LOWER[level]
    new = columns - (PROTECTION_COLUMNS[lower] if lower else frozenset())  # type: ignore[index]
    counts = {
        column: sum(r.column == column for c in controls for r in c.requirements)
        for column in ("must", "should", "high", "very high")
        if column in columns
    }
    return {
        "level": level,
        "requirements": counts,
        "total": sum(counts.values()),
        "controls": sum(any(r.column in columns for r in c.requirements) for c in controls),
        "added": sum(r.column in new for c in controls for r in c.requirements) if lower else 0,
        "added_controls": [c.id for c in controls if any(r.column in new for r in c.requirements)]
        if lower
        else [],
    }


def test_each_level_brings_in_its_columns_counted_from_the_base_framework():
    levels = protection_scope()

    assert [s.level for s in levels] == ["normal", "high", "very high"]
    for scope in levels:
        want = expected(scope.level)
        assert dict(scope.requirements) == want["requirements"]
        assert list(scope.requirements) == list(want["requirements"])  # the columns in order
        assert scope.total == want["total"]
        assert scope.controls == want["controls"]
        assert scope.added == want["added"]
        assert list(scope.added_controls) == want["added_controls"]


def test_normal_is_85_must_and_should_high_adds_14_and_very_high_adds_10():
    normal, high, very_high = protection_scope()

    assert dict(normal.requirements) == {"must": 37, "should": 48}
    assert (normal.total, normal.added, normal.added_controls) == (85, 0, ())
    assert (high.added, high.total) == (14, 99)
    assert (very_high.added, very_high.total) == (10, 109)
    assert normal.controls == 12


def test_a_developer_gets_the_three_levels_in_order():
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 200
    levels = response.json()["levels"]
    assert [lv["level"] for lv in levels] == ["normal", "high", "very high"]
    assert [lv["total"] for lv in levels] == [85, 99, 109]
    assert levels[1]["added_controls"] == expected("high")["added_controls"]
    assert levels[0]["requirements"] == {"must": 37, "should": 48}


@pytest.fixture
def clerk() -> FakeClerk:
    return FakeClerk({"user_alice": {}, "user_dev": {"role": "developer"}})


def test_a_compliance_lead_gets_403_and_signed_out_401(clerk):
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    lead = client.get(PATH, headers=bearer(token("user_alice")))
    signed_out = client.get(PATH)

    assert lead.status_code == 403
    assert lead.json() == {"detail": "This is available to developers only."}
    assert signed_out.status_code == 401


def test_an_unreadable_base_framework_is_a_500_naming_no_server_path(monkeypatch):
    where = Path("/srv/app/src/vouch/data/vda-isa-2027.1/controls.yaml")

    def unreadable(*args, **kwargs):
        raise BaseFrameworkError(where, "not valid YAML")

    monkeypatch.setattr(profile, "load_base_framework", unreadable)
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 500
    assert "/srv" not in response.text
    assert "controls.yaml" not in response.text
