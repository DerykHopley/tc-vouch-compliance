"""Open questions on a control (#414, spec #350 stories 11 and 12): the lead logs a question (what,
for whom, due when) on any control of the profile and records its answer, final; a control whose
newest assessment is accepted has a re-assessment suggested once a question on it is answered
after that acceptance, or falls overdue. In both goals.

The unit cases build assessments as the graph keeps them (test_plan's `assessment`); the API
cases run on the bundled fake model, in memory and on Postgres (test_memory's fixtures)."""

import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import test_memory
from fastapi.testclient import TestClient
from reviewing import accepted
from test_audit import _audit_table_shape
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import CONTROLS, NORDLICHT, seeded
from test_memory import app_on, assess
from test_plan import T0, all_done, assessment, plan_of, planned
from test_report import built_report
from test_report_pdf import text

from vouch import goal, report, report_pdf, rounds
from vouch.audit import AuditEntry, Reviewer
from vouch.database import (
    AUDIT_TABLE,
    CREATE_AUDIT_GUARD,
    CREATE_AUDIT_TRIGGERS,
    QUESTIONS_TABLE,
    SETUP_TABLES,
)
from vouch.dossier import overview
from vouch.plan import CardFlags, card_flags, with_reasons
from vouch.profile import DEFAULT_PROFILE
from vouch.questions import OpenQuestion, is_overdue, reasons, since

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

DUE = date(2026, 11, 20)


def question(
    *,
    control_id: str = "4.1.2",
    due: date = DUE,
    asked_at: datetime = T0,
    answered_at: datetime | None = None,
) -> OpenQuestion:
    return OpenQuestion(
        id=1,
        control_id=control_id,
        question="Is the exposure record reviewed?",
        asked_of="IT lead",
        due_date=due,
        asked_at=asked_at,
        answer="Yes, each quarter." if answered_at else None,
        answered_at=answered_at,
    )


# --- Overdue, as of an injected clock ---------------------------------------------------------


def test_a_question_is_overdue_from_the_start_in_utc_of_the_day_after_its_due_date():
    unanswered = question()
    on_the_due_date = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)
    the_day_after = datetime(2026, 11, 21, 0, 30, tzinfo=UTC)

    assert is_overdue(unanswered, on_the_due_date) is False
    assert is_overdue(unanswered, the_day_after) is True
    assert is_overdue(question(answered_at=T0), the_day_after) is False


def test_overdue_is_judged_in_utc_whatever_the_clocks_zone():
    berlin_after_midnight = datetime(2026, 11, 21, 0, 30, tzinfo=UTC).astimezone(
        ZoneInfo("Europe/Berlin")
    )  # 01:30 in Berlin, 00:30 UTC
    still_the_due_date = datetime(2026, 11, 20, 23, 30, tzinfo=UTC).astimezone(
        ZoneInfo("Asia/Tokyo")
    )  # the 21st in Tokyo, the 20th in UTC

    assert is_overdue(question(), berlin_after_midnight) is True
    assert is_overdue(question(), still_the_due_date) is False


# --- The reasons (story 12) -------------------------------------------------------------------

LATER = T0 + timedelta(days=1)
LONG_AFTER = datetime(2026, 12, 1, tzinfo=UTC)


def latest(*runs: Any) -> dict[str, Any]:
    return {run.control_id: run for run in runs}


def test_an_answer_after_the_acceptance_suggests_a_reassessment_in_both_goals():
    accepted = assessment("a", {"M1": "evidenced"})
    answered = question(answered_at=LATER)

    found = reasons(latest(accepted), [answered], LATER)

    assert found == {"4.1.2": ["question-answered"]}
    for goal_ in ("audit-readiness", "internal-improvement"):
        flags = with_reasons(card_flags([accepted], {}, {}, goal_), found)
        assert flags["4.1.2"] == CardFlags(True, False, ("question-answered",))


def test_a_reassessment_accepted_after_the_answer_clears_it():
    again = assessment("b", {"M1": "evidenced"}, reviewed_at=LATER + timedelta(hours=1))

    assert reasons(latest(again), [question(answered_at=LATER)], LATER) == {}


