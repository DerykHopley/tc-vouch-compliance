"""The Dossier overview (#40): readiness from accepted verdicts only, the 12 control cards, the
evidence quotes, and the next step; read from the assessments kept per user and profile."""

import pytest
from fastapi.testclient import TestClient
from test_api import proposed_thread

from vouch.api import create_app, load_default_profile
from vouch.citations import Citation
from vouch.dossier import QUOTES_SHOWN, MemoryAssessmentRecords, overview
from vouch.graph import (
    Assessment,
    RemediationItem,
    RequirementVerdict,
    Review,
    ShouldScore,
    Status,
    Verdict,
)
from vouch.isa import load_base_framework
from vouch.profile import load_named_profile
from vouch.settings import Settings

NORDLICHT = load_named_profile("nordlicht")
CONTROLS = load_base_framework().controls
DEMO_CONTROL = "4.1.2"


def quote(doc_id: str, text: str) -> Citation:
    return Citation(doc_id, "1.0", "Scope", text, "documented", False, None)


def seeded(
    control_id: str,
    verdict: Verdict,
    status: Status,
    *,
    citations: tuple[Citation, ...] = (),
    remediation: int = 0,
    profile_id: str = "nordlicht",
) -> Assessment:
    """An assessment as the graph would keep it, without running one."""
    requirement = RequirementVerdict("M1", "must", "Text.", verdict, "Reason.", citations)
    review = None
    if status != "waiting-for-review":
        review = Review("accept" if status == "accepted" else "reject", "", [])
    items = [
        RemediationItem(f"M{i}", "Missing.", "Owner", "2027-01-01") for i in range(remediation)
    ]
    return Assessment(
        control_id=control_id,
        profile_id=profile_id,
        verdict=verdict,
        should_score=ShouldScore(0, 0),
        requirements=[requirement],
        answer="Answer.",
        remediation=items,
        status=status,
        review=review,
    )


def test_with_nothing_assessed_every_control_is_open_and_the_first_is_the_next_step():
    shown = overview(NORDLICHT, CONTROLS, {})

    assert shown["company"] == "Nordlicht Komponenten GmbH"
    assert shown["country"] == "Germany"
    assert shown["readiness"] == {
        "controls": 12,
        "accepted": 0,
        "evidenced": 0,
        "partly_evidenced": 0,
        "not_evidenced": 0,
        "not_applicable": 0,
        "open": 12,
        "remediation_items": 0,
    }
    assert [c["id"] for c in shown["controls"]][:3] == ["1.1.1", "1.6.1", "1.6.2"]
    assert len(shown["controls"]) == 12
    assert {c["status"] for c in shown["controls"]} == {"not-assessed"}
    assert {c["verdict"] for c in shown["controls"]} == {None}
    assert shown["controls"][0]["question"].startswith("To what extent")
    assert shown["quotes"] == []
    assert shown["next_step"] == {"control_id": "1.1.1", "action": "assess"}


def test_readiness_counts_accepted_verdicts_only():
    latest = {
        "1.1.1": seeded("1.1.1", "evidenced", "accepted"),
        "1.6.1": seeded("1.6.1", "evidenced", "accepted"),
        "1.6.2": seeded("1.6.2", "partly-evidenced", "accepted", remediation=2),
        "2.1.3": seeded("2.1.3", "not-evidenced", "accepted", remediation=3),
        "4.1.2": seeded("4.1.2", "evidenced", "waiting-for-review", remediation=5),
        "4.1.3": seeded("4.1.3", "not-evidenced", "rejected", remediation=7),
        "5.1.2": seeded("5.1.2", "not-applicable", "accepted"),
    }

    readiness = overview(NORDLICHT, CONTROLS, latest)["readiness"]

    assert readiness == {
        "controls": 12,
        "accepted": 5,
        "evidenced": 2,
        "partly_evidenced": 1,
        "not_evidenced": 1,
        "not_applicable": 1,
        "open": 7,
        "remediation_items": 5,  # the accepted controls' items; proposed and rejected ones wait
    }


def test_each_card_has_its_review_state_and_verdict():
    latest = {
        "1.1.1": seeded("1.1.1", "evidenced", "accepted"),
        "4.1.2": seeded("4.1.2", "partly-evidenced", "waiting-for-review"),
        "4.1.3": seeded("4.1.3", "not-evidenced", "rejected"),
    }

    cards = {c["id"]: c for c in overview(NORDLICHT, CONTROLS, latest)["controls"]}

    assert (cards["1.1.1"]["status"], cards["1.1.1"]["verdict"]) == ("accepted", "evidenced")
    assert (cards["4.1.2"]["status"], cards["4.1.2"]["verdict"]) == (
        "waiting-for-review",
        "partly-evidenced",
    )
    assert (cards["4.1.3"]["status"], cards["4.1.3"]["verdict"]) == ("rejected", "not-evidenced")
    assert (cards["5.2.9"]["status"], cards["5.2.9"]["verdict"]) == ("not-assessed", None)
    assert cards["4.1.2"]["iso_27001"] == "ISO 27001:2022: A.5.15, A.8.5"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({"4.1.3": "rejected", "4.1.2": "waiting-for-review"}, ("4.1.2", "review")),
        ({"4.1.3": "rejected", "1.1.1": "accepted"}, ("4.1.3", "assess-again")),
        ({"1.1.1": "accepted"}, ("1.6.1", "assess")),
    ],
)
def test_the_next_step_is_a_waiting_review_then_a_rejected_control_then_an_unassessed_one(
    statuses, expected
):
    latest = {cid: seeded(cid, "evidenced", status) for cid, status in statuses.items()}

    step = overview(NORDLICHT, CONTROLS, latest)["next_step"]

    assert (step["control_id"], step["action"]) == expected


