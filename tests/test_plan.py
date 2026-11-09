"""The improvement plan (#410, spec #350 stories 9 and 10): each remediation item's priority (the
lead's, else the default from its requirement's verdict), an approved item marked done with a
note, a re-assessment suggested once every approved item is done, and a control Closed once that
re-assessment is accepted with no gap. All of it in the Internal improvement goal only.

The unit cases build assessments as the graph keeps them; the API cases run on the bundled fake
model, in memory and on Postgres (test_memory's fixtures). The fake model's 4.1.2 run judges M1
partly evidenced and the rest evidenced, so it drafts one item, for M1."""

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from reviewing import OWNER, confirm
from test_audit import _audit_table_shape
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import CONTROLS, NORDLICHT, seeded
from test_memory import app_on, assess
from test_report import built_report
from test_report_pdf import text

from vouch import goal, report, report_pdf, rounds
from vouch.audit import AuditEntry, Reviewer
from vouch.database import (
    AUDIT_TABLE,
    CREATE_AUDIT_GUARD,
    CREATE_AUDIT_TRIGGERS,
    PLAN_TABLE,
    SETUP_TABLES,
    Goal,
)
from vouch.dossier import overview
from vouch.graph import (
    Assessment,
    Decision,
    RemediationItem,
    RequirementVerdict,
    Review,
    ShouldScore,
    Verdict,
)
from vouch.plan import CardFlags, card_flags
from vouch.profile import DEFAULT_PROFILE
from vouch.remediation import DecidedItem, Key, Mark, shown_items

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

T0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def assessment(
    thread_id: str,
    verdicts: dict[str, Verdict],
    *,
    status: str = "accepted",
    reviewed_at: datetime | None = T0,
    control_id: str = "4.1.2",
) -> Assessment:
    """An assessment of the control as the graph keeps it after Confirm findings: one item per
    requirement not or partly evidenced."""
    requirements = [
        RequirementVerdict(rid, "must", "Text.", verdict, "Reason.")
        for rid, verdict in verdicts.items()
    ]
    items = [
        RemediationItem(r.id, f"{r.id} missing.", "CISO", "2027-01-01")
        for r in requirements
        if r.verdict in ("not-evidenced", "partly-evidenced")
    ]
    review = None
    if status != "waiting-for-review":
        decision: Decision = "accept" if status == "accepted" else "reject"
        at = reviewed_at.isoformat() if reviewed_at else ""
        review = Review(decision, "", [], "alice", "Alice", at, three_step=True)
    return Assessment(
        control_id=control_id,
        profile_id="nordlicht",
        verdict="partly-evidenced",
        should_score=ShouldScore(0, 0),
        requirements=requirements,
        answer="Answer.",
        remediation=items,
        thread_id=thread_id,
        status=status,  # type: ignore[arg-type]
        review=review,
        confirmed=[],
    )


def approved(thread_id: str, requirement_id: str, status: str = "approved") -> DecidedItem:
    return DecidedItem(
        thread_id,
        requirement_id,
        status,  # type: ignore[arg-type]
        f"{requirement_id} missing.",
        "CISO",
        datetime(2027, 1, 1).date(),
        T0,
    )


# --- Priority (story 9) ----------------------------------------------------------------------


def test_each_item_has_its_default_priority_from_its_requirements_verdict():
    """An override at Confirm findings (not to partly evidenced) is in the requirements already."""
    shown = shown_items(
        assessment("t1", {"M1": "not-evidenced", "M2": "partly-evidenced", "M3": "evidenced"}), {}
    )

    assert [(i["requirement_id"], i["priority"], i["done"]) for i in shown] == [
        ("M1", "high", None),
        ("M2", "medium", None),
    ]


