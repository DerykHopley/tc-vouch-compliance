"""The readiness report (#234): GET /api/report, under the request's compliance profile, in the
profile's language, built from the reviewed verdicts, the lead's remediation decisions, the
profile's not-applicable entries and the audit trail; and the German demo script that lets the
fake model assess a German profile.

Through the HTTP API, on the bundled fake model, on both backends where a case reads stored
data: in memory (no database URL) and Postgres (CI's `database` job gives a server through
VOUCH_TEST_DATABASE_URL; otherwise those are skipped). The user is set per request by a test
header (test_memory's).

The demo's 4.1.2 run (English for Nordlicht, German for Alpenwerk) judges M1 partly evidenced and
the rest evidenced, and drafts one item, for M1.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_api import read_events
from test_database import UNREACHABLE_URL, empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import seeded
from test_memory import app_on, restart

from vouch import api, audit, isa, report
from vouch.database import Database
from vouch.fake_model import ScriptedChatModel
from vouch.graph import Review
from vouch.language import audit_claim, written_in
from vouch.profile import load_named_profile
from vouch.settings import DEMO_SCRIPT

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users

DEMO_CONTROL = "4.1.2"
S1_NOT_EVIDENCED = {"requirement_id": "S1", "verdict": "not-evidenced", "reason": "No list."}


def under(user: dict[str, str], profile: str) -> dict[str, str]:
    return user | {api.PROFILE_HEADER: profile}


def assessed(client: TestClient, user: dict[str, str], control_id: str = DEMO_CONTROL) -> dict:
    """An assessment run up to the review: the proposed assessment, as the stream's result."""
    response = client.post("/api/assessments", json={"control_id": control_id}, headers=user)
    assert response.status_code == 200, response.text
    name, data = read_events(response)[-1]
    assert name == "result", data
    return data["assessment"]


def reviewed(
    client: TestClient,
    user: dict[str, str],
    control_id: str = DEMO_CONTROL,
    decision: str = "accept",
    **body: Any,
) -> str:
    thread_id = assessed(client, user, control_id)["thread_id"]
    response = client.post(
        f"/api/assessments/{thread_id}/review",
        json={"decision": decision, **body},
        headers=user,
    )
    assert response.status_code == 200, response.text
    return thread_id


def decide(client: TestClient, user: dict[str, str], thread_id: str, requirement: str, **body):
    response = client.post(
        f"/api/assessments/{thread_id}/remediation/{requirement}", json=body, headers=user
    )
    assert response.status_code == 200, response.text
    return response.json()["item"]


