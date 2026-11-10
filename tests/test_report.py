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

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import Any, Literal

import pytest
import reviewing
import test_memory
from fastapi.testclient import TestClient
from profiles_dir import SECOND, make_profiles
from pydantic import SecretStr
from pypdf import PdfReader
from test_api import read_events
from test_database import UNREACHABLE_URL, empty_database, needs_postgres  # noqa: F401 (a fixture)
from test_dossier import WHOLE_REASON, WITHOUT_1_1_1, seeded
from test_memory import app_on, restart

from vouch import api, audit, goal, isa, report
from vouch.database import Database
from vouch.dossier import overview
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
    decision: Literal["accept", "reject"] = "accept",
    **options: Any,
) -> str:
    """The control assessed and reviewed through the three steps (reviewing.reviewed's
    `options`); an Accept discards the items `decisions` leaves, so only those approved there
    are listed. Its thread id."""
    thread_id = assessed(client, user, control_id)["thread_id"]
    reviewing.reviewed(client, thread_id, decision, items="discard", headers=user, **options)
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
        reviewed(client, alice, "4.1.2", "accept", decisions={"M1": {"decision": "approve"}})
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
    # 4.1.2's approved M1 item, on both; nothing of the controls not accepted.
    assert overview["readiness"]["remediation_items"] == 1
    assert shown["readiness"]["remediation_items"] == 1
    assert [g["control_id"] for g in shown["remediation"]] == ["4.1.2"]


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


def test_a_control_not_applicable_as_a_whole_is_out_of_readiness_and_the_controls_table():
    # #454: the overview's readiness, no row in the controls table, its requirements still
    # listed as not applicable with the reason.
    controls = WITHOUT_1_1_1.isa_edition().controls
    latest = {
        "1.1.1": seeded("1.1.1", "evidenced", "accepted"),
        "1.6.1": seeded("1.6.1", "evidenced", "accepted"),
    }

    shown = report.readiness_report(
        WITHOUT_1_1_1,
        controls,
        latest,
        decided={},
        trail=audit.Page([], None),
        today=date(2026, 11, 6),
    )

    standing = overview(WITHOUT_1_1_1, controls, latest)["readiness"]
    assert {k: shown["readiness"][k] for k in ("controls", "accepted", "open", "by_verdict")} == {
        k: standing[k] for k in ("controls", "accepted", "open", "by_verdict")
    }
    assert shown["readiness"]["controls"] == len(controls) - 1
    assert shown["readiness"]["accepted"] == 1
    assert "1.1.1" not in [row["id"] for row in shown["controls"]]
    listed = [n for n in shown["not_applicable"] if n["control_id"] == "1.1.1"]
    assert listed and all(n["reason"] == WHOLE_REASON for n in listed)


# --- Remediation: approved items only, under their control (story 4) ---------------------------


def test_an_approved_item_is_listed_under_its_control_and_a_discarded_one_and_a_draft_are_not(
    database_url, users
):
    alice, _ = users
    due = (date.today() + timedelta(days=30)).isoformat()
    with TestClient(app_on(database_url)) as client:
        # M1's drafted item, and S1's "not drafted" one from the lead's override.
        reviewed(
            client,
            alice,
            "4.1.2",
            "accept",
            overrides=[S1_NOT_EVIDENCED],
            decisions={
                "M1": {"decision": "approve", "owner_role": "CISO", "due_date": due},
                "S1": {"decision": "discard"},
            },
        )
        reviewed(client, alice, "1.1.1", "accept")  # its items discarded
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
        reviewed(
            client,
            alice,
            "4.1.2",
            "accept",
            note="Fine.",
            overrides=[S1_NOT_EVIDENCED],
            decisions={"M1": {"decision": "approve", "owner_role": "CISO", "due_date": due}},
        )  # S1's item discarded before the Accept
        shown = report_of(client, alice)
        other = report_of(client, under(alice, "alpenwerk"))

    entries = shown["trail"]["entries"]
    assert [(e["action"], e["requirement_id"]) for e in entries] == [
        ("accept", None),
        ("discard", "S1"),
        ("approve", "M1"),
        ("confirm", None),
        ("override", "S1"),
    ]
    accept = entries[0]
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
    assert german["overlays"] == ["AT NIS2 (illustrativ)"]  # written in German (#379)
    assert german["country"] == "Österreich"  # the stored "Austria", in the report's words
    assert german["words_language"] == "de"
    assert german["protection_level"] == "high"


