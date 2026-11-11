"""The placeholder words and trail notes Vouch stores in English, served in the answer language
(#535): "not drafted", "no owner suggested", and the notes of an approval or discard, a question
and a priority change. Stored items and audit entries stay English; the API says them again when
it serves them, as vouch.language.stale_reason does for a stale document's reason (#359). English,
and a language without German's words, read as stored. Each note's shape is vouch.language's
NOTE_WORDS: the writers write it from there, and trail_note reads it back from there."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
import reviewing
import test_memory
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from test_database import empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_memory import app_on
from test_plan import T0, assessment
from test_report import assessed, entry, pdf_text, report_of, under

from vouch import api, audit, graph, report, report_pdf
from vouch.audit import Action, AuditEntry, Page
from vouch.graph import build_graph, thread_config
from vouch.language import (
    NOTE_WORDS,
    decided_note,
    placeholder,
    priority_note,
    question_note,
    trail_note,
)
from vouch.memory import Memory, process_checkpointer
from vouch.plan import card_flags
from vouch.profile import load_named_profile
from vouch.remediation import DecidedItem, Key, shown_items

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

# --- The words, unit by unit -------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(NOTE_WORDS))
def test_each_languages_placeholders_are_vouchs_two(code):
    assert set(NOTE_WORDS[code].placeholders) == {graph.NOT_DRAFTED, graph.NO_OWNER}


def test_the_placeholders_are_said_in_german_and_as_stored_in_english():
    assert placeholder(graph.NOT_DRAFTED, "de") == "nicht entworfen"
    assert placeholder(graph.NO_OWNER, "de") == "keine verantwortliche Rolle vorgeschlagen"
    assert placeholder(graph.NOT_DRAFTED, "en") == "not drafted"
    assert placeholder(graph.NOT_DRAFTED, "lt") == "not drafted"


def test_a_drafted_missing_text_stays_as_written():
    assert placeholder("Ein Register der Schlüssel.", "de") == "Ein Register der Schlüssel."


def test_the_reports_priority_words_are_the_notes():
    assert report.words_for("de")["priorities"] is NOTE_WORDS["de"].priorities


@pytest.mark.parametrize("action", ["approve", "discard"])
def test_a_decisions_note_reads_in_german(action):
    note = "Ein Register der Schlüssel. Owner: IT-Leiter. Due 2027-01-09."

    assert trail_note(note, action, "de") == (
        "Ein Register der Schlüssel. Verantwortliche Rolle: IT-Leiter. Fällig am 2027-01-09."
    )
    assert trail_note(note, action, "en") == note


def test_a_not_drafted_items_discard_says_both_placeholders_in_german():
    note = "not drafted Owner: no owner suggested. Due 2027-02-08."

    assert trail_note(note, "discard", "de") == (
        "nicht entworfen Verantwortliche Rolle: keine verantwortliche Rolle vorgeschlagen. "
        "Fällig am 2027-02-08."
    )


@pytest.mark.parametrize(
    ("missing", "owner"),
    [
        ("Ein Register der Schlüssel.", "Asset Owner: Plant 2"),  # the lead's owner holds it
        ("not drafted", "Asset Owner: Plant 2"),
        ("A list with an Owner: column.", "CISO"),  # Vouch's missing text holds it
    ],
)
def test_an_owner_in_the_missing_text_or_the_owner_role_stays_in_its_own_part(missing, owner):
    note = decided_note(missing, owner, date(2027, 1, 9))

    assert trail_note(note, "approve", "de") == (
        f"{placeholder(missing, 'de')} Verantwortliche Rolle: {placeholder(owner, 'de')}. "
        "Fällig am 2027-01-09."
    )


def test_a_questions_note_reads_in_german():
    note = "Gibt es eine Liste je System? (for IT-Leiter, due 2026-12-01)"

    assert trail_note(note, "question", "de") == (
        "Gibt es eine Liste je System? (für IT-Leiter, fällig am 2026-12-01)"
    )
    assert trail_note(note, "question", "en") == note


def test_an_asked_of_holding_for_stays_in_its_own_part():
    note = question_note("Wer prüft das?", "Leitung (for Werk 2)", date(2026, 12, 1))

    assert trail_note(note, "question", "de") == (
        "Wer prüft das? (für Leitung (for Werk 2), fällig am 2026-12-01)"
    )


def test_a_priority_changes_note_reads_in_german_with_the_reports_priority_words():
    note = "Priority: low (was medium)."

    assert trail_note(note, "priority", "de") == "Priorität: Niedrig (vorher Mittel)."
    assert trail_note(note, "priority", "en") == note


DUE = date(2027, 2, 8)


@pytest.mark.parametrize(
    ("action", "written", "german"),
    [
        (
            "approve",
            decided_note("Ein Register.", "IT-Leiter", DUE),
            "Ein Register. Verantwortliche Rolle: IT-Leiter. Fällig am 2027-02-08.",
        ),
        (
            "discard",
            decided_note(graph.NOT_DRAFTED, graph.NO_OWNER, DUE),
            (
                "nicht entworfen Verantwortliche Rolle: keine verantwortliche Rolle vorgeschlagen. "
                "Fällig am 2027-02-08."
            ),
        ),
        (
            "question",
            question_note("Gibt es eine Liste?", "IT-Leiter", DUE),
            "Gibt es eine Liste? (für IT-Leiter, fällig am 2027-02-08)",
        ),
        ("priority", priority_note("high", "low"), "Priorität: Hoch (vorher Niedrig)."),
    ],
)
def test_each_note_vouch_writes_reads_back_in_each_language(action, written, german):
    """A round trip: a reworded shape that trail_note can't read would fall back to English."""
    assert trail_note(written, action, "de") == german
    assert trail_note(written, action, "en") == written


