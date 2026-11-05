"""The Dossier overview (#40): readiness from accepted verdicts only, the 12 control cards, the
evidence quotes, and the next step; read from each user's assessments under the profile (#38's
memory and checkpointer). The API cases run in memory, and on Postgres where CI's database job
gives a server (VOUCH_TEST_DATABASE_URL; otherwise those are skipped)."""

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on, assess, restart

from vouch import memory
from vouch.citations import Citation
from vouch.dossier import QUOTES_SHOWN, overview
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

NORDLICHT = load_named_profile("nordlicht")
CONTROLS = load_base_framework().controls
DEMO_CONTROL = "4.1.2"

# #38's fixtures: each API case in memory and on Postgres, a new Postgres database, two new users.
database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users


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
        "by_verdict": {
            "evidenced": 0,
            "partly-evidenced": 0,
            "not-evidenced": 0,
            "not-applicable": 0,
        },
        "open": 12,
        "remediation_items": 0,
    }
    assert shown["by_status"] == {
        "not-assessed": 12,
        "waiting-for-review": 0,
        "accepted": 0,
        "rejected": 0,
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
        "by_verdict": {
            "evidenced": 2,
            "partly-evidenced": 1,
            "not-evidenced": 1,
            "not-applicable": 1,
        },
        "open": 7,
        "remediation_items": 5,  # the accepted controls' items; proposed and rejected ones wait
    }


def test_the_status_counts_cover_every_listed_control():
    latest = {
        "1.1.1": seeded("1.1.1", "evidenced", "accepted"),
        "4.1.2": seeded("4.1.2", "evidenced", "waiting-for-review"),
        "4.2.1": seeded("4.2.1", "evidenced", "waiting-for-review"),
        "4.1.3": seeded("4.1.3", "not-evidenced", "rejected"),
    }

    assert overview(NORDLICHT, CONTROLS, latest)["by_status"] == {
        "not-assessed": 8,
        "waiting-for-review": 2,
        "accepted": 1,
        "rejected": 1,
    }


def test_an_accepted_control_the_list_leaves_out_is_not_counted():
    listed = CONTROLS[:2]
    latest = {c.id: seeded(c.id, "evidenced", "accepted") for c in CONTROLS[:3]}

    shown = overview(NORDLICHT, listed, latest)

    assert shown["readiness"]["controls"] == 2
    assert shown["readiness"]["accepted"] == 2
    assert shown["readiness"]["by_verdict"]["evidenced"] == 2
    assert shown["readiness"]["open"] == 0
    assert shown["by_status"]["accepted"] == 2


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


def test_quotes_leave_out_rejected_controls():
    latest = {
        "1.1.1": seeded("1.1.1", "evidenced", "rejected", citations=(quote("isms", "Gone."),)),
        "4.1.2": seeded(
            "4.1.2", "evidenced", "waiting-for-review", citations=(quote("auth", "Kept."),)
        ),
    }

    quotes = overview(NORDLICHT, CONTROLS, latest)["quotes"]

    assert [q["quote"] for q in quotes] == ["Kept."]


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
            "accepted",
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


# --- Through the API, on #38's memory: in memory, and on Postgres in CI's database job ---------


def overview_of(client: TestClient, user: dict[str, str]) -> dict:
    response = client.get("/api/overview", headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def card(body: dict, control_id: str) -> dict:
    return next(c for c in body["controls"] if c["id"] == control_id)


def review(client: TestClient, user: dict[str, str], thread_id: str, decision: str) -> dict:
    response = client.post(
        f"/api/assessments/{thread_id}/review", json={"decision": decision}, headers=user
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_overview_starts_with_nothing_assessed(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        body = overview_of(client, alice)

    assert len(body["controls"]) == 12
    assert body["readiness"]["accepted"] == 0
    assert body["next_step"] == {"control_id": "1.1.1", "action": "assess"}


def test_an_assessment_shows_as_proposed_and_counts_only_once_accepted(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)

        proposed = overview_of(client, alice)
        assert card(proposed, DEMO_CONTROL) | {"question": ""} == {
            "id": DEMO_CONTROL,
            "question": "",
            "iso_27001": "ISO 27001:2022: A.5.15, A.8.5",
            "status": "waiting-for-review",
            "verdict": "partly-evidenced",
        }
        assert proposed["readiness"]["accepted"] == 0
        assert proposed["readiness"]["by_verdict"]["partly-evidenced"] == 0
        assert proposed["next_step"] == {"control_id": DEMO_CONTROL, "action": "review"}
        assert {q["control_id"] for q in proposed["quotes"]} == {DEMO_CONTROL}

        reviewed = review(client, alice, thread_id, "accept")

        accepted = overview_of(client, alice)
    assert card(accepted, DEMO_CONTROL)["status"] == "accepted"
    assert accepted["readiness"]["accepted"] == 1
    assert accepted["readiness"]["by_verdict"]["partly-evidenced"] == 1
    assert accepted["readiness"]["open"] == 11
    assert accepted["readiness"]["remediation_items"] == len(reviewed["assessment"]["remediation"])


def test_a_rejected_control_is_not_counted_and_is_the_next_step(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        review(client, alice, assess(client, alice), "reject")
        body = overview_of(client, alice)

    assert card(body, DEMO_CONTROL)["status"] == "rejected"
    assert body["readiness"]["accepted"] == 0
    assert body["next_step"] == {"control_id": DEMO_CONTROL, "action": "assess-again"}


def test_the_newest_assessment_of_a_control_is_the_one_shown(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        review(client, alice, assess(client, alice), "accept")
        assess(client, alice)  # assessed again: proposed, so no longer counted
        body = overview_of(client, alice)

    assert card(body, DEMO_CONTROL)["status"] == "waiting-for-review"
    assert body["readiness"]["accepted"] == 0


def test_each_user_sees_only_their_own_overview(database_url, users):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        review(client, alice, assess(client, alice), "accept")
        bobs = overview_of(client, bob)

    assert card(bobs, DEMO_CONTROL)["status"] == "not-assessed"
    assert bobs["readiness"]["accepted"] == 0


@needs_postgres
def test_the_overview_outlasts_a_restart_on_postgres(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        review(client, alice, assess(client, alice), "accept")

    restart()
    with TestClient(app_on(postgres.url)) as client:
        body = overview_of(client, alice)

    assert card(body, DEMO_CONTROL)["status"] == "accepted"
    assert body["readiness"]["accepted"] == 1


def test_the_overview_reads_no_more_than_each_controls_newest_assessment(
    database_url, users, monkeypatch
):
    alice, _ = users

    def not_read(self):
        raise AssertionError("the overview read when the user last worked")

    monkeypatch.setattr(memory.Memory, "last_worked", not_read)
    with TestClient(app_on(database_url)) as client:
        assess(client, alice)
        body = overview_of(client, alice)

    assert card(body, DEMO_CONTROL)["status"] == "waiting-for-review"
