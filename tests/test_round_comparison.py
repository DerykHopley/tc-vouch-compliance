"""Round comparison (#571, feature #554): the lead lists their review rounds and compares now
with any closed one they choose, control by control.

The listing on both stores (ProcessRounds, and PostgresRounds with `scripts/test-db.sh`); the
comparison, remediation done since and questions answered since directly; then the two routes
through the HTTP API on both backends and in both goals.
"""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from reviewing import accepted
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import seeded
from test_memory import DEMO_CONTROL, app_on, assess
from test_plan import ITEM, improve, mark_done, planned_item
from test_questions import answer, log
from test_rounds import AT, CONTROLS, IDS, close, frozen_as, reviewer, start

from vouch import rounds
from vouch.audit import ProcessTrail
from vouch.profile import DEFAULT_PROFILE
from vouch.questions import OpenQuestion
from vouch.remediation import Mark
from vouch.rounds import ListedRound, round_comparison

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

HOUR = timedelta(hours=1)


# --- The listing -------------------------------------------------------------------------------


@pytest.fixture(params=["process", pytest.param("postgres", marks=needs_postgres)])
def store(request) -> rounds.ReviewRounds:
    if request.param == "process":
        kept = ProcessTrail()
        return rounds.ProcessRounds(lambda: kept)
    return rounds.PostgresRounds(request.getfixturevalue("postgres"))


def test_with_no_round_kept_the_listing_is_the_first_round_alone_open(store, users):
    alice, _ = users

    listed = store.listed(alice["X-Test-User"], DEFAULT_PROFILE, None)

    assert listed == [ListedRound(1, "First round", None, None, before_fresh_start=False)]


def test_the_listing_is_every_round_oldest_first_numbered_from_1_the_open_one_last(store, users):
    alice, bob = users
    a = alice["X-Test-User"]
    who = {"reviewer": reviewer(alice), "profile_id": DEFAULT_PROFILE}
    frozen = tuple(frozen_as({IDS[0]: ("accepted", "evidenced")}))
    rounds.close(store, frozen, clock=lambda: AT, **who)
    rounds.start(store, "Q4 2026", clock=lambda: AT + HOUR, **who)
    rounds.close(store, [], clock=lambda: AT + 2 * HOUR, **who)
    rounds.start(store, "Q1 2027", clock=lambda: AT + 3 * HOUR, **who)

    listed = store.listed(a, DEFAULT_PROFILE, AT + HOUR)  # a fresh start between the closes

    assert listed == [
        ListedRound(1, "First round", None, AT, before_fresh_start=True, results=frozen),
        ListedRound(2, "Q4 2026", AT + HOUR, AT + 2 * HOUR, before_fresh_start=False),
        ListedRound(3, "Q1 2027", AT + 3 * HOUR, None, before_fresh_start=False),
    ]
    assert store.listed(bob["X-Test-User"], DEFAULT_PROFILE, None) == [
        ListedRound(1, "First round", None, None, before_fresh_start=False)
    ]


def test_the_default_choice_is_the_last_round_closed_since_the_fresh_start():
    first = ListedRound(1, "First round", None, AT, before_fresh_start=True)
    second = ListedRound(2, "Q4 2026", AT, AT + HOUR, before_fresh_start=False)
    third = ListedRound(3, "Q1 2027", AT + 2 * HOUR, None, before_fresh_start=False)

    assert rounds.default_choice([first, second, third]) == 2
    assert rounds.default_choice([first, replace_open(second)]) is None
    assert rounds.default_choice([ListedRound(1, "First round", None, None, False)]) is None


def replace_open(listed: ListedRound) -> ListedRound:
    return ListedRound(listed.number, listed.name, listed.opened_at, None, False)


# --- The comparison ----------------------------------------------------------------------------


def changes_of(comparison: rounds.RoundComparison) -> dict[str, tuple[Any, ...]]:
    return {
        c["control_id"]: (c["change"], c["before"], c["after"], c["proposed"])
        for c in comparison["controls"]
    }