def test_a_country_the_words_dont_name_shows_as_stored_never_blank():
    # #379: the stored name is English by design (the top bar finds its ISO code from it).
    assert report.country_in("Austria", "de") == "Österreich"
    assert report.country_in("Germany", "de") == "Deutschland"
    assert report.country_in("Atlantis", "de") == "Atlantis"
    assert report.country_in("Austria", "en") == "Austria"
    assert report.country_in("Austria", "fr") == "Austria"  # a language with no words: English


# --- Failures ----------------------------------------------------------------------------------


def unreadable_trail(*args: Any, **kwargs: Any) -> Any:
    """A trail read that fails: answered as an unexpected 500, not the edition's."""
    raise RuntimeError("the trail was read before the standing")


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
        # The standing is read before the trail, so the edition's 500 is the one answered.
        monkeypatch.setattr(audit, "read", unreadable_trail)
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


# --- The Markdown export (#57, story 9) ---------------------------------------------------------


def export_of(client: TestClient, user: dict[str, str]) -> str:
    response = client.get("/api/report/markdown", headers=user)
    assert response.status_code == 200, response.text
    return response.text


def test_the_export_is_a_markdown_attachment_named_for_the_profile_and_the_day(users, monkeypatch):
    alice, _ = users
    monkeypatch.setattr(report, "today", lambda: date(2026, 11, 6))
    with TestClient(app_on(None)) as client:
        response = client.get("/api/report/markdown", headers=under(alice, "alpenwerk"))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="readiness-report-alpenwerk-2026-11-06.md"; '
        "filename*=UTF-8''readiness-report-alpenwerk-2026-11-06.md"
    )
    assert response.text.startswith("# Bereitschaftsbericht\n")


_CELL_EDGE = re.compile(r"(?<!\\)\|")  # a cell's edge: a `|` not escaped


def section(md: str, heading: str) -> str:
    """The Markdown from the `## heading` up to the next `## ` heading or the closing rule."""
    rest = md[md.index(f"\n## {heading}\n") + 1 :]
    ends = [i for i in (rest.find("\n## ", 1), rest.find("\n---\n")) if i > 0]
    return rest[: min(ends)] if ends else rest


def table_rows(text: str) -> list[list[str]]:
    """The first table's rows in the text, its header and divider left out: each row's cells."""
    table: list[str] = []
    for line in text.splitlines():
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    assert table, text
    return [[c.strip() for c in _CELL_EDGE.split(row)[1:-1]] for row in table[2:]]


def flat(text: str) -> str:
    """The text on one line, list indents and a Markdown quote's marks left out."""
    return " ".join(re.sub(r"\n\s*(?:> ?)?", " ", text).split())


def carried(shown: dict[str, Any]) -> list[str]:
    """The texts the report carries from elsewhere, which stay as written."""
    texts = [c["question"] for c in shown["controls"]]
    texts += [q["quote"] for c in shown["controls"] for q in c["quotes"]]
    texts += [i["missing"] for g in shown["remediation"] for i in g["items"]]
    texts += [x for r in shown["not_applicable"] for x in (r["text"], r["reason"])]
    texts += [e["note"] for e in shown["trail"]["entries"]]
    return texts