def report_of(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/report", headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def named(app: Any, name: str) -> None:
    """Local mode's one user's display name, from the next request on."""
    app.state.settings = replace(app.state.settings, local_user_name=name)


def control(shown: dict[str, Any], control_id: str) -> dict[str, Any]:
    return next(c for c in shown["controls"] if c["id"] == control_id)


def every_word(words: Any) -> list[str]:
    """Each text in a words table, however it nests."""
    if isinstance(words, str):
        return [words]
    if isinstance(words, dict):
        return [w for value in words.values() for w in every_word(value)]
    return [w for value in words for w in every_word(value)]


# --- Readiness: accepted verdicts only, as the overview counts them (story 3) ------------------


def test_readiness_counts_accepted_verdicts_only_and_equals_the_overviews(database_url, users):
    alice, _ = users
    with TestClient(app_on(database_url)) as client:
        reviewed(client, alice, "4.1.2", "accept")
        assessed(client, alice, "1.1.1")  # proposed, waiting for review
        reviewed(client, alice, "1.6.1", "reject")
        shown = report_of(client, alice)
        overview = client.get("/api/overview", headers=alice).json()

    assert shown["readiness"]["accepted"] == 1
    assert shown["readiness"]["open"] == 11
    assert shown["readiness"]["controls"] == 12
    counts = ("controls", "accepted", "by_verdict", "open")
    assert {k: shown["readiness"][k] for k in counts} == {
        k: overview["readiness"][k] for k in counts
    }
    # The overview counts 4.1.2's undecided M1 draft; the report counts what it lists: none.
    assert overview["readiness"]["remediation_items"] == 1
    assert shown["readiness"]["remediation_items"] == 0
    assert shown["remediation"] == []


# --- The controls: reviewed verdict, reviewer, date and quotes; open ones carry none (1, 2) ----


def test_an_accepted_control_carries_its_reviewed_verdict_reviewer_date_and_quotes(
    database_url, users
):
    alice, _ = users
    app = app_on(database_url)
    named(app, "Dana Lead")
    with TestClient(app) as client:
        before = datetime.now(UTC)
        reviewed(client, alice, "4.1.2", "accept")
        assessed(client, alice, "1.1.1")
        reviewed(client, alice, "1.6.1", "reject")
        shown = report_of(client, alice)

    accepted = control(shown, "4.1.2")
    assert accepted["status"] == "accepted"
    assert accepted["verdict"] == "partly-evidenced"
    assert accepted["reviewer"] == "Dana Lead"
    assert before <= datetime.fromisoformat(accepted["reviewed_at"]) <= datetime.now(UTC)
    quotes = [(q["doc_id"], q["version"], q["quote"]) for q in accepted["quotes"]]
    assert ("authentication-standard", "3.1", "Passwords have at least 12 characters.") in quotes
    assert len(quotes) == len(set(quotes))  # each once
    assert all(q["section"] for q in accepted["quotes"])
    # Each quote carries its evidence kind and whether it is stale, as the overview's do.
    assert {(q["kind"], q["stale"]) for q in accepted["quotes"]} == {("documented", False)}
    for open_id in ("1.1.1", "1.6.1", "1.6.2"):  # proposed, rejected, not assessed
        assert control(shown, open_id) | {"question": None} == {
            "id": open_id,
            "question": None,
            "status": "open",
            "verdict": None,
            "reviewer": None,
            "reviewed_at": None,
            "quotes": [],
        }
    assert [c["id"] for c in shown["controls"]] == [
        c.id for c in isa.load_base_framework().controls
    ]


def test_a_review_kept_without_a_name_shows_the_reviewers_id_and_with_neither_none():
    profile = load_named_profile("nordlicht")
    controls = profile.isa_edition().controls
    by_id = seeded("1.1.1", "evidenced", "accepted")
    by_id = replace(by_id, review=Review("accept", "", [], reviewer_id="user_42"))
    nobody = seeded("1.6.1", "evidenced", "accepted")

    shown = report.readiness_report(
        profile,
        controls,
        {"1.1.1": by_id, "1.6.1": nobody},
        decided={},
        trail=audit.Page([], None),
        today=date(2026, 11, 6),
    )

    assert control(shown, "1.1.1")["reviewer"] == "user_42"
    assert control(shown, "1.6.1")["reviewer"] is None
    assert shown["disclaimer"] == (
        "Drafted by an AI assistant, reviewed by user_42; not an audit result."
    )


# --- Remediation: approved items only, under their control (story 4) ---------------------------


def test_an_approved_item_is_listed_under_its_control_and_a_discarded_one_and_a_draft_are_not(
    database_url, users
):
    alice, _ = users
    due = (date.today() + timedelta(days=30)).isoformat()
    with TestClient(app_on(database_url)) as client:
        # M1's drafted item, and S1's "not drafted" one from the lead's override.
        thread = reviewed(client, alice, "4.1.2", "accept", overrides=[S1_NOT_EVIDENCED])
        decide(client, alice, thread, "M1", decision="approve", owner_role="CISO", due_date=due)
        decide(client, alice, thread, "S1", decision="discard")
        reviewed(client, alice, "1.1.1", "accept")  # its drafts stay drafts
        shown = report_of(client, alice)

    assert shown["remediation"] == [
        {
            "control_id": "4.1.2",
            "question": control(shown, "4.1.2")["question"],
            "items": [
                {
                    "requirement_id": "M1",
                    "missing": (
                        "A record that each system's exposure was assessed to choose its "
                        "sign-in method, with the date and who did it."
                    ),
                    "owner_role": "CISO",
                    "due_date": due,
                }
            ],
        }
    ]
    assert shown["readiness"]["remediation_items"] == 1  # the one item it lists


# --- Not applicable: each with the profile's reason (story 5) ---------------------------------


def test_nordlichts_report_lists_5_1_2_s3_as_not_applicable_and_alpenwerks_list_is_empty(users):
    alice, _ = users
    nordlicht = load_named_profile("nordlicht")
    with TestClient(app_on(None)) as client:
        english = report_of(client, under(alice, "nordlicht"))
        german = report_of(client, under(alice, "alpenwerk"))

    (entry,) = english["not_applicable"]
    assert (entry["control_id"], entry["requirement_id"]) == ("5.1.2", "S3")
    assert entry["reason"] == nordlicht.not_applicable[0].reason
    assert entry["reason"].startswith("Nordlicht allows no remote access")
    assert entry["text"]
    assert german["not_applicable"] == []
    assert german["words"]["sentences"]["no_not_applicable"]


# --- The audit trail, newest first, 100 at most (story 6) --------------------------------------


def test_the_trail_holds_each_accept_override_and_approval_newest_first(database_url, users):
    alice, _ = users
    due = (date.today() + timedelta(days=30)).isoformat()
    app = app_on(database_url)
    named(app, "Dana Lead")
    with TestClient(app) as client:
        thread = reviewed(
            client, alice, "4.1.2", "accept", note="Fine.", overrides=[S1_NOT_EVIDENCED]
        )
        decide(client, alice, thread, "M1", decision="approve", owner_role="CISO", due_date=due)
        shown = report_of(client, alice)
        other = report_of(client, under(alice, "alpenwerk"))

    entries = shown["trail"]["entries"]
    assert [(e["action"], e["requirement_id"]) for e in entries] == [
        ("approve", "M1"),
        ("accept", None),
        ("override", "S1"),
    ]
    accept = entries[1]
    assert accept["reviewer_name"] == "Dana Lead"
    assert accept["note"] == "Fine."
    assert accept["evidence"] == [{"doc_id": "authentication-standard", "version": "3.1"}]
    assert accept["model"] and accept["prompt_version"]
    assert (accept["verdict_before"], accept["verdict_after"]) == (
        "partly-evidenced",
        "partly-evidenced",
    )
    datetime.fromisoformat(accept["at"])
    assert shown["trail"]["next"] is None
    assert other["trail"] == {"entries": [], "next": None}  # another profile's trail


def entry(user_id: str, n: int) -> audit.AuditEntry:
    return audit.AuditEntry(
        id=None,
        user_id=user_id,
        reviewer_name="Dana Lead",
        at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=n),
        profile_id="nordlicht",
        control_id="4.1.2",
        thread_id=f"t{n}",
        action="accept",
        requirement_id=None,
        note=f"Entry {n}.",
        verdict_before="evidenced",
        verdict_after="evidenced",
        evidence=(),
        model="fake",
        prompt_version="v",
    )