def test_an_overdue_question_suggests_a_reassessment_when_accepted_before_it_fell_overdue():
    before = assessment("a", {"M1": "evidenced"}, reviewed_at=T0)
    after = assessment("b", {"M1": "evidenced"}, reviewed_at=datetime(2026, 11, 21, 8, tzinfo=UTC))
    clock = datetime(2026, 11, 21, 0, 30, tzinfo=UTC)

    assert reasons(latest(before), [question()], clock) == {"4.1.2": ["question-overdue"]}
    assert reasons(latest(after), [question()], LONG_AFTER) == {}
    assert reasons(latest(before), [question()], datetime(2026, 11, 20, 23, 30, tzinfo=UTC)) == {}


@pytest.mark.parametrize("status", ["waiting-for-review", "rejected"])
def test_no_question_reason_while_the_newest_assessment_waits_or_was_rejected(status):
    newest = assessment("b", {"M1": "evidenced"}, status=status, reviewed_at=LATER)
    asked = [question(answered_at=LATER + timedelta(hours=1)), replace(question(), id=2)]

    assert reasons(latest(newest), asked, LONG_AFTER) == {}


def test_no_question_reason_on_a_control_not_assessed():
    asked = [question(answered_at=LATER), replace(question(), id=2)]

    assert reasons({}, asked, LONG_AFTER) == {}


def test_both_question_reasons_are_listed_once_each_in_order():
    accepted = assessment("a", {"M1": "evidenced"})
    asked = [
        replace(question(), id=1),
        question(answered_at=LATER),
        replace(question(answered_at=LATER + timedelta(hours=1)), id=3),
        replace(question(), id=4),
    ]

    found = reasons(latest(accepted), asked, LONG_AFTER)

    assert found == {"4.1.2": ["question-answered", "question-overdue"]}


def test_fixes_done_and_an_answer_read_both_reasons_in_order():
    """Under Internal improvement (test_plan's plan: both items approved and done)."""
    fixes = card_flags([planned()], plan_of(), all_done(), "internal-improvement")
    answered = question(answered_at=LATER + timedelta(days=1))

    flags = with_reasons(fixes, reasons(latest(planned()), [answered], LONG_AFTER))

    assert flags["4.1.2"].reasons == ("fixes-done", "question-answered")
    assert flags["4.1.2"].reassess_suggested is True


def test_a_closed_control_with_an_answered_question_is_closed_and_suggested():
    reassessed = assessment(
        "n", {"M1": "evidenced", "M2": "evidenced"}, reviewed_at=T0 + timedelta(days=2)
    )
    closed = card_flags([reassessed, planned()], plan_of(), all_done(), "internal-improvement")
    answered = question(answered_at=T0 + timedelta(days=3))

    flags = with_reasons(closed, reasons(latest(reassessed), [answered], LONG_AFTER))

    assert flags["4.1.2"] == CardFlags(True, True, ("question-answered",))


def test_a_flag_without_a_reason_is_refused():
    with pytest.raises(ValueError):
        CardFlags(reassess_suggested=True, closed=False)


def test_the_question_reasons_change_no_status_readiness_or_next_step():
    """Nor the round's frozen results, which take no flags."""
    newest = {"4.1.2": seeded("4.1.2", "evidenced", "accepted")}
    flags = {"4.1.2": CardFlags(True, False, ("question-answered", "question-overdue"))}

    plain = overview(NORDLICHT, CONTROLS, newest)
    marked = overview(NORDLICHT, CONTROLS, newest, flags=flags)

    card = next(c for c in marked["controls"] if c["id"] == "4.1.2")
    assert card["reassess_suggested"] is True
    assert card["reassess_reasons"] == ["question-answered", "question-overdue"]
    assert card["status"] == "accepted"
    for part in ("readiness", "by_status", "next_step"):
        assert marked[part] == plain[part]  # type: ignore[literal-required]
    then = rounds.freeze(CONTROLS, newest)
    assert rounds.compare(then, CONTROLS, newest) == rounds.compare(then, CONTROLS, newest)