def seed_nordlicht(client: TestClient, user: dict[str, str]) -> None:
    """4.1.2 accepted with S1 overridden (M1 approved, S1 discarded), 1.1.1 accepted."""
    due = (date.today() + timedelta(days=30)).isoformat()
    reviewed(
        client,
        user,
        "4.1.2",
        "accept",
        note="Fine.",
        overrides=[S1_NOT_EVIDENCED],
        decisions={
            "M1": {"decision": "approve", "owner_role": "CISO", "due_date": due},
            "S1": {"decision": "discard"},
        },
    )
    reviewed(client, user, "1.1.1", "accept")


def test_the_export_holds_the_same_standing_as_the_served_report(database_url, users):
    alice, _ = users
    app = app_on(database_url)
    named(app, "Dana Lead")
    with TestClient(app) as client:
        seed_nordlicht(client, alice)
        shown = report_of(client, alice)
        md = export_of(client, alice)

    words = shown["words"]
    r = shown["readiness"]
    readiness = section(md, "Readiness")
    assert f"{r['accepted']} of {r['controls']} controls accepted" in readiness
    for verdict, n in r["by_verdict"].items():
        assert f"- {n} {words['verdicts'][verdict]}" in readiness
    assert f"- {r['open']} Open" in readiness
    assert f"- {report.remediation_count(r['remediation_items'], words)}" in readiness

    rows = {row[0]: row for row in table_rows(section(md, "Controls"))}
    assert list(rows) == [c["id"] for c in shown["controls"]]
    for c in shown["controls"]:
        row = rows[c["id"]]
        if c["status"] == "accepted":
            day = datetime.fromisoformat(c["reviewed_at"]).astimezone(UTC).date().isoformat()
            assert row[2:] == ["Accepted", words["verdicts"][c["verdict"]], "Dana Lead", day]
            assert c["quotes"]
            for q in c["quotes"]:
                assert flat(q["quote"]) in flat(md)
                assert f"{q['doc_id']} v{q['version']} · {q['section']}" in md
        else:
            assert row[2:4] == ["Open", words["sentences"]["open_control"]]
    assert sum(c["status"] == "accepted" for c in shown["controls"]) == 2

    items = section(md, "Remediation items")
    (group,) = shown["remediation"]
    (item,) = group["items"]
    assert f"### {group['control_id']} " in items
    assert table_rows(items) == [["M1", item["missing"], "CISO", item["due_date"]]]  # not S1

    (excluded,) = shown["not_applicable"]
    assert flat(excluded["reason"]) in flat(section(md, "Not applicable"))

    trail = table_rows(section(md, "Audit trail"))
    entries = shown["trail"]["entries"]
    # 4.1.2: override, confirm, approve, discard, accept; 1.1.1: confirm, two discards, accept.
    assert len(trail) == len(entries) == 9
    for row, e in zip(trail, entries, strict=True):
        action = words["actions"][e["action"]]
        assert row[1:5] == [e["reviewer_name"], e["control_id"], e["requirement_id"] or "", action]
        assert row[7] == e["note"]
    assert md.rstrip().endswith(shown["disclaimer"])


def test_the_export_holds_every_trail_entry_newest_first_not_a_page(users):
    alice, _ = users
    restart()
    audit.process_trail().keep([entry(alice["X-Test-User"], n) for n in range(101)])
    with TestClient(app_on(None)) as client:
        md = export_of(client, alice)

    notes = [row[7] for row in table_rows(section(md, "Audit trail"))]
    assert len(notes) == audit.DEFAULT_PAGE + 1 == 101
    assert notes == [f"Entry {n}." for n in range(100, -1, -1)]