def test_with_101_entries_the_trail_holds_the_newest_100_and_a_next(users):
    alice, _ = users
    restart()
    audit.process_trail().keep([entry(alice["X-Test-User"], n) for n in range(101)])
    with TestClient(app_on(None)) as client:
        shown = report_of(client, alice)
        rest = client.get(f"/api/audit-trail?before={shown['trail']['next']}", headers=alice).json()

    entries = shown["trail"]["entries"]
    assert len(entries) == audit.DEFAULT_PAGE == 100
    assert entries[0]["note"] == "Entry 100."
    assert shown["trail"]["next"] == entries[-1]["id"]
    assert [e["note"] for e in rest["entries"]] == ["Entry 0."]


# --- The disclaimer (story 7) -----------------------------------------------------------------


def test_with_nothing_accepted_the_report_says_nothing_has_been_reviewed(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        english = report_of(client, alice)
        german = report_of(client, under(alice, "alpenwerk"))

    assert english["readiness"]["accepted"] == 0
    assert english["readiness"]["open"] == 12
    assert {c["status"] for c in english["controls"]} == {"open"}
    assert english["remediation"] == []
    assert english["disclaimer"] == (
        "Drafted by an AI assistant; nothing has been reviewed yet; not an audit result."
    )
    assert german["disclaimer"] == (
        "Erstellt von einem KI-Assistenten; noch nichts geprüft; kein Auditergebnis."
    )
    sentences = english["words"]["sentences"]
    assert sentences["no_remediation"] and sentences["no_trail"]


def test_the_disclaimer_names_one_reviewer_once_and_two_joined_in_the_reports_language(users):
    alice, _ = users
    app = app_on(None)
    with TestClient(app) as client:
        named(app, "Dana Lead")
        reviewed(client, alice, "4.1.2")
        reviewed(client, alice, "1.1.1")
        reviewed(client, under(alice, "alpenwerk"), "4.1.2")
        one_en = report_of(client, alice)["disclaimer"]
        one_de = report_of(client, under(alice, "alpenwerk"))["disclaimer"]
        named(app, "Ola Berg")
        reviewed(client, alice, "1.6.1")
        reviewed(client, under(alice, "alpenwerk"), "5.1.1")  # names in the framework's order
        two_en = report_of(client, alice)["disclaimer"]
        two_de = report_of(client, under(alice, "alpenwerk"))["disclaimer"]

    assert one_en == "Drafted by an AI assistant, reviewed by Dana Lead; not an audit result."
    assert one_de == "Erstellt von einem KI-Assistenten, geprüft von Dana Lead; kein Auditergebnis."
    assert two_en == (
        "Drafted by an AI assistant, reviewed by Dana Lead and Ola Berg; not an audit result."
    )
    assert two_de == (
        "Erstellt von einem KI-Assistenten, geprüft von Dana Lead und Ola Berg; kein Auditergebnis."
    )


def test_three_names_are_listed_with_commas_then_the_languages_and():
    assert report.disclaimer(["A", "B", "C"], "en") == (
        "Drafted by an AI assistant, reviewed by A, B and C; not an audit result."
    )
    assert report.disclaimer(["A", "B", "C"], "de") == (
        "Erstellt von einem KI-Assistenten, geprüft von A, B und C; kein Auditergebnis."
    )


# --- The report's language (story 8, spec seam 2) ----------------------------------------------


def test_alpenwerks_report_assessed_and_accepted_on_the_fake_has_german_words(users):
    alice, _ = users
    alpenwerk = under(alice, "alpenwerk")
    with TestClient(app_on(None)) as client:
        reviewed(client, alpenwerk, "4.1.2")
        shown = report_of(client, alpenwerk)

    assert shown["head"]["answer_language"] == "de"
    assert written_in(" ".join(every_word(shown["words"]))) == "de"
    assert shown["words"]["verdicts"] == {
        "evidenced": "Nachgewiesen",
        "partly-evidenced": "Teilweise nachgewiesen",
        "not-evidenced": "Nicht nachgewiesen",
        "not-applicable": "Nicht anwendbar",
    }
    assert control(shown, "4.1.2")["verdict"] == "partly-evidenced"
    assert shown["readiness"]["accepted"] == 1


def test_the_english_words_are_designs_verdict_words_and_read_as_english():
    words = report.words_for("en")

    assert words["verdicts"] == {
        "evidenced": "Evidenced",
        "partly-evidenced": "Partly evidenced",
        "not-evidenced": "Not evidenced",
        "not-applicable": "Not applicable",
    }
    assert written_in(" ".join(every_word(words))) == "en"
    assert len(every_word(words)) == len(every_word(report.words_for("de")))  # one table shape


def test_a_stale_quote_has_its_flag_word_in_each_language():
    # #56: the page's stale flag, and later the Markdown export's, in the report's language.
    assert report.words_for("en")["labels"]["stale"] == "Stale"
    assert report.words_for("de")["labels"]["stale"] == "Prüfung überfällig"


@pytest.mark.parametrize("language", ["en", "de"])
def test_no_word_of_either_table_claims_an_audit_result(language):
    claims = {w: audit_claim(w) for w in every_word(report.words_for(language))}

    assert {w: c for w, c in claims.items() if c} == {}


def test_a_served_reports_own_words_claim_no_audit_result(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        reviewed(client, alice)
        reviewed(client, under(alice, "alpenwerk"))
        served = [report_of(client, alice), report_of(client, under(alice, "alpenwerk"))]

    for shown in served:
        own = [*every_word(shown["words"]), shown["disclaimer"]]
        assert [w for w in own if audit_claim(w)] == []


def test_a_profile_answering_in_lithuanian_gets_the_english_words():
    lithuanian = load_named_profile("nordlicht").model_copy(update={"answer_language": "lt"})

    shown = report.readiness_report(
        lithuanian,
        lithuanian.isa_edition().controls,
        {},
        decided={},
        trail=audit.Page([], None),
        today=date(2026, 11, 6),
    )

    assert report.words_for("lt") == report.words_for("en")
    assert shown["disclaimer"].startswith("Drafted by an AI assistant")
    # The answers stay Lithuanian; the report's own words, and so its lang, are English.
    assert (shown["head"]["answer_language"], shown["head"]["words_language"]) == ("lt", "en")


# --- The head ----------------------------------------------------------------------------------


def test_the_head_names_the_company_framework_level_overlays_language_and_todays_date(
    users, monkeypatch
):
    alice, _ = users
    monkeypatch.setattr(report, "today", lambda: date(2026, 11, 6))  # the server's UTC date
    with TestClient(app_on(None)) as client:
        english = report_of(client, alice)["head"]
        german = report_of(client, under(alice, "alpenwerk"))["head"]

    assert english == {
        "company": "Nordlicht Komponenten GmbH",
        "country": "Germany",
        "base_framework": "VDA ISA 2027.1",
        "protection_level": "normal",
        "overlays": [],
        "answer_language": "en",
        "words_language": "en",
        "date": "2026-11-06",
    }
    assert german["overlays"] == ["AT NIS2 (illustrative)"]
    assert german["words_language"] == "de"
    assert german["protection_level"] == "high"


# --- Failures ----------------------------------------------------------------------------------


def test_an_unknown_profile_is_a_404(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.get("/api/report", headers=under(alice, "nowhere"))

    assert response.status_code == 404
    assert response.json()["detail"] == api.UNKNOWN_PROFILE


def test_a_broken_german_edition_is_a_plain_500_on_the_report(users, monkeypatch, tmp_path):
    alice, _ = users
    alpenwerk = under(alice, "alpenwerk")
    broken = tmp_path / "controls-de.yaml"
    broken.write_text("controls: [\n", encoding="utf-8")
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        report_of(client, alpenwerk)  # the profile is read while its edition is whole
        monkeypatch.setattr(isa, "CONTROLS_DE_FILE", broken)
        response = client.get("/api/report", headers=alpenwerk)

    assert response.status_code == 500
    assert response.json()["detail"] == api.REPORT_FAILED
    assert str(tmp_path) not in response.text


def test_with_the_database_down_the_report_answers_503(users):
    alice, _ = users
    app = app_on(SecretStr(UNREACHABLE_URL))
    app.state.database = Database(SecretStr(UNREACHABLE_URL), timeout=0.3)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/report", headers=alice)

    assert response.status_code == 503
    assert response.json()["unavailable"] == "database"


# --- A German profile on the fake model ---------------------------------------------------------


def test_alpenwerk_assessed_on_the_fake_waits_for_review_with_a_german_answer(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        german = assessed(client, under(alice, "alpenwerk"))
        english = assessed(client, alice)

    assert german["status"] == "waiting-for-review"
    assert written_in(german["answer"]) == "de"
    assert not [s for s in german["steps"] if "written in" in s["text"]]
    assert german["warnings"] == []
    assert [r["verdict"] for r in german["requirements"] if r["id"] in ("M1", "S2")] == [
        "partly-evidenced",
        "evidenced",
    ]
    assert all(r["citations"] for r in german["requirements"])  # each German quote was found
    demo = ScriptedChatModel.from_file(DEMO_SCRIPT).script
    assert english["answer"] == demo.gather[-1].answer