def test_an_approved_item_shows_the_leads_priority_and_its_done_mark():
    a = assessment("t1", {"M1": "not-evidenced", "M2": "partly-evidenced"})
    decided = {("t1", "M1"): approved("t1", "M1"), ("t1", "M2"): approved("t1", "M2")}
    done_at = T0 + timedelta(days=1)
    marks = {
        ("t1", "M1"): Mark(priority="low", done_at=None, done_note=None),
        ("t1", "M2"): Mark(priority=None, done_at=done_at, done_note="MFA rolled out."),
    }

    shown = shown_items(a, decided, marks)

    assert [(i["status"], i["priority"], i["done"]) for i in shown] == [
        ("approved", "low", None),
        ("approved", "medium", {"at": done_at.isoformat(), "note": "MFA rolled out."}),
    ]


def test_a_discarded_item_has_no_priority_and_is_never_done():
    a = assessment("t1", {"M1": "not-evidenced"})
    decided = {("t1", "M1"): approved("t1", "M1", "discarded")}
    marks = {("t1", "M1"): Mark(priority="low", done_at=T0, done_note="Stray.")}

    (shown,) = shown_items(a, decided, marks)

    assert (shown["status"], shown["priority"], shown["done"]) == ("discarded", None, None)


# --- Re-assessment suggested and Closed (story 10) -------------------------------------------

NEITHER = (False, False)


def done(at: datetime, note: str = "Fixed.") -> Mark:
    return Mark(priority=None, done_at=at, done_note=note)


def planned(thread_id: str = "p", reviewed_at: datetime = T0) -> Assessment:
    """The earlier assessment: accepted with a gap in M1 and M2, both items approved."""
    return assessment(
        thread_id, {"M1": "not-evidenced", "M2": "partly-evidenced"}, reviewed_at=reviewed_at
    )


def plan_of(thread_id: str = "p") -> dict[Key, DecidedItem]:
    return {(thread_id, r): approved(thread_id, r) for r in ("M1", "M2")}


def all_done(thread_id: str = "p", at: datetime = T0 + timedelta(days=1)) -> dict[Key, Mark]:
    return {(thread_id, r): done(at) for r in ("M1", "M2")}


def flags(
    history: list[Assessment],
    decided: dict[Key, DecidedItem],
    marks: dict[Key, Mark],
    goal: Goal = "internal-improvement",
    control_id: str = "4.1.2",
) -> tuple[bool, bool]:
    """The control's (reassess_suggested, closed)."""
    shown = card_flags(history, decided, marks, goal).get(control_id)
    return (shown.reassess_suggested, shown.closed) if shown else NEITHER


def test_a_reassessment_is_suggested_once_every_approved_item_is_done():
    one_left = {("p", "M1"): done(T0 + timedelta(days=1))}

    assert flags([planned()], plan_of(), all_done()) == (True, False)
    assert flags([planned()], plan_of(), one_left) == NEITHER


def test_a_discarded_item_needs_no_done_mark():
    decided = plan_of() | {("p", "M2"): approved("p", "M2", "discarded")}
    marks = {("p", "M1"): done(T0 + timedelta(days=1))}

    assert flags([planned()], decided, marks) == (True, False)


def test_no_reassessment_is_suggested_without_an_approved_item_or_while_waiting():
    waiting = assessment("p", {"M1": "not-evidenced"}, status="waiting-for-review")
    discarded = {("p", r): approved("p", r, "discarded") for r in ("M1", "M2")}

    assert flags([planned()], discarded, {}) == NEITHER
    assert flags([waiting], plan_of(), all_done()) == NEITHER


def test_an_accepted_reassessment_with_no_gap_after_every_done_mark_is_closed():
    reassessed = assessment(
        "n", {"M1": "evidenced", "M2": "evidenced"}, reviewed_at=T0 + timedelta(days=2)
    )

    assert flags([reassessed, planned()], plan_of(), all_done()) == (False, True)


def test_a_reassessment_with_a_gap_is_not_closed():
    reassessed = assessment(
        "n", {"M1": "evidenced", "M2": "partly-evidenced"}, reviewed_at=T0 + timedelta(days=2)
    )

    assert flags([reassessed, planned()], plan_of(), all_done()) == NEITHER