def test_alpenwerks_export_after_an_accept_on_the_german_fake_is_in_german(users):
    alpenwerk = under(users[0], "alpenwerk")
    with TestClient(app_on(None)) as client:
        reviewed(client, alpenwerk, "4.1.2")
        shown = report_of(client, alpenwerk)
        md = export_of(client, alpenwerk)

    assert md.startswith("# Bereitschaftsbericht\n")
    row = next(r for r in table_rows(section(md, "Kontrollen")) if r[0] == "4.1.2")
    assert row[2:4] == ["Akzeptiert", "Teilweise nachgewiesen"]
    assert md.rstrip().endswith(
        "Erstellt von einem KI-Assistenten, geprüft von Local user; kein Auditergebnis."
    )
    assert shown["disclaimer"] in md


def test_a_profile_without_a_words_table_exports_in_english(users, tmp_path):
    alice, _ = users
    app = app_on(None)
    folder = make_profiles(tmp_path, broken=False, language="lt")
    app.state.settings = replace(app.state.settings, profiles_dir=folder)
    with TestClient(app) as client:
        md = export_of(client, under(alice, SECOND))

    assert md.startswith("# Readiness report\n")
    assert "- **Language:** lt" in md  # no word for Lithuanian: its code
    assert md.rstrip().endswith(report.words_for("en")["sentences"]["disclaimer_nothing_reviewed"])