def test_with_every_control_accepted_there_is_no_next_step():
    latest = {c.id: seeded(c.id, "evidenced", "accepted") for c in CONTROLS}

    assert overview(NORDLICHT, CONTROLS, latest)["next_step"] is None


def test_quotes_come_from_assessed_controls_in_control_order_each_once_and_capped():
    latest = {
        "4.1.2": seeded(
            "4.1.2",
            "evidenced",
            "waiting-for-review",
            citations=(quote("auth", "Keys for admins."), quote("auth", "Keys for admins.")),
        ),
        "1.1.1": seeded(
            "1.1.1", "evidenced", "accepted", citations=(quote("isms", "We have one."),)
        ),
        "5.2.9": seeded(
            "5.2.9",
            "evidenced",
            "rejected",
            citations=tuple(quote("backup", f"Backup rule {i}.") for i in range(10)),
        ),
    }

    quotes = overview(NORDLICHT, CONTROLS, latest)["quotes"]

    assert len(quotes) == QUOTES_SHOWN
    assert [q["quote"] for q in quotes[:3]] == [
        "We have one.",
        "Keys for admins.",
        "Backup rule 0.",
    ]
    assert quotes[0] == {
        "control_id": "1.1.1",
        "requirement_id": "M1",
        "doc_id": "isms",
        "version": "1.0",
        "section": "Scope",
        "quote": "We have one.",
        "kind": "documented",
        "stale": False,
    }


def test_records_keep_the_latest_assessment_per_user_profile_and_control():
    records = MemoryAssessmentRecords()
    first = seeded(DEMO_CONTROL, "not-evidenced", "rejected")
    again = seeded(DEMO_CONTROL, "evidenced", "waiting-for-review")
    other_profile = seeded("1.1.1", "evidenced", "accepted", profile_id="other")

    records.keep("ann", first)
    records.keep("ann", again)
    records.keep("bob", seeded("1.1.1", "evidenced", "accepted"))
    records.keep("ann", other_profile)

    assert records.latest("ann", "nordlicht") == {DEMO_CONTROL: again}
    assert records.latest("bob", "nordlicht") == {"1.1.1": seeded("1.1.1", "evidenced", "accepted")}
    assert records.latest("ann", "other") == {"1.1.1": other_profile}
    assert records.latest("cat", "nordlicht") == {}


@pytest.fixture
def client():
    """The app in local mode on the bundled demo script, the profile read afresh."""
    load_default_profile.cache_clear()
    yield TestClient(create_app(Settings()), raise_server_exceptions=False)
    load_default_profile.cache_clear()


def card(client: TestClient, control_id: str) -> dict:
    return next(c for c in client.get("/api/overview").json()["controls"] if c["id"] == control_id)


def assess_demo(client: TestClient) -> str:
    """Runs the demo control on the fake model; its thread id."""
    return proposed_thread(client)


def test_the_overview_route_starts_with_nothing_assessed(client):
    response = client.get("/api/overview")

    assert response.status_code == 200
    body = response.json()
    assert len(body["controls"]) == 12
    assert body["readiness"]["accepted"] == 0
    assert body["next_step"] == {"control_id": "1.1.1", "action": "assess"}


def test_an_assessment_shows_as_proposed_and_counts_only_once_accepted(client):
    thread_id = assess_demo(client)

    proposed = client.get("/api/overview").json()
    assert card(client, DEMO_CONTROL) | {"question": ""} == {
        "id": DEMO_CONTROL,
        "question": "",
        "iso_27001": "ISO 27001:2022: A.5.15, A.8.5",
        "status": "waiting-for-review",
        "verdict": "partly-evidenced",
    }
    assert proposed["readiness"]["accepted"] == 0
    assert proposed["readiness"]["partly_evidenced"] == 0
    assert proposed["next_step"] == {"control_id": DEMO_CONTROL, "action": "review"}
    assert {q["control_id"] for q in proposed["quotes"]} == {DEMO_CONTROL}

    review = client.post(f"/api/assessments/{thread_id}/review", json={"decision": "accept"})
    assert review.status_code == 200

    accepted = client.get("/api/overview").json()
    assert card(client, DEMO_CONTROL)["status"] == "accepted"
    assert accepted["readiness"]["accepted"] == 1
    assert accepted["readiness"]["partly_evidenced"] == 1
    assert accepted["readiness"]["open"] == 11
    assert accepted["readiness"]["remediation_items"] == len(
        review.json()["assessment"]["remediation"]
    )


def test_a_rejected_control_is_not_counted_and_is_the_next_step(client):
    thread_id = assess_demo(client)
    client.post(f"/api/assessments/{thread_id}/review", json={"decision": "reject"})

    body = client.get("/api/overview").json()

    assert card(client, DEMO_CONTROL)["status"] == "rejected"
    assert body["readiness"]["accepted"] == 0
    assert body["next_step"] == {"control_id": DEMO_CONTROL, "action": "assess-again"}


def test_each_app_starts_with_its_own_records():
    load_default_profile.cache_clear()
    first = TestClient(create_app(Settings()))
    assess_demo(first)

    second = TestClient(create_app(Settings()))

    assert card(second, DEMO_CONTROL)["status"] == "not-assessed"
