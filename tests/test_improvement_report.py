"""The improvement report (#420, spec #350 story 13): the readiness report's shape in the Internal
improvement goal, for the team. The current standing against the last review round closed since
the fresh start, the improvement plan by priority with each item's status, the open questions,
the audit trail and an internal self-assessment disclaimer; on the same report page and the same
Markdown and PDF downloads, in the profile's language.

The unit cases build assessments as the graph keeps them (test_plan's); the API cases run on the
bundled fake model, in memory and on Postgres (test_memory's fixtures). The fake's 4.1.2 run judges
M1 partly evidenced and the rest evidenced, so it drafts one item, for M1."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from reviewing import OWNER, confirm
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import WITHOUT_1_1_1
from test_memory import app_on, assess
from test_plan import T0, approved, assessment, done
from test_report import every_word, section, table_rows, under
from test_report_pdf import text

from vouch import report, report_pdf, rounds
from vouch.audit import AuditEntry, Page
from vouch.graph import Assessment, Verdict
from vouch.language import audit_claim
from vouch.plan import card_flags
from vouch.profile import load_named_profile
from vouch.questions import OpenQuestion
from vouch.remediation import DecidedItem, Key, Mark

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

TODAY = date(2026, 11, 20)
NORDLICHT = load_named_profile("nordlicht")
CONTROLS = list(NORDLICHT.isa_edition().controls)


def built(
    latest: dict[str, Assessment] | None = None,
    *,
    history: list[Assessment] | None = None,
    decided: dict[Key, DecidedItem] | None = None,
    marks: dict[Key, Mark] | None = None,
    comparison: dict[str, Any] | None = None,
    questions: list[OpenQuestion] | None = None,
    profile: Any = NORDLICHT,
    entries: list[AuditEntry] | None = None,
) -> dict[str, Any]:
    """The improvement report drawn up on TODAY, its flags from the history (as the API reads
    them), as served before redaction."""
    history = history if history is not None else list((latest or {}).values())
    decided = decided or {}
    marks = marks or {}
    flags = card_flags(history, decided, marks, "internal-improvement")
    return dict(
        report.improvement_report(
            profile,
            profile.isa_edition().controls,
            latest if latest is not None else {},
            history=history,
            decided=decided,
            marks=marks,
            flags=flags,
            comparison=comparison,  # type: ignore[arg-type]
            questions=questions or [],
            trail=Page(entries or [], None),
            today=TODAY,
        )
    )


def plan_of(shown: dict[str, Any]) -> list[tuple[str, list[tuple[str, str, str, str]]]]:
    """The plan as (priority, [(control, requirement, status, note)])."""
    return [
        (
            g["priority"],
            [(i["control_id"], i["requirement_id"], i["status"], i["note"]) for i in g["items"]],
        )
        for g in shown["plan"]
    ]


# --- Its shape --------------------------------------------------------------------------------


def test_the_improvement_report_has_its_parts_and_none_of_the_auditors():
    shown = built()

    assert shown["goal"] == "internal-improvement"
    assert set(shown) == {
        "goal",
        "head",
        "readiness",
        "comparison",
        "plan",
        "questions",
        "trail",
        "disclaimer",
    }
    assert shown["head"]["company"] == NORDLICHT.company
    assert shown["head"]["date"] == "2026-11-20"
    assert shown["readiness"]["controls"] == len(CONTROLS)


def test_the_readiness_report_names_its_goal():
    shown = report.readiness_report(
        NORDLICHT, CONTROLS, {}, decided={}, trail=Page([], None), today=TODAY
    )

    assert shown["goal"] == "audit-readiness"


# --- The comparison ----------------------------------------------------------------------------


def accepted_run(control_id: str, thread_id: str, verdict: Verdict) -> Assessment:
    """The control accepted (by Alice) with the verdict, its one requirement so judged."""
    return replace(assessment(thread_id, {"M1": verdict}, control_id=control_id), verdict=verdict)


def comparison_against(latest: dict[str, Assessment]) -> dict[str, Any]:
    """Now against "Q3 2026", closed on 2026-10-01 with 1.1.1 accepted partly evidenced."""
    frozen = [rounds.FrozenControl("1.1.1", "accepted", "partly-evidenced", "old")]
    closed_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC).isoformat()
    return {"against": {"name": "Q3 2026", "closed_at": closed_at}} | dict(
        rounds.compare(frozen, CONTROLS, latest)
    )


def test_the_comparison_names_the_round_its_close_and_the_changed_control():
    latest = {
        "1.1.1": accepted_run("1.1.1", "a", "evidenced"),
        "4.1.2": accepted_run("4.1.2", "b", "partly-evidenced"),
    }

    shown = built(latest, comparison=comparison_against(latest))
    comparison = shown["comparison"]

    assert comparison["against"]["name"] == "Q3 2026"
    assert comparison["accepted"] == {"now": 2, "then": 1}
    assert {
        "control_id": "1.1.1",
        "before": "partly-evidenced",
        "after": "evidenced",
        "reassessing": False,
    } in comparison["changed"]
    words = report.words_for("en")
    lead, counts, closing = report.comparison_lines(comparison, words)
    assert lead == [
        "Compared with Q3 2026, closed on 2026-10-01.",
        "Accepted: 2 now, 1 when Q3 2026 closed.",
    ]
    assert counts == ["Evidenced 1 (+1)", "Partly evidenced 1 (no change)"]
    assert closing is None
    assert report.changed_rows(comparison, words) == [
        ["1.1.1", "Partly evidenced", "Evidenced"],
        ["4.1.2", "Not accepted", "Partly evidenced"],
    ]


def test_a_verdict_less_now_reads_minus_and_nothing_changed_says_so():
    frozen = [rounds.FrozenControl("1.1.1", "accepted", "partly-evidenced", "old")]
    comparison = {"against": {"name": "Q3", "closed_at": "2026-10-01T00:00:00+00:00"}} | dict(
        rounds.compare(frozen, CONTROLS, {})
    )
    _, counts, closing = report.comparison_lines(comparison, report.words_for("en"))
    assert counts == ["Partly evidenced 0 (-1)"]
    assert closing is None

    same = {"1.1.1": accepted_run("1.1.1", "a", "partly-evidenced")}
    unchanged = {"against": {"name": "Q3", "closed_at": "2026-10-01T00:00:00+00:00"}} | dict(
        rounds.compare(frozen, CONTROLS, same)
    )
    _, _, closing = report.comparison_lines(unchanged, report.words_for("de"))
    assert closing == "Seit dem Abschluss von Q3 hat sich nichts geändert."


def test_with_no_round_closed_the_comparison_is_null_and_the_markdown_says_so():
    shown = built()

    assert shown["comparison"] is None
    md = report.markdown(shown, report.words_for("en"))
    assert section(md, "Compared with the last round").strip().splitlines()[-1] == (
        "No review round has been closed yet, so there is nothing to compare with."
    )


# --- The improvement plan ----------------------------------------------------------------------

DAY_1 = T0 + timedelta(days=1)


def test_the_plan_groups_items_by_priority_with_their_status_and_note():
    planned = assessment(
        "a",
        {
            "M1": "not-evidenced",
            "M2": "partly-evidenced",
            "M3": "partly-evidenced",
            "M4": "not-evidenced",
        },
    )
    waiting = assessment(
        "w", {"M1": "not-evidenced"}, status="waiting-for-review", control_id="1.1.1"
    )
    decided = {
        ("a", "M1"): approved("a", "M1"),
        ("a", "M2"): approved("a", "M2"),
        ("a", "M3"): approved("a", "M3"),
        ("a", "M4"): approved("a", "M4", "discarded"),
        ("w", "M1"): approved("w", "M1"),
    }
    marks = {
        ("a", "M2"): Mark(priority="low", done_at=None, done_note=None),
        ("a", "M3"): Mark(priority=None, done_at=DAY_1, done_note="Policy signed."),
    }

    shown = built(
        {"4.1.2": planned, "1.1.1": waiting},
        history=[waiting, planned],
        decided=decided,
        marks=marks,
    )

    assert plan_of(shown) == [
        ("high", [("4.1.2", "M1", "open", "")]),
        ("medium", [("4.1.2", "M3", "done", "Policy signed.")]),
        ("low", [("4.1.2", "M2", "open", "")]),
    ]
    item = shown["plan"][0]["items"][0]
    assert item == {
        "control_id": "4.1.2",
        "requirement_id": "M1",
        "missing": "M1 missing.",
        "owner_role": "CISO",
        "due_date": "2027-01-01",
        "status": "open",
        "note": "",
    }
    rows = report.plan_rows(shown["plan"][1], report.words_for("en"))
    assert rows == [["4.1.2", "M3", "M3 missing.", "CISO", "2027-01-01", "Done", "Policy signed."]]
    assert shown["readiness"]["remediation_items"] == 3  # the items the plan lists


def test_a_closed_controls_items_show_closed_from_the_plan_that_closed_it():
    planned = assessment("p", {"M1": "not-evidenced", "M2": "partly-evidenced"})
    closing = assessment(
        "r", {"M1": "evidenced", "M2": "evidenced"}, reviewed_at=T0 + timedelta(days=2)
    )
    decided = {(("p", r)): approved("p", r) for r in ("M1", "M2")}
    marks = {("p", r): done(DAY_1, f"{r} fixed.") for r in ("M1", "M2")}

    shown = built({"4.1.2": closing}, history=[closing, planned], decided=decided, marks=marks)

    assert plan_of(shown) == [
        ("high", [("4.1.2", "M1", "closed", "M1 fixed.")]),
        ("medium", [("4.1.2", "M2", "closed", "M2 fixed.")]),
    ]
    assert report.plan_rows(shown["plan"][0], report.words_for("de"))[0][5] == "Geschlossen"


def test_done_items_stay_done_while_their_reassessment_waits_or_shows_a_gap():
    # #414: card_flags hides the suggestion while a re-assessment waits; Closed needs it accepted.
    planned = assessment("p", {"M1": "not-evidenced"})
    decided = {("p", "M1"): approved("p", "M1")}
    marks = {("p", "M1"): done(DAY_1, "Fixed.")}
    waiting = assessment("r", {"M1": "evidenced"}, status="waiting-for-review")
    gap = assessment("g", {"M1": "partly-evidenced"}, reviewed_at=T0 + timedelta(days=2))

    for newest in (waiting, gap):
        shown = built({"4.1.2": newest}, history=[newest, planned], decided=decided, marks=marks)
        statuses = [i["status"] for g in shown["plan"] for i in g["items"]]
        # The gap's own item is a draft (not approved), so the plan is still the earlier one.
        assert statuses == ["done"], newest.thread_id


def test_a_new_gaps_item_after_a_closed_control_reads_open():
    # #420's review: A's item done, B closed the control, C found a new gap with an approved item.
    planned = assessment("a", {"M1": "not-evidenced"})
    closed_by = assessment("b", {"M1": "evidenced"}, reviewed_at=T0 + timedelta(days=2))
    new_gap = assessment("c", {"M2": "not-evidenced"}, reviewed_at=T0 + timedelta(days=3))
    decided = {("a", "M1"): approved("a", "M1"), ("c", "M2"): approved("c", "M2")}
    marks = {("a", "M1"): done(DAY_1, "Fixed.")}

    shown = built(
        {"4.1.2": new_gap},
        history=[new_gap, closed_by, planned],
        decided=decided,
        marks=marks,
    )

    assert plan_of(shown) == [("high", [("4.1.2", "M2", "open", "")])]


def test_items_follow_the_base_frameworks_control_order_within_a_priority():
    later = assessment("x", {"M1": "not-evidenced"}, control_id="4.1.2")
    earlier = assessment("y", {"M1": "not-evidenced"}, control_id="1.1.1")
    decided = {("x", "M1"): approved("x", "M1"), ("y", "M1"): approved("y", "M1")}

    shown = built({"4.1.2": later, "1.1.1": earlier}, history=[later, earlier], decided=decided)

    assert [i["control_id"] for i in shown["plan"][0]["items"]] == ["1.1.1", "4.1.2"]


def test_with_no_approved_item_the_plan_is_empty_and_the_markdown_says_so():
    shown = built()

    assert shown["plan"] == []
    md = report.markdown(shown, report.words_for("de"))
    assert "Noch ist keine Maßnahme freigegeben." in section(md, "Verbesserungsplan")


# --- The open questions ------------------------------------------------------------------------


def question(n: int, control_id: str, due: date, **fields: Any) -> OpenQuestion:
    return OpenQuestion(
        id=n,
        control_id=control_id,
        question=f"Question {n}?",
        asked_of="IT lead",
        due_date=due,
        asked_at=T0 + timedelta(minutes=n),
        **fields,
    )


def test_the_questions_are_answered_overdue_or_open_as_of_the_day_it_is_drawn_up():
    answered = question(
        1, "4.1.2", TODAY - timedelta(days=5), answer="Yes.", answered_at=T0 + timedelta(days=2)
    )
    overdue = question(2, "4.1.2", TODAY - timedelta(days=1))
    due_today = question(3, "1.1.1", TODAY)

    shown = built(questions=[answered, overdue, due_today])

    assert [(q["control_id"], q["id"], q["overdue"]) for q in shown["questions"]] == [
        ("1.1.1", 3, False),
        ("4.1.2", 1, False),
        ("4.1.2", 2, True),
    ]
    rows = report.question_rows(shown["questions"], report.words_for("en"))
    assert rows == [
        ["1.1.1", "Question 3?", "IT lead", TODAY.isoformat(), "Open", ""],
        ["4.1.2", "Question 1?", "IT lead", "2026-11-15", "Answered", "Yes."],
        ["4.1.2", "Question 2?", "IT lead", "2026-11-19", "Overdue", ""],
    ]
    assert [r[4] for r in report.question_rows(shown["questions"], report.words_for("de"))] == [
        "Offen",
        "Beantwortet",
        "Überfällig",
    ]


# --- The disclaimer ----------------------------------------------------------------------------


def test_the_disclaimer_names_the_accepting_reviewer_or_says_nothing_was_reviewed():
    latest = {"4.1.2": accepted_run("4.1.2", "a", "evidenced")}  # reviewed by "Alice"

    assert built(latest)["disclaimer"] == (
        "Internal self-assessment, drafted by an AI assistant, reviewed by Alice."
    )
    assert built()["disclaimer"] == (
        "Internal self-assessment, drafted by an AI assistant; nothing has been reviewed yet."
    )
    alpenwerk = load_named_profile("alpenwerk")
    german = built({"1.1.1": accepted_run("1.1.1", "a", "evidenced")}, profile=alpenwerk)
    assert german["disclaimer"] == (
        "Interne Selbstbewertung, erstellt von einem KI-Assistenten, geprüft von Alice."
    )
    assert built(profile=alpenwerk)["disclaimer"] == (
        "Interne Selbstbewertung, erstellt von einem KI-Assistenten; noch nichts geprüft."
    )


# --- The words -----------------------------------------------------------------------------------

NEW_WORDS = {
    "improvement_title": "Verbesserungsbericht",
    "labels.comparison": "Vergleich mit der letzten Prüfrunde",
    "labels.changed": "Geänderte Kontrollen",
    "labels.plan": "Verbesserungsplan",
    "labels.open_questions": "Offene Fragen",
    "labels.asked_of": "Für",
    "labels.answer": "Antwort",
    "priorities.high": "Hoch",
    "priorities.medium": "Mittel",
    "priorities.low": "Niedrig",
    "item_statuses.open": "Offen",
    "item_statuses.done": "Erledigt",
    "item_statuses.closed": "Geschlossen",
    "question_statuses.answered": "Beantwortet",
    "question_statuses.overdue": "Überfällig",
    "question_statuses.open": "Offen",
    "sentences.no_change": "(unverändert)",
    "sentences.not_accepted": "Nicht akzeptiert",
    "sentences.no_plan": "Noch ist keine Maßnahme freigegeben.",
    "sentences.no_questions": "Es wurde keine offene Frage erfasst.",
}


def word(words: dict[str, Any], path: str) -> Any:
    for part in path.split("."):
        words = words[part]
    return words


@pytest.mark.parametrize("path", NEW_WORDS)
def test_each_new_word_has_an_english_and_a_german_entry_and_claims_no_audit_result(path):
    english, german = word(report.words_for("en"), path), word(report.words_for("de"), path)

    assert german == NEW_WORDS[path]
    assert isinstance(english, str) and english
    assert audit_claim(english) is None and audit_claim(german) is None
    assert len(every_word(report.words_for("en"))) == len(every_word(report.words_for("de")))


# --- The Markdown ------------------------------------------------------------------------------


def full_report() -> dict[str, Any]:
    """A report with a comparison, a plan item done with a note carrying `|`, `<` and a line
    break, and an answered question whose answer carries them too."""
    planned = assessment("a", {"M1": "not-evidenced"})
    decided = {("a", "M1"): approved("a", "M1")}
    marks = {("a", "M1"): done(DAY_1, "Fixed | <b>now</b>\nfor good.")}
    latest = {"4.1.2": planned}
    asked = question(1, "4.1.2", TODAY, answer="Yes | <i>all</i>\nof them.", answered_at=DAY_1)
    return built(
        latest,
        decided=decided,
        marks=marks,
        comparison=comparison_against(latest),
        questions=[asked],
        entries=[],
    )


def test_the_markdown_has_its_parts_in_order_and_escapes_carried_text():
    md = report.markdown(full_report(), report.words_for("en"))

    headings = [line for line in md.splitlines() if line.startswith("#")]
    assert headings == [
        "# Improvement report",
        "## Readiness",
        "## Compared with the last round",
        "### Changed controls",
        "## Improvement plan",
        "### High",
        "## Open questions",
        "## Audit trail",
    ]
    assert md.index("- **Company:**") < md.index("## Readiness")
    assert md.rstrip().endswith(
        "---\n\nInternal self-assessment, drafted by an AI assistant, reviewed by Alice."
    )
    (plan_row,) = table_rows(section(md, "Improvement plan"))
    assert plan_row[5:] == ["Done", "Fixed \\| &lt;b&gt;now&lt;/b&gt; for good."]
    (question_row,) = table_rows(section(md, "Open questions"))
    assert question_row[4:] == ["Answered", "Yes \\| &lt;i&gt;all&lt;/i&gt; of them."]
    assert "<b>" not in md and "<i>" not in md
    for gone in ("## Controls", "## Remediation items", "## Not applicable"):
        assert gone not in md


def test_the_markdown_with_nothing_logged_has_each_empty_sentence():
    md = report.markdown(built(), report.words_for("en"))

    assert "No remediation item is approved yet." in section(md, "Improvement plan")
    assert "No open question has been logged." in section(md, "Open questions")


# --- The PDF -----------------------------------------------------------------------------------


def test_the_pdf_builds_with_a_long_answer_and_holds_each_part_in_order():
    shown = full_report()
    shown["questions"][0]["answer"] = " ".join(f"Antwort {n} zum Thema." for n in range(150))
    assert len(shown["questions"][0]["answer"]) >= 3000
    planned_low = dict(shown["plan"][0], priority="low")
    shown["plan"] = [*shown["plan"], planned_low]

    data = report_pdf.pdf(shown, report.words_for("en"))
    read = text(data)

    order = [
        "IMPROVEMENT REPORT",
        "READINESS",
        "COMPARED WITH THE LAST ROUND",
        "Changed controls",
        "IMPROVEMENT PLAN",
        "High",
        "Low",
        "OPEN QUESTIONS",
        "AUDIT TRAIL",
        "Internal self-assessment, drafted by an AI assistant, reviewed by Alice.",
    ]
    at = [read.index(part) for part in order]
    assert at == sorted(at), dict(zip(order, at, strict=True))
    assert "Antwort 149 zum Thema." in read  # the long answer whole, in its note
    assert "CONTROLS" not in read and "NOT APPLICABLE" not in read


# --- Through the API ---------------------------------------------------------------------------


def improve(client: TestClient, user: dict[str, str]) -> None:
    response = client.put("/api/goal", json={"goal": "internal-improvement"}, headers=user)
    assert response.status_code == 200, response.text


def report_of(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/report", headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def planned_and_done(client: TestClient, user: dict[str, str]) -> str:
    """4.1.2 assessed, M1's item approved, accepted, then marked done: its thread id."""
    thread_id = assess(client, user)
    assert confirm(client, thread_id, headers=user).status_code == 200
    approve = {"decision": "approve", "owner_role": OWNER}
    response = client.post(
        f"/api/assessments/{thread_id}/remediation/M1", json=approve, headers=user
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/assessments/{thread_id}/review", json={"decision": "accept"}, headers=user
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/assessments/{thread_id}/remediation/M1/done", json={"note": "Done."}, headers=user
    )
    assert response.status_code == 200, response.text
    return thread_id