def test_each_control_is_better_worse_new_or_unchanged_by_its_accepted_verdict():
    a, b, c, d, e, f = IDS[:6]
    frozen = frozen_as(
        {
            a: ("accepted", "partly-evidenced"),  # -> evidenced: better
            b: ("accepted", "evidenced"),  # -> not evidenced: worse
            c: ("accepted", "evidenced"),  # -> rejected now: worse
            d: ("waiting-for-review", "evidenced"),  # not accepted then, accepted now: new
            e: ("accepted", "not-evidenced"),  # same: unchanged
        }
    )
    latest = {
        a: seeded(a, "evidenced", "accepted"),
        b: seeded(b, "not-evidenced", "accepted"),
        c: seeded(c, "evidenced", "rejected"),
        d: seeded(d, "partly-evidenced", "accepted"),
        e: seeded(e, "not-evidenced", "accepted"),
    }

    shown = round_comparison(frozen, CONTROLS, CONTROLS, latest, added=[], closed_at=AT)

    changes = changes_of(shown)
    assert changes[a] == ("better", "partly-evidenced", "evidenced", False)
    assert changes[b] == ("worse", "evidenced", "not-evidenced", False)
    assert changes[c] == ("worse", "evidenced", None, False)
    assert changes[d] == ("new", None, "partly-evidenced", False)
    assert changes[e] == ("unchanged", "not-evidenced", "not-evidenced", False)
    assert changes[f] == ("unchanged", None, None, False)  # not accepted then or now
    assert shown["changes"] == {"better": 1, "worse": 2, "new": 1, "unchanged": len(IDS) - 4}


def test_scope_changes_are_new_since_and_out_of_scope_since_left_out_of_the_counts():
    a, b, c = IDS[:3]
    frozen = [f for f in frozen_as({a: ("accepted", "evidenced")}) if f.control_id != b]
    latest = {
        b: seeded(b, "evidenced", "accepted"),
        a: seeded(a, "evidenced", "accepted"),
    }
    now_applicable = [x for x in CONTROLS if x.id != a]  # a out of scope now, b in scope now

    shown = round_comparison(frozen, CONTROLS, now_applicable, latest, added=[], closed_at=AT)

    changes = changes_of(shown)
    assert changes[a] == ("out-of-scope-since", "evidenced", None, False)
    assert changes[b] == ("new-since", None, "evidenced", False)
    assert changes[c][0] == "unchanged"
    assert shown["changes"] == {"better": 0, "worse": 0, "new": 0, "unchanged": len(IDS) - 2}
    # then counts the round as it closed: a, out of scope now, was accepted
    assert shown["readiness"]["accepted"] == {"now": 1, "then": 1}
    assert shown["readiness"]["by_verdict"]["evidenced"] == {"now": 1, "then": 1}


def test_controls_come_in_the_base_frameworks_order_each_with_its_chapter():
    shown = round_comparison(frozen_as({}), CONTROLS, CONTROLS, {}, added=[], closed_at=AT)

    assert [c["control_id"] for c in shown["controls"]] == IDS
    assert [c["chapter"] for c in shown["controls"]] == [i.split(".")[0] for i in IDS]


def test_a_control_waiting_for_review_is_proposed_and_counted_under_its_earlier_accepted_verdict():
    a, b = IDS[:2]
    frozen = frozen_as({a: ("accepted", "partly-evidenced"), b: ("accepted", "evidenced")})
    latest = {
        a: seeded(a, "evidenced", "waiting-for-review"),  # earlier accepted: partly evidenced
        b: seeded(b, "evidenced", "waiting-for-review"),  # no earlier accepted one
    }
    earlier = {a: seeded(a, "partly-evidenced", "accepted")}

    shown = round_comparison(frozen, CONTROLS, CONTROLS, latest, earlier, added=[], closed_at=AT)

    changes = changes_of(shown)
    assert changes[a] == ("unchanged", "partly-evidenced", "partly-evidenced", True)
    assert changes[b] == ("worse", "evidenced", None, True)
    assert shown["readiness"]["accepted"] == {"now": 1, "then": 2}
    assert shown["readiness"]["by_verdict"]["partly-evidenced"] == {"now": 1, "then": 1}
    assert shown["readiness"]["by_verdict"]["evidenced"] == {"now": 0, "then": 1}


def test_remediation_items_done_since_the_close_in_scope_now_are_listed_with_control_and_when():
    a, b, c = IDS[:3]
    control_of = {f"t-{i}": i for i in (a, b, c)}
    marked = {
        ("t-" + a, "M1"): Mark(None, AT + HOUR, "Done."),
        ("t-" + a, "M2"): Mark(None, AT - HOUR, "Before."),  # done before the close
        ("t-" + b, "M1"): Mark("low", None, None),  # not done
        ("t-" + c, "M1"): Mark(None, AT + HOUR, "Done."),  # c out of scope now
        ("t-other", "M1"): Mark(None, AT + HOUR, "Done."),  # a thread not listed
    }

    done = rounds.done_since(control_of, marked, AT, in_scope={a, b})

    assert done == [
        {"control_id": a, "requirement_id": "M1", "at": AT + HOUR},
    ]


def test_open_questions_answered_since_the_close_in_scope_now_are_listed_with_control_and_when():
    def question(i: int, answered_at: datetime | None) -> OpenQuestion:
        return OpenQuestion(
            i, IDS[i], "Who?", "IT", date(2026, 12, 1), AT - 2 * HOUR,
            None if answered_at is None else "Them.", answered_at,
        )  # fmt: skip

    asked = [question(0, AT + HOUR), question(1, AT - HOUR), question(2, None)]
    asked.append(question(3, AT + HOUR))  # out of scope now
    in_scope = set(IDS[:3])

    assert rounds.answered_since(asked, AT, in_scope) == [
        {"control_id": IDS[0], "question_id": 0, "at": AT + HOUR}
    ]