@pytest.mark.parametrize(
    ("note", "action"),
    [
        ("Die Liste fehlt.", "override"),  # a lead's own reason
        ("Passt so.", "accept"),  # a decision's note
        ("MFA eingeführt.", "done"),  # a done note
        ("", "accept"),
        # A lead's own note in a decision's shape is the lead's: only its action's shape is said.
        ("Draft Owner: CISO. Due 2027-01-09.", "override"),
        ("Priority: low (was medium).", "done"),
        # A note that doesn't match its action's shape.
        ("Freigegeben ohne Termin.", "approve"),
        ("Priority: urgent (was medium).", "priority"),
        ("Wer prüft das?", "question"),
    ],
)
def test_a_note_in_no_shape_of_its_action_stays_as_written(note: str, action: Action):
    assert trail_note(note, action, "de") == note


# --- The report: its remediation table, plan and trail, on the page, in Markdown and the PDF ----

TODAY = date(2026, 11, 20)
ALPENWERK = load_named_profile("alpenwerk")
NORDLICHT = load_named_profile("nordlicht")
NOT_DRAFTED_NOTE = "not drafted Owner: IT-Leiter. Due 2027-02-08."
GERMAN_NOTE = "nicht entworfen Verantwortliche Rolle: IT-Leiter. Fällig am 2027-02-08."


def not_drafted_approved(thread_id: str = "t1") -> dict[Key, DecidedItem]:
    """M1's item approved as Vouch left it, not drafted, with the owner the lead named."""
    kept = DecidedItem(
        thread_id, "M1", "approved", graph.NOT_DRAFTED, "IT-Leiter", date(2027, 2, 8), T0
    )
    return {(thread_id, "M1"): kept}


def its_approval(profile_id: str) -> AuditEntry:
    return replace(
        entry("alice", 1),
        profile_id=profile_id,
        action="approve",
        requirement_id="M1",
        note=NOT_DRAFTED_NOTE,
    )


def readiness_of(profile: Any) -> dict[str, Any]:
    latest = {"4.1.2": assessment("t1", {"M1": "not-evidenced"})}
    return dict(
        report.readiness_report(
            profile,
            profile.isa_edition().controls,
            latest,
            decided=not_drafted_approved(),
            trail=Page([its_approval(profile.id)], None),
            today=TODAY,
        )
    )


def improvement_of(profile: Any) -> dict[str, Any]:
    history = [assessment("t1", {"M1": "not-evidenced"})]
    decided = not_drafted_approved()
    return dict(
        report.improvement_report(
            profile,
            profile.isa_edition().controls,
            {"4.1.2": history[0]},
            history=history,
            decided=decided,
            marks={},
            flags=card_flags(history, decided, {}, "internal-improvement"),
            comparison=None,
            questions=[],
            trail=Page([its_approval(profile.id)], None),
            today=TODAY,
        )
    )


def remediation_missing(shown: dict[str, Any]) -> list[str]:
    return [i["missing"] for group in shown["remediation"] for i in group["items"]]


def plan_missing(shown: dict[str, Any]) -> list[str]:
    return [i["missing"] for group in shown["plan"] for i in group["items"]]


def notes(shown: dict[str, Any]) -> list[str]:
    return [e["note"] for e in shown["trail"]["entries"]]


def test_a_not_drafted_item_reads_in_german_in_a_german_readiness_report():
    shown = readiness_of(ALPENWERK)

    assert remediation_missing(shown) == ["nicht entworfen"]
    assert notes(shown) == [GERMAN_NOTE]