def log(client: TestClient, user: dict[str, str], asked: str, control_id: str = "1.1.1") -> None:
    due = (datetime.now(UTC).date() + timedelta(days=14)).isoformat()
    sent = {"question": asked, "asked_of": "IT lead", "due_date": due}
    response = client.post(f"/api/controls/{control_id}/questions", json=sent, headers=user)
    assert response.status_code == 200, response.text


def test_under_audit_readiness_the_report_names_its_goal(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        shown = report_of(client, alice)

    assert shown["goal"] == "audit-readiness"
    assert {"controls", "remediation", "not_applicable"} <= set(shown)


def test_under_internal_improvement_the_report_is_the_improvement_report(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        planned_and_done(client, alice)
        assert client.post("/api/rounds/close", headers=alice).status_code == 200
        log(client, alice, "Who owns the scope?")
        shown = report_of(client, alice)

    assert shown["goal"] == "internal-improvement"
    assert set(shown) == {
        "goal",
        "head",
        "readiness",
        "comparison",
        "plan",
        "questions",
        "trail",
        "disclaimer",
        "words",
    }
    assert shown["comparison"]["against"]["name"] == rounds.FIRST_ROUND
    assert shown["comparison"]["accepted"] == {"now": 1, "then": 1}
    assert plan_of(shown) == [("medium", [("4.1.2", "M1", "done", "Done.")])]  # partly evidenced
    assert [(q["control_id"], q["question"], q["answer"]) for q in shown["questions"]] == [
        ("1.1.1", "Who owns the scope?", None)
    ]
    assert shown["disclaimer"].startswith("Internal self-assessment, drafted by an AI assistant")
    assert shown["words"]["improvement_title"] == "Improvement report"


def test_a_question_asked_before_the_fresh_start_is_not_in_the_report(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        improve(client, alice)
        log(client, alice, "Asked before.")
        assert client.post("/api/progress/fresh-start", headers=alice).status_code == 200
        log(client, alice, "Asked after.")
        shown = report_of(client, alice)

    assert [q["question"] for q in shown["questions"]] == ["Asked after."]


@pytest.mark.parametrize(("path", "extension"), [("markdown", "md"), ("pdf", "pdf")])
def test_the_improvement_downloads_are_named_improvement_report(
    database_url, users, monkeypatch, path, extension
):
    alice, _ = users
    monkeypatch.setattr(report, "today", lambda: date(2026, 11, 20))
    with TestClient(app_on(database_url)) as client:
        improve(client, under(alice, "alpenwerk"))
        response = client.get(f"/api/report/{path}", headers=under(alice, "alpenwerk"))

    assert response.status_code == 200, response.text
    name = f"improvement-report-alpenwerk-2026-11-20.{extension}"
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}"
    )
    if extension == "md":
        assert response.text.startswith("# Verbesserungsbericht\n")
    else:
        assert text(response.content).startswith("VERBESSERUNGSBERICHT")


def test_a_control_not_applicable_as_a_whole_is_out_of_the_readiness():
    # #454: as the overview counts it.
    latest = {
        "1.1.1": assessment("t-1", {"M1": "evidenced"}, control_id="1.1.1"),
        "1.6.1": assessment("t-2", {"M1": "evidenced"}, control_id="1.6.1"),
    }

    shown = built(latest, profile=WITHOUT_1_1_1)

    assert shown["readiness"]["controls"] == len(CONTROLS) - 1
    assert shown["readiness"]["accepted"] == 1
    assert shown["readiness"]["open"] == len(CONTROLS) - 2


def test_a_control_not_applicable_as_a_whole_has_no_plan_item_question_or_reviewer():
    # #454 review: the plan, the questions and the disclaimer leave it out, as readiness does.
    excluded = assessment("x", {"M1": "not-evidenced"}, control_id="1.1.1")
    kept = assessment("k", {"M1": "not-evidenced"}, control_id="1.6.1")
    decided = {("x", "M1"): approved("x", "M1"), ("k", "M1"): approved("k", "M1")}
    asked = [question(1, "1.1.1", TODAY), question(2, "1.6.1", TODAY)]

    shown = built(
        {"1.1.1": excluded},
        history=[excluded, kept],
        decided=decided,
        questions=asked,
        profile=WITHOUT_1_1_1,
    )

    assert [i["control_id"] for g in shown["plan"] for i in g["items"]] == ["1.6.1"]
    assert [q["control_id"] for q in shown["questions"]] == ["1.6.1"]
    assert shown["disclaimer"] == (
        "Internal self-assessment, drafted by an AI assistant; nothing has been reviewed yet."
    )