# --- Through the API -------------------------------------------------------------------------


def listing(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/rounds", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def compared(client: TestClient, headers: dict[str, str], number: int) -> Any:
    return client.get(f"/api/rounds/{number}/comparison", headers=headers)


@pytest.mark.parametrize("goal", ["audit-readiness", "internal-improvement"])
def test_the_rounds_are_listed_with_the_default_and_any_closed_one_compared_with_now(
    database_url, users, goal
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        set_goal = client.put("/api/goal", json={"goal": goal}, headers=alice)
        assert set_goal.status_code == 200, set_goal.text
        none_closed = listing(client, alice)
        close(client, alice)  # First round: nothing accepted
        start(client, alice, "Q4 2026")
        verdict = accepted(client, assess(client, alice), headers=alice)["verdict"]
        close(client, alice)  # Q4 2026: DEMO_CONTROL accepted
        start(client, alice, "Q1 2027")
        listed = listing(client, alice)
        with_first = compared(client, alice, 1)
        with_second = compared(client, alice, 2)

    assert none_closed["default"] is None
    assert [r["number"] for r in none_closed["rounds"]] == [1]
    assert listed["default"] == 2
    assert [
        (r["number"], r["name"], r["closed_at"] is None, r["before_fresh_start"])
        for r in listed["rounds"]
    ] == [
        (1, "First round", False, False),
        (2, "Q4 2026", False, False),
        (3, "Q1 2027", True, False),
    ]
    assert listed["rounds"][0]["opened_at"] is None
    assert with_first.status_code == 200, with_first.text
    first = with_first.json()
    assert first["round"]["number"] == 1
    assert first["changes"]["new"] == 1
    assert first["readiness"]["accepted"] == {"now": 1, "then": 0}
    assert first["readiness"]["by_verdict"][verdict] == {"now": 1, "then": 0}
    demo = next(c for c in first["controls"] if c["control_id"] == DEMO_CONTROL)
    assert demo == {
        "control_id": DEMO_CONTROL,
        "chapter": DEMO_CONTROL.split(".")[0],
        "change": "new",
        "before": None,
        "after": verdict,
        "proposed": False,
    }
    assert with_second.json()["changes"]["new"] == 0


def test_an_unknown_round_is_a_404_the_open_one_and_one_before_the_fresh_start_409s(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        open_first = compared(client, alice, 1)
        unknown = compared(client, alice, 2)
        zero = compared(client, alice, 0)
        close(client, alice)
        start(client, alice, "Q4 2026")
        assert client.post("/api/progress/fresh-start", headers=alice).status_code == 200
        before_fresh = compared(client, alice, 1)
        open_second = compared(client, alice, 2)
        listed = listing(client, alice)

    assert (unknown.status_code, zero.status_code) == (404, 404)
    for refused in (open_first, open_second):
        assert refused.status_code == 409
        assert refused.json()["detail"] == "Close this review round to compare with it."
    assert before_fresh.status_code == 409
    assert before_fresh.json()["detail"] == (
        "This review round closed before the fresh start, so it can't be compared."
    )
    assert [r["before_fresh_start"] for r in listed["rounds"]] == [True, False]
    assert listed["default"] is None


def test_remediation_done_and_questions_answered_since_the_round_closed_are_served(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        asked = log(client, alice, DEMO_CONTROL)
        assert asked.status_code == 200, asked.text
        close(client, alice)
        start(client, alice, "Q4 2026")
        assert mark_done(client, alice, thread_id, note="Fixed.").status_code == 200
        question_id = asked.json()["question"]["id"]
        assert (
            answer(
                client, alice, question_id, {"answer": "The ISMS lead."}, DEMO_CONTROL
            ).status_code
            == 200
        )
        shown = compared(client, alice, 1).json()

    (done,) = shown["remediation_done"]
    assert (done["control_id"], done["requirement_id"]) == (DEMO_CONTROL, ITEM)
    (answered,) = shown["questions_answered"]
    assert answered["question_id"] == question_id
    assert answered["control_id"] == DEMO_CONTROL
    assert datetime.fromisoformat(done["at"]) > datetime.fromisoformat(shown["round"]["closed_at"])


def test_the_overviews_comparison_is_as_it_was(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        close(client, alice)
        start(client, alice, "Q4 2026")
        comparison = client.get("/api/overview", headers=alice).json()["rounds"]["comparison"]

    assert set(comparison) == {"against", "accepted", "by_verdict", "changed"}
