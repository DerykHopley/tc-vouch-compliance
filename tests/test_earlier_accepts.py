"""Controls accepted before the three-step review (#373, story 3 of #350): an assessment whose
stored `Review` was made before the three-step review reads `before_three_step_review` true, and
still counts as accepted on the overview and in the report; reading it rewrites nothing.

Through the HTTP API on the bundled fake model, in memory: such a review is written into the
thread's checkpoint as it was kept before #372 (a `Review` without `three_step`, reviewing's
accept_as_before_three_steps). The fake model's 4.1.2 run judges M1 partly evidenced, so it
drafts one item, for M1.
"""

from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from reviewing import accept_as_before_three_steps, accepted, reviewed, thread_graph
from test_memory import DEMO_CONTROL, app_on, assess

users = test_memory.users


def accepted_before(client: TestClient, user: dict[str, str]) -> str:
    """A 4.1.2 assessment accepted before the three-step review: its thread."""
    thread_id = assess(client, user)
    accept_as_before_three_steps(user, thread_id)
    return thread_id


def opened(client: TestClient, user: dict[str, str], thread_id: str) -> dict[str, Any]:
    response = client.get(f"/api/assessments/{thread_id}", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["assessment"]


def test_a_review_kept_before_the_three_steps_reads_before_three_step_review(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        thread_id = accepted_before(client, alice)
        shown = opened(client, alice, thread_id)

    assert shown["status"] == "accepted"
    assert shown["before_three_step_review"] is True


@pytest.mark.parametrize("state", ["accepted", "rejected-at-confirm", "waiting"])
def test_an_assessment_of_the_three_steps_or_waiting_reads_false(users, state):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        thread_id = assess(client, alice)
        if state == "accepted":
            answered = accepted(client, thread_id, headers=alice)
        elif state == "rejected-at-confirm":
            answered = reviewed(client, thread_id, "reject", headers=alice)
        shown = opened(client, alice, thread_id)

    assert shown["before_three_step_review"] is False
    if state != "waiting":  # the step's own answer carries it too
        assert answered["before_three_step_review"] is False


def test_the_stream_result_carries_it_false(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.post("/api/assessments", json={"control_id": DEMO_CONTROL}, headers=alice)
    _, data = test_memory.read_events(response)[-1]
    assert data["assessment"]["before_three_step_review"] is False


def test_it_counts_as_accepted_on_the_overview_and_in_the_report_and_reading_writes_nothing(
    users,
):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        thread_id = accepted_before(client, alice)
        built, config = thread_graph(alice, thread_id)

        def kept() -> tuple[Any, ...]:
            state = built.get_state(config)
            trail = client.get("/api/audit-trail", headers=alice).json()["entries"]
            return (
                state.config["configurable"]["checkpoint_id"],
                len(list(built.get_state_history(config))),
                trail,
                opened(client, alice, thread_id)["remediation"],
            )

        before = kept()
        overview = client.get("/api/overview", headers=alice).json()
        report = client.get("/api/report", headers=alice).json()
        progress = client.get("/api/assessments", headers=alice).json()["assessments"]
        after = kept()

    assert overview["readiness"]["accepted"] == 1
    assert overview["readiness"]["by_verdict"]["partly-evidenced"] == 1
    assert report["readiness"]["accepted"] == 1
    (control,) = [c for c in report["controls"] if c["id"] == DEMO_CONTROL]
    assert (control["status"], control["verdict"]) == ("accepted", "partly-evidenced")
    assert [(a["thread_id"], a["status"]) for a in progress] == [(thread_id, "accepted")]
    assert after == before
    assert [(i["requirement_id"], i["status"]) for i in after[3]] == [("M1", "draft")]