def test_an_evidenced_control_with_no_earlier_plan_is_not_closed():
    evidenced = assessment("n", {"M1": "evidenced"}, reviewed_at=T0 + timedelta(days=2))
    earlier_without_plan = assessment("p", {"M1": "evidenced"})

    assert flags([evidenced, earlier_without_plan], {}, {}) == NEITHER


def test_a_reassessment_is_not_closed_while_an_earlier_approved_item_is_not_done():
    reassessed = assessment("n", {"M1": "evidenced"}, reviewed_at=T0 + timedelta(days=2))
    one_done = {("p", "M1"): done(T0 + timedelta(days=1))}

    assert flags([reassessed, planned()], plan_of(), one_done) == NEITHER


def test_a_reassessment_accepted_before_the_last_done_mark_is_not_closed():
    reassessed = assessment("n", {"M1": "evidenced"}, reviewed_at=T0 + timedelta(hours=12))

    assert flags([reassessed, planned()], plan_of(), all_done()) == NEITHER


def test_a_waiting_reassessment_is_not_closed():
    waiting = assessment("n", {"M1": "evidenced"}, status="waiting-for-review")

    assert flags([waiting, planned()], plan_of(), all_done()) == NEITHER


def test_nothing_is_suggested_or_closed_under_audit_readiness():
    reassessed = assessment("n", {"M1": "evidenced"}, reviewed_at=T0 + timedelta(days=2))

    assert flags([planned()], plan_of(), all_done(), "audit-readiness") == NEITHER
    assert flags([reassessed, planned()], plan_of(), all_done(), "audit-readiness") == NEITHER


def test_each_control_is_judged_on_its_own_assessments():
    other = assessment(
        "o", {"M1": "evidenced"}, control_id="1.1.1", reviewed_at=T0 + timedelta(days=2)
    )

    assert flags([other, planned()], plan_of(), all_done(), control_id="1.1.1") == NEITHER
    assert flags([other, planned()], plan_of(), all_done()) == (True, False)


def test_a_closed_control_still_counts_as_accepted():
    """Readiness, the status counts, the next step and the round's frozen results ignore it."""
    latest = {"4.1.2": seeded("4.1.2", "evidenced", "accepted")}
    closed = {"4.1.2": CardFlags(reassess_suggested=False, closed=True)}

    plain = overview(NORDLICHT, CONTROLS, latest)
    marked = overview(NORDLICHT, CONTROLS, latest, flags=closed)

    card = next(c for c in marked["controls"] if c["id"] == "4.1.2")
    assert (card["status"], card["closed"]) == ("accepted", True)
    for part in ("readiness", "by_status", "next_step"):
        assert marked[part] == plain[part]  # type: ignore[literal-required]
    assert rounds.freeze(CONTROLS, latest) == rounds.freeze(CONTROLS, latest)  # no flags taken


# --- Through the API: priority (story 9) ------------------------------------------------------

M1_NOT_EVIDENCED = [{"requirement_id": "M1", "verdict": "not-evidenced", "reason": "No record."}]
ITEM = "M1"


def improve(client: TestClient, user: dict[str, str]) -> None:
    response = client.put("/api/goal", json={"goal": "internal-improvement"}, headers=user)
    assert response.status_code == 200, response.text


def at_plan_fixes(client: TestClient, user: dict[str, str]) -> str:
    """4.1.2 assessed, its findings confirmed with M1 overridden to not evidenced (its default
    priority high): its item a draft."""
    thread_id = assess(client, user)
    response = confirm(client, thread_id, M1_NOT_EVIDENCED, headers=user)
    assert response.status_code == 200, response.text
    return thread_id


def decide(client: TestClient, user: dict[str, str], thread_id: str, how: str = "approve") -> None:
    body = {"decision": how} | ({"owner_role": OWNER} if how == "approve" else {})
    response = client.post(
        f"/api/assessments/{thread_id}/remediation/{ITEM}", json=body, headers=user
    )
    assert response.status_code == 200, response.text