def test_a_not_drafted_item_reads_as_stored_in_an_english_readiness_report():
    shown = readiness_of(NORDLICHT)

    assert remediation_missing(shown) == ["not drafted"]
    assert notes(shown) == [NOT_DRAFTED_NOTE]


def test_a_not_drafted_item_reads_in_german_in_a_german_improvement_reports_plan():
    shown = improvement_of(ALPENWERK)

    assert plan_missing(shown) == ["nicht entworfen"]
    assert notes(shown) == [GERMAN_NOTE]
    assert plan_missing(improvement_of(NORDLICHT)) == ["not drafted"]
    assert notes(improvement_of(NORDLICHT)) == [NOT_DRAFTED_NOTE]


def test_an_owner_cell_holding_the_no_owner_sentinel_reads_in_german():
    """An approval refuses it, but where the sentinel reaches a German report it is German."""
    latest = {"4.1.2": assessment("t1", {"M1": "not-evidenced"})}
    decided = {k: replace(v, owner_role=graph.NO_OWNER) for k, v in not_drafted_approved().items()}
    for profile, owner in [
        (ALPENWERK, "keine verantwortliche Rolle vorgeschlagen"),
        (NORDLICHT, graph.NO_OWNER),
    ]:
        shown = report.readiness_report(
            profile,
            profile.isa_edition().controls,
            latest,
            decided=decided,
            trail=Page([], None),
            today=TODAY,
        )
        assert [i["owner_role"] for g in shown["remediation"] for i in g["items"]] == [owner]


@pytest.mark.parametrize("built", [readiness_of, improvement_of])
def test_the_markdown_and_the_pdf_carry_the_german_words(built):
    shown = built(ALPENWERK)
    words = report.words_for("de")

    for written in (report.markdown(shown, words), pdf_text(report_pdf.pdf(shown, words))):
        assert "nicht entworfen" in written
        assert "Verantwortliche Rolle:" in written
        assert "not drafted" not in written
        assert "Owner:" not in written


# --- Through the API, on Alpenwerk with the fake model ----------------------------------------

S1_NOT_EVIDENCED = {"requirement_id": "S1", "verdict": "not-evidenced", "reason": "Keine Liste."}


def served_s1(client: TestClient, user: dict[str, str], thread_id: str) -> dict[str, Any]:
    shown = client.get(f"/api/assessments/{thread_id}", headers=user)
    assert shown.status_code == 200, shown.text
    return next(i for i in shown.json()["assessment"]["remediation"] if i["requirement_id"] == "S1")


def test_alpenwerks_working_paper_report_and_trail_say_the_placeholders_and_notes_in_german(
    database_url, users
):
    alice = under(users[0], "alpenwerk")
    due = (date.today() + timedelta(days=30)).isoformat()
    app = app_on(database_url)
    with TestClient(app) as client:
        thread_id = assessed(client, alice)["thread_id"]
        assert reviewing.confirm(client, thread_id, [S1_NOT_EVIDENCED], alice).status_code == 200

        # The working paper: the item in German, its owner role the sentinel the frontend checks.
        draft = served_s1(client, alice, thread_id)
        assert (draft["missing"], draft["owner_role"]) == ("nicht entworfen", graph.NO_OWNER)

        kept = reviewing.decide(
            client,
            thread_id,
            "S1",
            {"decision": "approve", "owner_role": "IT-Leiter", "due_date": due},
            alice,
        )
        assert kept["missing"] == "nicht entworfen"  # the item the working paper shows next
        assert served_s1(client, alice, thread_id)["missing"] == "nicht entworfen"
        asked = client.post(
            "/api/controls/4.1.2/questions",
            json={
                "question": "Gibt es eine Liste je System?",
                "asked_of": "IT-Leiter",
                "due_date": due,
            },
            headers=alice,
        )
        assert asked.status_code == 200, asked.text

        approval = f"nicht entworfen Verantwortliche Rolle: IT-Leiter. Fällig am {due}."
        question = f"Gibt es eine Liste je System? (für IT-Leiter, fällig am {due})"
        for served in (
            report_of(client, alice)["trail"],
            client.get("/api/audit-trail", headers=alice).json(),
        ):
            by_action = {e["action"]: e["note"] for e in served["entries"]}
            assert (by_action["approve"], by_action["question"]) == (approval, question)

        # Nothing stored changes: the decided item and its entry read as Vouch wrote them.
        user_id = alice["X-Test-User"]
        stored = api.remediation_items(app.state.database).decided(
            user_id, "alpenwerk", [thread_id]
        )
        assert stored[(thread_id, "S1")].missing == graph.NOT_DRAFTED
        entries = audit.read_all(api.audit_trail(app.state.database), user_id, "alpenwerk")
        notes_kept = {e.action: e.note for e in entries}
        assert notes_kept["approve"] == f"not drafted Owner: IT-Leiter. Due {due}."
        assert notes_kept["question"] == f"Gibt es eine Liste je System? (for IT-Leiter, due {due})"