def test_a_question_asked_before_the_fresh_start_is_left_out():
    old = question(asked_at=T0)
    new = replace(question(asked_at=LATER), id=2)

    assert since([old, new], T0 + timedelta(hours=1)) == [new]
    assert since([old, new], None) == [old, new]


# --- Through the API: logging (story 11) ------------------------------------------------------

CONTROL = "1.1.1"
ASKED = "Who reviews the ISMS scope each year?"


def in_days(days: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def log(client: TestClient, user: dict[str, str], control_id: str = CONTROL, **body: Any) -> Any:
    sent = {"question": ASKED, "asked_of": "IT lead", "due_date": in_days(14)} | body
    return client.post(f"/api/controls/{control_id}/questions", json=sent, headers=user)


def answer(
    client: TestClient,
    user: dict[str, str],
    question_id: int,
    body: Any,
    control_id: str = CONTROL,
) -> Any:
    return client.post(
        f"/api/controls/{control_id}/questions/{question_id}/answer", json=body, headers=user
    )


def trail(client: TestClient, user: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def actions(client: TestClient, user: dict[str, str], *wanted: str) -> list[dict[str, Any]]:
    return [e for e in trail(client, user) if e["action"] in wanted]


def overview_of(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/overview", headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def card_of(client: TestClient, user: dict[str, str], control_id: str) -> dict[str, Any]:
    return next(c for c in overview_of(client, user)["controls"] if c["id"] == control_id)


def test_the_lead_logs_a_question_on_a_control_not_assessed_with_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        logged = log(client, alice)
        entries = actions(client, alice, "question")
        listed = overview_of(client, alice)["questions"]

    assert logged.status_code == 200, logged.text
    kept = logged.json()["question"]
    assert kept["id"] >= 1
    assert (kept["question"], kept["asked_of"], kept["due_date"]) == (ASKED, "IT lead", in_days(14))
    assert (kept["answer"], kept["answered_at"], kept["overdue"]) == (None, None, False)
    assert datetime.fromisoformat(kept["asked_at"]).tzinfo is not None
    (entry,) = entries
    assert (entry["control_id"], entry["thread_id"], entry["requirement_id"]) == (CONTROL, "", None)
    assert entry["note"] == f"{ASKED} (for IT lead, due {in_days(14)})"
    assert listed == {CONTROL: [kept]}


def test_questions_are_listed_oldest_first_by_control(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        first = log(client, alice, question="First?").json()["question"]
        other = log(client, alice, control_id="4.1.2", question="Elsewhere?").json()["question"]
        second = log(client, alice, question="Second?").json()["question"]
        listed = overview_of(client, alice)["questions"]

    assert listed == {CONTROL: [first, second], "4.1.2": [other]}


def test_each_user_and_profile_lists_only_their_own_questions(database_url, users):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        log(client, alice)
        bobs = overview_of(client, bob)["questions"]
        alpenwerk = client.get(
            "/api/overview", headers=alice | {"X-Vouch-Profile": "alpenwerk"}
        ).json()["questions"]

    assert (bobs, alpenwerk) == ({}, {})


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"question": ""}, None),
        ({"question": "   "}, None),
        ({"question": "x" * 1001}, None),
        ({"question": "Who?\x00"}, None),
        ({"asked_of": ""}, None),
        ({"asked_of": "  "}, None),
        ({"asked_of": "x" * 201}, None),
        ({"asked_of": "IT\x00lead"}, None),
        ({"due_date": "not-a-date"}, None),
        ({"due_date": "2020-01-01"}, "The due date 2020-01-01 has passed."),
        ({"due_date": in_days(400)}, "is more than 365 days away"),
        ({"question": None}, None),
    ],
)
def test_a_question_is_refused_with_a_422_writing_nothing(database_url, users, body, detail):
    alice, _ = users
    sent = {k: v for k, v in body.items() if v is not None}
    with TestClient(app_on(database_url)) as client:
        if body == {"question": None}:  # a missing field
            refused = client.post(
                f"/api/controls/{CONTROL}/questions",
                json={"asked_of": "IT lead", "due_date": in_days(14)},
                headers=alice,
            )
        else:
            refused = log(client, alice, **sent)
        entries = actions(client, alice, "question")
        listed = overview_of(client, alice)["questions"]

    assert refused.status_code == 422, refused.text
    if detail:
        assert detail in refused.json()["detail"]
    assert (entries, listed) == ([], {})