def review(client: TestClient, user: dict[str, str], thread_id: str, decision: str) -> None:
    response = client.post(
        f"/api/assessments/{thread_id}/review", json={"decision": decision}, headers=user
    )
    assert response.status_code == 200, response.text


def planned_item(
    client: TestClient, user: dict[str, str], *, how: str = "approve", then: str | None = "accept"
) -> str:
    """4.1.2 with its M1 item decided `how`, then accepted (or rejected, or left waiting)."""
    thread_id = at_plan_fixes(client, user)
    decide(client, user, thread_id, how)
    if then:
        review(client, user, thread_id, then)
    return thread_id


def set_priority(client: TestClient, user: dict[str, str], thread_id: str, **body: Any) -> Any:
    return client.put(
        f"/api/assessments/{thread_id}/remediation/{ITEM}/priority", json=body, headers=user
    )


def mark_done(client: TestClient, user: dict[str, str], thread_id: str, **body: Any) -> Any:
    return client.post(
        f"/api/assessments/{thread_id}/remediation/{ITEM}/done", json=body, headers=user
    )


def shown_item(client: TestClient, user: dict[str, str], thread_id: str) -> dict[str, Any]:
    response = client.get(f"/api/assessments/{thread_id}", headers=user)
    assert response.status_code == 200, response.text
    (item,) = response.json()["assessment"]["remediation"]
    return item


