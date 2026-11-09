"""What each protection level brings into scope (#403): `protection_scope()` and the developer
route that sends it to company setup's step 3."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_auth import FakeClerk, bearer, clerk_app, token

from vouch import profile
from vouch.api import create_app
from vouch.isa import BaseFrameworkError, load_base_framework
from vouch.profile import in_scope, protection_scope
from vouch.settings import Settings

PATH = "/api/developer/protection-levels"
HIGH_ADDS_IN = ("1.6.2", "4.1.2", "4.2.1", "5.1.1", "5.1.2", "5.2.4", "5.2.9")
VERY_HIGH_ADDS_IN = ("1.6.1", "1.6.2", "4.1.2", "4.2.1", "5.1.2", "5.2.4", "5.2.9")


def test_each_level_brings_in_its_columns_counted_from_the_base_framework():
    normal, high, very_high = protection_scope()

    assert [s.level for s in (normal, high, very_high)] == ["normal", "high", "very high"]
    assert list(normal.requirements.items()) == [("must", 37), ("should", 48)]
    assert list(high.requirements.items()) == [("must", 37), ("should", 48), ("high", 14)]
    assert list(very_high.requirements.items()) == [
        ("must", 37),
        ("should", 48),
        ("high", 14),
        ("very high", 10),
    ]
    assert (normal.controls, high.controls, very_high.controls) == (12, 12, 12)


def test_normal_is_85_must_and_should_high_adds_14_and_very_high_adds_10():
    normal, high, very_high = protection_scope()

    assert (normal.total, normal.added, normal.added_controls) == (85, 0, ())
    assert (high.total, high.added, high.added_controls) == (99, 14, HIGH_ADDS_IN)
    assert (very_high.total, very_high.added, very_high.added_controls) == (
        109,
        10,
        VERY_HIGH_ADDS_IN,
    )


def test_the_scope_is_worked_out_once():
    assert protection_scope() is protection_scope()


def test_a_requirement_is_in_scope_at_the_levels_that_bring_its_column_in():
    controls = load_base_framework().controls
    high_one = next(r for c in controls for r in c.requirements if r.column == "high")

    assert [in_scope(high_one, level) for level in ("normal", "high", "very high")] == [
        False,
        True,
        True,
    ]


def test_a_developer_gets_the_three_levels_in_order():
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 200
    levels = response.json()["levels"]
    assert [lv["level"] for lv in levels] == ["normal", "high", "very high"]
    assert [lv["total"] for lv in levels] == [85, 99, 109]
    assert levels[1]["added_controls"] == list(HIGH_ADDS_IN)
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
    protection_scope.cache_clear()  # worked out afresh from the unreadable framework
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 500
    assert "/srv" not in response.text
    assert "controls.yaml" not in response.text