def test_with_nothing_accepted_the_export_has_the_empty_list_sentences(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        md = export_of(client, alice)

    sentences = report.words_for("en")["sentences"]
    assert "0 of 12 controls accepted" in md
    assert sentences["no_remediation"] in section(md, "Remediation items")
    assert sentences["no_trail"] in section(md, "Audit trail")
    assert sentences["no_overlays"] in md
    assert md.rstrip().endswith(sentences["disclaimer_nothing_reviewed"])
    assert "### Quotes" not in md


def test_a_note_with_a_bar_and_a_line_break_keeps_the_trail_tables_shape():
    profile = load_named_profile("nordlicht")
    noted = replace(entry("u", 1), id=2, note="Checked | twice\nby phone.")
    built = report.readiness_report(
        profile,
        profile.isa_edition().controls,
        {},
        decided={},
        trail=audit.Page([noted, replace(entry("u", 0), id=1)], None),
        today=date(2026, 11, 6),
    )

    md = report.markdown(built, report.words_for("en"))

    trail = section(md, "Audit trail")
    rows = table_rows(trail)
    assert [len(row) for row in rows] == [11, 11]
    assert rows[0][7] == "Checked \\| twice by phone."
    header = next(line for line in trail.splitlines() if line.startswith("|"))
    assert len(_CELL_EDGE.split(header)) - 2 == 11


GOAL_SENTENCES = {
    "en": "Goal changed from Audit readiness to Internal improvement",
    "de": "Ziel geändert von Audit-Bereitschaft zu Interner Verbesserung",
}


def goal_change(n: int = 1) -> audit.AuditEntry:
    """A goal change's entry (#369), as vouch.goal writes it."""
    return goal.goal_entry(
        audit.Reviewer("u", "Dana Lead"),
        "nordlicht",
        "audit-readiness",
        "internal-improvement",
        datetime(2026, 11, 9, 10, 30, tzinfo=UTC) + timedelta(minutes=n),
    )


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_goal_changes_trail_row_says_the_change_in_the_reports_words(language):
    built = built_report(entries=[replace(goal_change(), id=1)])

    rows = report.trail_rows(built["trail"]["entries"], report.words_for(language))

    assert rows == [
        [
            "2026-11-09 10:31 UTC",
            "Dana Lead",
            "",
            "",
            GOAL_SENTENCES[language],
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    ]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_markdown_export_shows_a_goal_change_with_its_control_cells_empty(language):
    built = built_report(entries=[replace(goal_change(), id=2), replace(entry("u", 0), id=1)])

    md = report.markdown(built, report.words_for(language))

    goal_row, accept_row = table_rows(section(md, report.words_for(language)["labels"]["trail"]))
    assert goal_row[4] == GOAL_SENTENCES[language]
    assert [goal_row[i] for i in (2, 3, 5, 6, 7, 8, 9, 10)] == [""] * 8
    assert accept_row[4] == report.words_for(language)["actions"]["accept"]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_reports_words_name_both_goals(language):
    words = report.words_for(language)

    assert set(words["goals"]) == {"audit-readiness", "internal-improvement"}
    assert "{before}" in words["actions"]["goal"] and "{after}" in words["actions"]["goal"]


@pytest.mark.parametrize("profile", ["nordlicht", "alpenwerk"])
def test_the_generated_markdown_claims_no_audit_result(users, profile):
    lead = under(users[0], profile)
    with TestClient(app_on(None)) as client:
        reviewed(client, lead, "4.1.2", note="Reviewed with the CISO.")
        reviewed(client, lead, "1.1.1")
        shown = report_of(client, lead)
        md = export_of(client, lead)

    assert shown["readiness"]["accepted"] == 2
    assert audit_claim(md, carried(shown)) is None


def test_an_unknown_profile_is_a_404_on_the_export(users):
    with TestClient(app_on(None)) as client:
        response = client.get("/api/report/markdown", headers=under(users[0], "nowhere"))

    assert response.status_code == 404
    assert response.json()["detail"] == api.UNKNOWN_PROFILE


def test_a_broken_german_edition_is_a_plain_500_with_export_failed(users, monkeypatch, tmp_path):
    alpenwerk = under(users[0], "alpenwerk")
    broken = tmp_path / "controls-de.yaml"
    broken.write_text("controls: [\n", encoding="utf-8")
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        export_of(client, alpenwerk)  # the profile is read while its edition is whole
        monkeypatch.setattr(isa, "CONTROLS_DE_FILE", broken)
        # The standing is read before the trail, so the edition's 500 is the one answered.
        monkeypatch.setattr(audit, "read_all", unreadable_trail)
        response = client.get("/api/report/markdown", headers=alpenwerk)

    assert response.status_code == 500
    assert response.json()["detail"] == api.EXPORT_FAILED
    assert api.EXPORT_FAILED == (
        "The readiness report could not be exported just now. Please try again."
    )
    assert str(tmp_path) not in response.text


def test_with_the_database_down_the_export_answers_503(users):
    app = app_on(SecretStr(UNREACHABLE_URL))
    app.state.database = Database(SecretStr(UNREACHABLE_URL), timeout=0.3)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/report/markdown", headers=users[0])

    assert response.status_code == 503
    assert response.json()["unavailable"] == "database"


# --- Review fixes (#57): the file's name, carried text, the whole trail's paging ---------------


def test_a_name_outside_ascii_downloads_under_a_safe_name_and_its_own():
    # vouch.profile.PROFILE_NAME keeps today's profile ids to ASCII slugs, so this is checked
    # on the header alone: a wider id rule later can't break it.
    header = api.attachment('readiness-report-jürgen-žal"o-2026-11-06.md')

    assert header == (
        'attachment; filename="readiness-report-jurgen-zal-o-2026-11-06.md"; '
        "filename*=UTF-8''readiness-report-j%C3%BCrgen-%C5%BEal%22o-2026-11-06.md"
    )
    header.encode("latin-1")  # a header Starlette can send


def test_an_attachments_header_holds_no_line_break_or_bare_quote():
    header = api.attachment('a"\r\nb.md')

    assert "\r" not in header and "\n" not in header
    assert header == "attachment; filename=\"a-b.md\"; filename*=UTF-8''a%22%0D%0Ab.md"


def built_report(**trail: Any) -> dict[str, Any]:
    """Nordlicht's report with nothing accepted, drawn up on 2026-11-06, as served."""
    profile = load_named_profile("nordlicht")
    return dict(
        report.readiness_report(
            profile,
            profile.isa_edition().controls,
            {},
            decided={},
            trail=audit.Page(trail.get("entries", []), None),
            today=date(2026, 11, 6),
        )
    )


def test_carried_text_becomes_no_heading_and_no_live_html():
    built = built_report(
        entries=[replace(entry("u", 0), id=1, note="<img src=x onerror=alert(1)>")]
    )
    built["head"] = built["head"] | {"company": "Evil GmbH\n# Injected heading"}
    built["not_applicable"] = [
        {
            "control_id": "5.1.2",
            "requirement_id": "S3",
            "text": "Kept <b>bold</b>\n# Not a heading",
            "reason": "R&D only\n\n## Nor this",
        }
    ]
    first = built["controls"][0]
    built["controls"][0] = first | {
        "status": "accepted",
        "verdict": "evidenced",
        "reviewer": "Dana Lead",
        "reviewed_at": "2026-11-05T10:00:00+00:00",
        "quotes": [
            {
                "doc_id": "policy",
                "version": "2",
                "section": "1",
                "quote": "<script>x()</script>\n# second line",
                "kind": "documented",
                "stale": False,
            }
        ],
    }

    md = report.markdown(built, report.words_for("en"))

    headings = [line for line in md.splitlines() if line.startswith("#")]
    assert [h for h in headings if "Injected" in h or "Not a heading" in h or "Nor this" in h] == []
    assert "<" not in md  # every tag escaped (a `>` is a blockquote's own)
    assert "- **Company:** Evil GmbH # Injected heading" in md
    assert "Kept &lt;b&gt;bold&lt;/b&gt; # Not a heading" in md
    assert "R&amp;D only  ## Nor this" in md
    assert table_rows(section(md, "Audit trail"))[0][7] == "&lt;img src=x onerror=alert(1)&gt;"
    # A quote stays a blockquote, line by line as written.
    assert "> &lt;script&gt;x()&lt;/script&gt;\n> # second line\n>\n> — policy v2 · 1" in md


def test_a_backslash_before_a_bar_survives_in_a_table_cell():
    built = built_report(entries=[replace(entry("u", 0), id=1, note="a\\|b and c\\")])

    md = report.markdown(built, report.words_for("en"))

    (row,) = table_rows(section(md, "Audit trail"))
    assert len(row) == 11
    # Each `\` and `|` escaped: a Markdown viewer shows `a\|b and c\`, as written.
    assert row[7] == "a\\\\\\|b and c\\\\"


def test_the_whole_trail_is_read_page_by_page_without_a_gap_or_a_repeat(users, monkeypatch):
    alice, _ = users
    restart()
    audit.process_trail().keep([entry(alice["X-Test-User"], n) for n in range(20)])
    monkeypatch.setattr(audit, "MAX_PAGE", 7)
    reads = []
    entries = audit.ProcessTrail.entries

    def counted(self: audit.ProcessTrail, *args: Any, **kwargs: Any) -> list[audit.AuditEntry]:
        reads.append(kwargs)
        return entries(self, *args, **kwargs)

    monkeypatch.setattr(audit.ProcessTrail, "entries", counted)

    every = audit.read_all(audit.process_trail(), alice["X-Test-User"], "nordlicht")

    assert [e.note for e in every] == [f"Entry {n}." for n in range(19, -1, -1)]
    assert len({e.id for e in every}) == 20
    assert len(reads) == 3  # 7, 7 and 6
    assert {r["limit"] for r in reads} == {8}  # a page of 7, and one more to see if there's next


@pytest.mark.parametrize(
    ("lang", "one", "many"),
    [("en", "1 Remediation item", "2 Remediation items"), ("de", "1 Maßnahme", "2 Maßnahmen")],
)
def test_the_export_counts_one_remediation_item_in_the_singular(lang, one, many):
    words = report.words_for(lang)
    readiness = {
        "accepted": 1,
        "controls": 12,
        "open": 11,
        "by_verdict": {},
        "remediation_items": 1,
    }
    assert report.remediation_count(1, words) == one
    assert report.remediation_count(2, words) == many
    assert f"- {one}\n" in report._md_readiness(readiness, words) + "\n"


# --- The PDF export (#308): the same report, built with fpdf2 (vouch.report_pdf) --------------


def _page_lines(data: bytes) -> list[list[str]]:
    return [page.extract_text().splitlines() for page in PdfReader(BytesIO(data)).pages]


def pdf_pages(data: bytes) -> list[str]:
    """Each page's body text, read back with pypdf, its whitespace collapsed to single spaces:
    the letterhead and footer (#352) left out, the first and last lines fpdf2 draws on a page."""
    pages = []
    for lines in _page_lines(data):
        assert lines[0].startswith("VOUCH"), lines[0]  # the letterhead, drawn first
        assert re.search(r"\d+ (of|von) \d+$", lines[-1]), lines[-1]  # the footer, drawn last
        pages.append(" ".join(" ".join(lines[1:-1]).split()))
    return pages


def pdf_letterheads(data: bytes) -> list[tuple[str, str]]:
    """Each page's letterhead and footer line (#352), whitespace collapsed."""
    return [(" ".join(ls[0].split()), " ".join(ls[-1].split())) for ls in _page_lines(data)]


def pdf_text(data: bytes) -> str:
    return " ".join(pdf_pages(data))


def test_the_pdf_export_is_a_pdf_attachment_named_for_the_profile_and_the_day(users, monkeypatch):
    monkeypatch.setattr(report, "today", lambda: date(2026, 11, 6))
    with TestClient(app_on(None)) as client:
        response = client.get("/api/report/pdf", headers=under(users[0], "alpenwerk"))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="readiness-report-alpenwerk-2026-11-06.pdf"; '
        "filename*=UTF-8''readiness-report-alpenwerk-2026-11-06.pdf"
    )
    assert response.content.startswith(b"%PDF-")
    assert pdf_text(response.content).startswith("BEREITSCHAFTSBERICHT")


def test_a_note_with_an_emoji_still_gives_the_pdf(users):
    alpenwerk = under(users[0], "alpenwerk")
    with TestClient(app_on(None)) as client:
        reviewed(client, alpenwerk, "4.1.2", note="Passt 🙂 – „gut“.")
        response = client.get("/api/report/pdf", headers=alpenwerk)

    assert response.status_code == 200, response.text
    assert "Passt – „gut“." in pdf_text(response.content)


def test_an_unknown_profile_is_a_404_on_the_pdf_export(users):
    with TestClient(app_on(None)) as client:
        response = client.get("/api/report/pdf", headers=under(users[0], "nowhere"))

    assert response.status_code == 404
    assert response.json()["detail"] == api.UNKNOWN_PROFILE


def test_a_broken_german_edition_is_a_plain_500_with_export_failed_on_the_pdf(
    users, monkeypatch, tmp_path
):
    alpenwerk = under(users[0], "alpenwerk")
    broken = tmp_path / "controls-de.yaml"
    broken.write_text("controls: [\n", encoding="utf-8")
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        export_of(client, alpenwerk)  # the profile is read while its edition is whole
        monkeypatch.setattr(isa, "CONTROLS_DE_FILE", broken)
        # The standing is read before the trail, so the edition's 500 is the one answered.
        monkeypatch.setattr(audit, "read_all", unreadable_trail)
        response = client.get("/api/report/pdf", headers=alpenwerk)

    assert response.status_code == 500
    assert response.json()["detail"] == api.EXPORT_FAILED
    assert str(tmp_path) not in response.text


def test_with_the_database_down_the_pdf_export_answers_503(users):
    app = app_on(SecretStr(UNREACHABLE_URL))
    app.state.database = Database(SecretStr(UNREACHABLE_URL), timeout=0.3)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/report/pdf", headers=users[0])

    assert response.status_code == 503
    assert response.json()["unavailable"] == "database"