def trail(client: TestClient, user: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def actions(client: TestClient, user: dict[str, str], *wanted: str) -> list[dict[str, Any]]:
    return [e for e in trail(client, user) if e["action"] in wanted]


def card_of(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/overview", headers=user)
    assert response.status_code == 200, response.text
    return next(c for c in response.json()["controls"] if c["id"] == "4.1.2")


def test_the_lead_lowers_an_approved_items_priority_with_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        before = shown_item(client, alice, thread_id)["priority"]
        changed = set_priority(client, alice, thread_id, priority="low")
        again = set_priority(client, alice, thread_id, priority="low")
        shown = shown_item(client, alice, thread_id)
        entries = actions(client, alice, "priority")

    assert before == "high"
    assert changed.status_code == 200, changed.text
    assert changed.json()["item"]["priority"] == "low"
    assert again.status_code == 200
    assert again.json() == changed.json()
    assert (shown["priority"], shown["status"], shown["done"]) == ("low", "approved", None)
    (entry,) = entries
    assert (entry["thread_id"], entry["requirement_id"], entry["control_id"]) == (
        thread_id,
        ITEM,
        "4.1.2",
    )
    assert entry["note"] == "Priority: low (was high)."
    assert (entry["verdict_before"], entry["verdict_after"]) == ("not-evidenced", "not-evidenced")


def test_the_priority_can_be_set_while_the_assessment_waits_at_accept(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice, then=None)
        changed = set_priority(client, alice, thread_id, priority="medium")

    assert changed.status_code == 200, changed.text
    assert changed.json()["item"]["priority"] == "medium"


@pytest.mark.parametrize(
    ("case", "detail"),
    [
        ("audit-readiness", "The improvement plan is part of the Internal improvement goal."),
        ("draft", "Approve this remediation item first."),
        ("discarded", "Approve this remediation item first."),
        ("done", "This remediation item is done already."),
        ("rejected", "This control's verdict was rejected"),
    ],
)
def test_a_priority_is_refused_with_a_409_writing_nothing(database_url, users, case, detail):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        if case != "audit-readiness":
            improve(client, alice)
        if case == "draft":
            thread_id = at_plan_fixes(client, alice)
        elif case == "discarded":
            thread_id = planned_item(client, alice, how="discard")
        elif case == "rejected":
            thread_id = planned_item(client, alice, then="reject")
        else:
            thread_id = planned_item(client, alice)
        if case == "done":
            assert mark_done(client, alice, thread_id, note="Record made.").status_code == 200
        before = trail(client, alice)
        refused = set_priority(client, alice, thread_id, priority="low")
        after = trail(client, alice)

    assert refused.status_code == 409
    assert refused.json()["detail"].startswith(detail)
    assert after == before


@pytest.mark.parametrize("route", ["priority", "done"])
def test_another_users_thread_or_a_requirement_with_no_item_is_a_404(database_url, users, route):
    alice, bob = users
    body = {"priority": "low"} if route == "priority" else {"note": "Done."}
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        improve(client, bob)
        thread_id = planned_item(client, alice)
        send = client.put if route == "priority" else client.post
        bobs = send(
            f"/api/assessments/{thread_id}/remediation/{ITEM}/{route}", json=body, headers=bob
        )
        no_item = send(
            f"/api/assessments/{thread_id}/remediation/S1/{route}", json=body, headers=alice
        )

    assert (bobs.status_code, no_item.status_code) == (404, 404)


@pytest.mark.parametrize("body", [{"priority": "urgent"}, None])
def test_any_other_priority_or_no_body_is_a_422(database_url, users, body):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        url = f"/api/assessments/{thread_id}/remediation/{ITEM}/priority"
        refused = client.put(url, json=body, headers=alice)
        entries = actions(client, alice, "priority")

    assert refused.status_code == 422
    assert entries == []


# --- Through the API: done (story 10) ---------------------------------------------------------


def test_the_lead_marks_an_approved_item_done_with_a_note_and_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        marked = mark_done(client, alice, thread_id, note="  Exposure record kept.  ")
        again = mark_done(client, alice, thread_id, note="Exposure record kept.")
        other = mark_done(client, alice, thread_id, note="Something else.")
        shown = shown_item(client, alice, thread_id)
        entries = actions(client, alice, "done")
        no_priority = set_priority(client, alice, thread_id, priority="low")

    assert marked.status_code == 200, marked.text
    done = marked.json()["item"]["done"]
    assert done["note"] == "Exposure record kept."
    assert datetime.fromisoformat(done["at"]).tzinfo is not None
    assert again.status_code == 200
    assert again.json() == marked.json()
    assert other.status_code == 409
    assert other.json()["detail"] == (
        "This remediation item was marked done already: Exposure record kept."
    )
    assert shown["done"] == done
    (entry,) = entries
    assert (entry["thread_id"], entry["requirement_id"], entry["note"]) == (
        thread_id,
        ITEM,
        "Exposure record kept.",
    )
    assert no_priority.status_code == 409


@pytest.mark.parametrize(
    ("case", "detail"),
    [
        ("audit-readiness", "The improvement plan is part of the Internal improvement goal."),
        ("draft", "Accept the control before marking its remediation items done."),
        ("discarded", "Approve this remediation item first."),
        ("waiting", "Accept the control before marking its remediation items done."),
    ],
)
def test_a_done_mark_is_refused_with_a_409_writing_nothing(database_url, users, case, detail):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        if case != "audit-readiness":
            improve(client, alice)
        if case == "draft":
            thread_id = at_plan_fixes(client, alice)
        elif case == "discarded":
            thread_id = planned_item(client, alice, how="discard")
        elif case == "waiting":
            thread_id = planned_item(client, alice, then=None)
        else:
            thread_id = planned_item(client, alice)
        before = trail(client, alice)
        refused = mark_done(client, alice, thread_id, note="Record made.")
        after = trail(client, alice)

    assert refused.status_code == 409
    assert refused.json()["detail"] == detail
    assert after == before


@pytest.mark.parametrize("body", [{"note": ""}, {"note": "   "}, {"note": "x" * 2001}, None])
def test_a_blank_longer_or_missing_note_is_a_422(database_url, users, body):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        url = f"/api/assessments/{thread_id}/remediation/{ITEM}/done"
        refused = client.post(url, json=body, headers=alice)
        entries = actions(client, alice, "done")

    assert refused.status_code == 422
    assert entries == []


def test_a_note_of_2000_characters_is_kept(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        marked = mark_done(client, alice, thread_id, note="x" * 2000)

    assert marked.status_code == 200, marked.text


# --- The cards: re-assessment suggested, Closed ------------------------------------------------


def test_the_card_suggests_a_reassessment_then_reads_closed_after_one_with_no_gap(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        planned_card = card_of(client, alice)
        mark_done(client, alice, thread_id, note="Exposure record kept.")
        suggested = card_of(client, alice)
        again = assess(client, alice)
        waiting = card_of(client, alice)
        no_gap = [{"requirement_id": "M1", "verdict": "evidenced", "reason": "Record found."}]
        assert confirm(client, again, no_gap, headers=alice).status_code == 200
        review(client, alice, again, "accept")
        closed = card_of(client, alice)
        client.put("/api/goal", json={"goal": "audit-readiness"}, headers=alice)
        under_readiness = card_of(client, alice)

    def flagged(card: dict[str, Any]) -> tuple[str, bool, bool]:
        return card["status"], card["reassess_suggested"], card["closed"]

    assert flagged(planned_card) == ("accepted", False, False)
    assert flagged(suggested) == ("accepted", True, False)
    assert flagged(waiting) == ("waiting-for-review", False, False)
    assert flagged(closed) == ("accepted", False, True)
    assert flagged(under_readiness) == ("accepted", False, False)


def test_a_reassessment_accepted_with_a_gap_is_not_closed(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        mark_done(client, alice, thread_id, note="Exposure record kept.")
        planned_item(client, alice)  # M1 still not evidenced: a new plan
        shown = card_of(client, alice)

    assert (shown["reassess_suggested"], shown["closed"]) == (False, False)


def test_an_earlier_plan_from_before_the_fresh_start_closes_nothing(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        mark_done(client, alice, thread_id, note="Exposure record kept.")
        assert client.post("/api/progress/fresh-start", headers=alice).status_code == 200
        again = assess(client, alice)
        no_gap = [{"requirement_id": "M1", "verdict": "evidenced", "reason": "Record found."}]
        confirm(client, again, no_gap, headers=alice)
        review(client, alice, again, "accept")
        shown = card_of(client, alice)

    assert (shown["status"], shown["closed"]) == ("accepted", False)


# --- Postgres: one transaction, turns on the lock, before setup --------------------------------


@needs_postgres
@pytest.mark.parametrize("route", ["priority", "done"])
def test_a_failing_trail_insert_is_a_503_and_leaves_the_item_as_it_was(postgres, users, route):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        with postgres.connection() as conn:  # the entry's insert breaks a constraint
            conn.execute(
                f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT no_plan "
                "CHECK (action NOT IN ('priority', 'done')) NOT VALID"
            )
        if route == "priority":
            failed = set_priority(client, alice, thread_id, priority="low")
        else:
            failed = mark_done(client, alice, thread_id, note="Record made.")
        shown = shown_item(client, alice, thread_id)

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert (shown["priority"], shown["done"]) == ("high", None)


@needs_postgres
def test_two_done_marks_at_once_write_one_entry(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        go = threading.Barrier(2)
        codes: list[int] = []

        def marking() -> None:
            go.wait()
            codes.append(mark_done(client, alice, thread_id, note="Record made.").status_code)

        threads = [threading.Thread(target=marking) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = actions(client, alice, "done")

    assert codes == [200, 200]
    assert len(entries) == 1


@needs_postgres
def test_before_setup_made_the_plan_table_items_read_their_defaults_and_a_mark_is_a_503(
    postgres, users, caplog
):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        with postgres.connection() as conn:  # a deploy that adds the table, before setup runs
            conn.execute(f"DROP TABLE {PLAN_TABLE}")
        shown = shown_item(client, alice, thread_id)
        card = card_of(client, alice)
        failed = mark_done(client, alice, thread_id, note="Record made.")

    assert (shown["priority"], shown["done"]) == ("high", None)
    assert card["reassess_suggested"] is False
    assert failed.status_code == 503
    assert f"no {PLAN_TABLE} table" in caplog.text


def test_setup_makes_the_plan_table_and_health_checks_it():
    assert PLAN_TABLE in SETUP_TABLES


CREATE_AUDIT_BEFORE_410 = (
    f"CREATE TABLE {AUDIT_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, reviewer_name text NOT NULL, at timestamptz NOT NULL, "
    "profile_id text NOT NULL, control_id text NOT NULL, thread_id text NOT NULL, "
    "action text NOT NULL "
    "CHECK (action IN ('accept', 'reject', 'override', 'confirm', 'approve', 'discard', 'goal', "
    "'round-start', 'round-close')), "
    "requirement_id text, note text NOT NULL, verdict_before text, verdict_after text, "
    "evidence jsonb NOT NULL, model text NOT NULL, prompt_version text NOT NULL, "
    "goal_before text, goal_after text, round_name text)"
)
"""The trail as #402's setup made it."""


@needs_postgres
def test_setup_on_a_database_made_before_410_makes_the_plan_and_keeps_the_trail(request, users):
    alice, _ = users
    db = request.getfixturevalue("empty_database")  # not set up: made as #402's setup made it
    with db.connection() as conn:
        conn.execute(CREATE_AUDIT_BEFORE_410)
        conn.execute(CREATE_AUDIT_GUARD)
        for trigger in CREATE_AUDIT_TRIGGERS:
            conn.execute(trigger)
        conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_id, reviewer_name, at, profile_id, control_id, "
            "thread_id, action, note, evidence, model, prompt_version, round_name) "
            "VALUES (%s, 'Dana', now() - interval '1 day', %s, '', '', 'round-close', '', '[]', "
            "'', '', 'First round')",
            (alice["X-Test-User"], DEFAULT_PROFILE),
        )

    db.setup()
    shape = _audit_table_shape(db)
    db.setup()  # safe to run again: nothing changes
    assert _audit_table_shape(db) == shape
    with TestClient(app_on(db.url)) as client:
        improve(client, alice)
        thread_id = planned_item(client, alice)
        lowered = set_priority(client, alice, thread_id, priority="low")
        marked = mark_done(client, alice, thread_id, note="Record made.")
        entries = trail(client, alice)

    assert (lowered.status_code, marked.status_code) == (200, 200)
    assert [e["action"] for e in entries][:2] == ["done", "priority"]
    assert entries[-1]["action"] == "round-close"  # the old row kept as it was


# --- The report's trail ------------------------------------------------------------------------

PLAN_WORDS = {  # newest first, as the trail lists them: the done mark, then the priority
    "en": ("Marked done", "Priority changed"),
    "de": ("Als erledigt markiert", "Priorität geändert"),
}


def plan_entries() -> list[AuditEntry]:
    base = goal.goal_entry(
        Reviewer("u", "Dana Lead"), "nordlicht", "audit-readiness", "internal-improvement", T0
    )
    common: dict[str, Any] = {"control_id": "4.1.2", "thread_id": "t", "requirement_id": "M1"}
    common |= {"goal_before": None, "goal_after": None}
    return [
        replace(base, id=2, action="done", note="Record made.", **common),
        replace(base, id=1, action="priority", note="Priority: low (was high).", **common),
    ]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_plans_trail_rows_say_their_action_in_the_reports_words(language):
    built = built_report(entries=plan_entries())

    rows = report.trail_rows(built["trail"]["entries"], report.words_for(language))

    assert [row[4] for row in rows] == list(PLAN_WORDS[language])
    assert [row[7] for row in rows] == ["Record made.", "Priority: low (was high)."]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_pdf_builds_with_the_plans_entries(language):
    built = built_report(entries=plan_entries())

    read = text(report_pdf.pdf(built, report.words_for(language)))

    for word in PLAN_WORDS[language]:
        assert word in read