def test_alpenwerks_priority_change_serves_its_item_and_note_in_german(users):
    alice = under(users[0], "alpenwerk")
    due = (date.today() + timedelta(days=30)).isoformat()
    approve = {"decision": "approve", "owner_role": "IT-Leiter", "due_date": due}
    with TestClient(app_on(None)) as client:
        goal = client.put("/api/goal", json={"goal": "internal-improvement"}, headers=alice)
        assert goal.status_code == 200, goal.text
        thread_id = assessed(client, alice)["thread_id"]
        reviewing.accepted(
            client,
            thread_id,
            overrides=[S1_NOT_EVIDENCED],
            decisions={"S1": approve},
            headers=alice,
        )
        changed = client.put(
            f"/api/assessments/{thread_id}/remediation/S1/priority",
            json={"priority": "low"},
            headers=alice,
        )
        trail = client.get("/api/audit-trail", headers=alice).json()["entries"]

    assert changed.status_code == 200, changed.text
    assert changed.json()["item"]["missing"] == "nicht entworfen"
    assert trail[0]["note"] == "Priorität: Niedrig (vorher Hoch)."


def stored_as_english(user: dict[str, str], thread_id: str) -> None:
    """The Alpenwerk thread's state saying English, as an assessment kept before its answer
    language was stored reads when its profile can't be read (vouch.graph._profile_language)."""
    scope = Memory(InMemoryStore(), user["X-Test-User"], "alpenwerk").scope
    built = build_graph(None, checkpointer=process_checkpointer())
    built.update_state(thread_config(thread_id, scope), {"answer_language": "en"})


def test_an_item_reads_in_its_assessments_language_on_every_route(users):
    """One source: the working paper, the decision's answer and a plan route's answer agree, so
    the item never flips language after a priority change."""
    alice = under(users[0], "alpenwerk")
    due = (date.today() + timedelta(days=30)).isoformat()
    approve = {"decision": "approve", "owner_role": "IT-Leiter", "due_date": due}
    with TestClient(app_on(None)) as client:
        client.put("/api/goal", json={"goal": "internal-improvement"}, headers=alice)
        thread_id = assessed(client, alice)["thread_id"]
        reviewing.accepted(
            client,
            thread_id,
            overrides=[S1_NOT_EVIDENCED],
            decisions={"S1": approve},
            headers=alice,
        )
        stored_as_english(alice, thread_id)
        kept = reviewing.decide(client, thread_id, "S1", approve, alice)  # the same again
        changed = client.put(
            f"/api/assessments/{thread_id}/remediation/S1/priority",
            json={"priority": "low"},
            headers=alice,
        )
        shown = served_s1(client, alice, thread_id)

    assert changed.status_code == 200, changed.text
    assert [kept["missing"], changed.json()["item"]["missing"], shown["missing"]] == [
        graph.NOT_DRAFTED
    ] * 3


def test_the_german_no_owner_phrase_is_refused_as_an_owner_like_the_english_one(users):
    alice = under(users[0], "alpenwerk")
    due = (date.today() + timedelta(days=30)).isoformat()
    with TestClient(app_on(None)) as client:
        thread_id = assessed(client, alice)["thread_id"]
        assert reviewing.confirm(client, thread_id, [S1_NOT_EVIDENCED], alice).status_code == 200
        refused = [
            client.post(
                f"/api/assessments/{thread_id}/remediation/S1",
                json={"decision": "approve", "owner_role": owner, "due_date": due},
                headers=alice,
            )
            for owner in (graph.NO_OWNER, "keine verantwortliche Rolle vorgeschlagen")
        ]
        still = served_s1(client, alice, thread_id)

    assert [r.status_code for r in refused] == [422, 422]
    assert refused[1].json()["detail"] == refused[0].json()["detail"]
    assert still["status"] == "draft"


def test_a_drafts_item_keeps_the_no_owner_sentinel_and_says_not_drafted_in_its_language():
    german = replace(
        assessment("t1", {"M1": "not-evidenced"}),
        remediation=[graph.RemediationItem.not_drafted("M1", date(2026, 11, 9))],
        answer_language="de",
    )

    (item,) = shown_items(german, {})
    (as_english,) = shown_items(replace(german, answer_language="en"), {})

    assert (item["missing"], item["owner_role"]) == ("nicht entworfen", graph.NO_OWNER)
    assert (as_english["missing"], as_english["owner_role"]) == (graph.NOT_DRAFTED, graph.NO_OWNER)