def test_no_body_is_a_422(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        refused = client.post(f"/api/controls/{CONTROL}/questions", headers=alice)

    assert refused.status_code == 422


def test_a_question_of_1000_characters_is_kept(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        logged = log(client, alice, question="x" * 1000, due_date=in_days(0))

    assert logged.status_code == 200, logged.text


def test_an_unknown_control_is_a_404_writing_nothing(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        refused = log(client, alice, control_id="9.9.9")
        entries = actions(client, alice, "question")

    assert refused.status_code == 404
    assert entries == []


# --- Through the API: answering (story 11) ----------------------------------------------------


def test_the_lead_records_an_answer_once_with_one_entry(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        asked = log(client, alice).json()["question"]
        answered = answer(client, alice, asked["id"], {"answer": "  The CISO, each March.  "})
        again = answer(client, alice, asked["id"], {"answer": "The CISO, each March."})
        other = answer(client, alice, asked["id"], {"answer": "Nobody."})
        entries = actions(client, alice, "answer")
        listed = overview_of(client, alice)["questions"][CONTROL]

    assert answered.status_code == 200, answered.text
    kept = answered.json()["question"]
    assert kept["answer"] == "The CISO, each March."
    assert datetime.fromisoformat(kept["answered_at"]).tzinfo is not None
    assert (kept["id"], kept["overdue"]) == (asked["id"], False)
    assert again.status_code == 200
    assert again.json() == answered.json()
    assert other.status_code == 409
    assert other.json()["detail"] == "This question was answered already: The CISO, each March."
    (entry,) = entries
    assert (entry["control_id"], entry["thread_id"], entry["note"]) == (
        CONTROL,
        "",
        "The CISO, each March.",
    )
    assert listed == [kept]


def test_a_refused_answer_names_the_kept_answer_redacted(database_url, users):
    """The 409's detail is redacted as every answer is: the kept answer as the 200 showed it."""
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        asked = log(client, alice).json()["question"]
        kept = answer(client, alice, asked["id"], {"answer": "Ask jane.doe@example.com"})
        other = answer(client, alice, asked["id"], {"answer": "Nobody."})

    shown = kept.json()["question"]["answer"]
    assert "jane.doe@example.com" not in shown
    assert other.status_code == 409
    assert other.json()["detail"] == f"This question was answered already: {shown}"


def test_an_answer_of_2000_characters_is_kept(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        asked = log(client, alice).json()["question"]
        answered = answer(client, alice, asked["id"], {"answer": "x" * 2000})

    assert answered.status_code == 200, answered.text


@pytest.mark.parametrize("case", ["unknown", "another user's", "another profile's", "elsewhere"])
def test_answering_a_question_not_the_users_on_that_control_is_a_404(database_url, users, case):
    alice, bob = users
    with TestClient(app_on(database_url)) as client:
        asked = log(client, alice).json()["question"]
        if case == "unknown":
            refused = answer(client, alice, asked["id"] + 1000, {"answer": "Yes."})
        elif case == "another user's":
            refused = answer(client, bob, asked["id"], {"answer": "Yes."})
        elif case == "another profile's":
            refused = answer(
                client, alice | {"X-Vouch-Profile": "alpenwerk"}, asked["id"], {"answer": "Yes."}
            )
        else:
            refused = answer(client, alice, asked["id"], {"answer": "Yes."}, control_id="4.1.2")
        entries = actions(client, alice, "answer")
        kept = overview_of(client, alice)["questions"][CONTROL]

    assert refused.status_code == 404
    assert entries == []
    assert kept == [asked]


@pytest.mark.parametrize("body", [{"answer": ""}, {"answer": "   "}, {"answer": "x" * 2001}, None])
def test_a_blank_longer_or_missing_answer_is_a_422(database_url, users, body):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        asked = log(client, alice).json()["question"]
        refused = answer(client, alice, asked["id"], body)
        entries = actions(client, alice, "answer")

    assert refused.status_code == 422
    assert entries == []


def test_a_question_asked_before_the_fresh_start_isnt_listed_and_suggests_nothing(
    database_url, users
):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        thread_id = assess(client, alice)
        accepted(client, thread_id, headers=alice)
        asked = log(client, alice, control_id="4.1.2").json()["question"]
        answer(client, alice, asked["id"], {"answer": "Yes."}, control_id="4.1.2")
        assert client.post("/api/progress/fresh-start", headers=alice).status_code == 200
        again = assess(client, alice)
        accepted(client, again, headers=alice)
        body = overview_of(client, alice)
        entries = actions(client, alice, "question", "answer")

    card = next(c for c in body["controls"] if c["id"] == "4.1.2")
    assert body["questions"] == {}
    assert (card["reassess_suggested"], card["reassess_reasons"]) == (False, [])
    assert [e["action"] for e in entries] == ["answer", "question"]  # still in the trail


# --- Through the API: the cards (story 12) ----------------------------------------------------


def accepted_with_question(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    """4.1.2 assessed and accepted, then a question logged on it."""
    thread_id = assess(client, user)
    accepted(client, thread_id, headers=user)
    return log(client, user, control_id="4.1.2").json()["question"]


@pytest.mark.parametrize("goal_", ["audit-readiness", "internal-improvement"])
def test_an_answer_suggests_a_reassessment_until_one_is_accepted(database_url, users, goal_):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        client.put("/api/goal", json={"goal": goal_}, headers=alice)
        asked = accepted_with_question(client, alice)
        logged = card_of(client, alice, "4.1.2")
        answer(client, alice, asked["id"], {"answer": "Yes."}, control_id="4.1.2")
        answered = card_of(client, alice, "4.1.2")
        again = assess(client, alice)
        waiting = card_of(client, alice, "4.1.2")
        accepted(client, again, headers=alice)
        cleared = card_of(client, alice, "4.1.2")

    def flags(card: dict[str, Any]) -> tuple[bool, list[str]]:
        return card["reassess_suggested"], card["reassess_reasons"]

    assert flags(logged) == (False, [])
    assert flags(answered) == (True, ["question-answered"])
    assert flags(waiting) == (False, [])
    assert flags(cleared) == (False, [])
    assert answered["status"] == "accepted"


# --- Postgres: one transaction, turns on the row lock, before setup --------------------------


@needs_postgres
@pytest.mark.parametrize("route", ["question", "answer"])
def test_a_failing_trail_insert_is_a_503_and_keeps_nothing(postgres, users, route):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        asked = log(client, alice).json()["question"] if route == "answer" else None
        with postgres.connection() as conn:  # the entry's insert breaks a constraint
            conn.execute(
                f"ALTER TABLE {AUDIT_TABLE} ADD CONSTRAINT no_questions "
                "CHECK (action NOT IN ('question', 'answer')) NOT VALID"
            )
        if asked is None:
            failed = log(client, alice)
        else:
            failed = answer(client, alice, asked["id"], {"answer": "Yes."})
        listed = overview_of(client, alice)["questions"]

    assert failed.status_code == 503
    assert failed.json()["unavailable"] == "database"
    assert listed == ({} if asked is None else {CONTROL: [asked]})


@needs_postgres
def test_two_answers_at_once_keep_one_answer_and_write_one_entry(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        asked = log(client, alice).json()["question"]
        go = threading.Barrier(2)
        codes: list[int] = []

        def answering(text_: str) -> None:
            go.wait()
            codes.append(answer(client, alice, asked["id"], {"answer": text_}).status_code)

        threads = [threading.Thread(target=answering, args=(t,)) for t in ("Yes.", "No.")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = actions(client, alice, "answer")
        (kept,) = overview_of(client, alice)["questions"][CONTROL]

    assert sorted(codes) == [200, 409]
    (entry,) = entries
    assert kept["answer"] == entry["note"]


@needs_postgres
def test_before_setup_made_the_table_no_questions_are_listed_and_a_log_is_a_503(
    postgres, users, caplog
):
    alice, _ = users
    with TestClient(app_on(postgres.url), raise_server_exceptions=False) as client:
        with postgres.connection() as conn:  # a deploy that adds the table, before setup runs
            conn.execute(f"DROP TABLE {QUESTIONS_TABLE}")
        listed = overview_of(client, alice)["questions"]
        failed = log(client, alice)

    assert listed == {}
    assert failed.status_code == 503
    assert f"no {QUESTIONS_TABLE} table" in caplog.text


def test_setup_makes_the_questions_table_and_health_checks_it():
    assert QUESTIONS_TABLE in SETUP_TABLES


CREATE_AUDIT_BEFORE_414 = (
    f"CREATE TABLE {AUDIT_TABLE} (id bigserial PRIMARY KEY, "
    "user_id text NOT NULL, reviewer_name text NOT NULL, at timestamptz NOT NULL, "
    "profile_id text NOT NULL, control_id text NOT NULL, thread_id text NOT NULL, "
    "action text NOT NULL "
    "CHECK (action IN ('accept', 'reject', 'override', 'confirm', 'approve', 'discard', 'goal', "
    "'round-start', 'round-close', 'priority', 'done')), "
    "requirement_id text, note text NOT NULL, verdict_before text, verdict_after text, "
    "evidence jsonb NOT NULL, model text NOT NULL, prompt_version text NOT NULL, "
    "goal_before text, goal_after text, round_name text)"
)
"""The trail as #410's setup made it."""


@needs_postgres
def test_setup_on_a_database_made_before_414_makes_the_table_and_keeps_the_trail(request, users):
    alice, _ = users
    db = request.getfixturevalue("empty_database")  # not set up: made as #410's setup made it
    with db.connection() as conn:
        conn.execute(CREATE_AUDIT_BEFORE_414)
        conn.execute(CREATE_AUDIT_GUARD)
        for trigger in CREATE_AUDIT_TRIGGERS:
            conn.execute(trigger)
        conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_id, reviewer_name, at, profile_id, control_id, "
            "thread_id, action, note, evidence, model, prompt_version) "
            "VALUES (%s, 'Dana', now() - interval '1 day', %s, '4.1.2', 't', 'done', "
            "'Record made.', '[]', '', '')",
            (alice["X-Test-User"], DEFAULT_PROFILE),
        )

    db.setup()
    shape = _audit_table_shape(db)
    db.setup()  # safe to run again: nothing changes
    assert _audit_table_shape(db) == shape
    with TestClient(app_on(db.url)) as client:
        asked = log(client, alice)
        answered = answer(client, alice, asked.json()["question"]["id"], {"answer": "Yes."})
        entries = trail(client, alice)

    assert (asked.status_code, answered.status_code) == (200, 200)
    assert [e["action"] for e in entries] == ["answer", "question", "done"]  # the old row kept


# --- The report's trail ------------------------------------------------------------------------

QUESTION_WORDS = {  # newest first, as the trail lists them: the answer, then the question
    "en": ("Question answered", "Open question logged"),
    "de": ("Frage beantwortet", "Offene Frage erfasst"),
}


def question_entries() -> list[AuditEntry]:
    base = goal.goal_entry(
        Reviewer("u", "Dana Lead"), "nordlicht", "audit-readiness", "internal-improvement", T0
    )
    common: dict[str, Any] = {"control_id": "1.1.1", "thread_id": "", "requirement_id": None}
    common |= {"goal_before": None, "goal_after": None}
    return [
        replace(base, id=2, action="answer", note="The CISO, each March.", **common),
        replace(
            base, id=1, action="question", note=f"{ASKED} (for IT lead, due 2026-11-30)", **common
        ),
    ]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_questions_trail_rows_say_their_action_in_the_reports_words(language):
    built = built_report(entries=question_entries())

    rows = report.trail_rows(built["trail"]["entries"], report.words_for(language))

    assert [row[4] for row in rows] == list(QUESTION_WORDS[language])
    assert [row[7] for row in rows] == [
        "The CISO, each March.",
        f"{ASKED} (for IT lead, due 2026-11-30)",
    ]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_pdf_builds_with_the_questions_entries(language):
    built = built_report(entries=question_entries())

    read = text(report_pdf.pdf(built, report.words_for(language)))

    for word in QUESTION_WORDS[language]:
        assert word in read
