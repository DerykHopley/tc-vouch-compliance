"""The profile pass of a company made in setup, on the fake model (#446): the library script
(LIBRARY_PASS_SCRIPT) drafts every applicable control from the sample library, limited to the
documents the company chose (chosen_documents_only).

Offline: no key and no ASSESS_FAKE_SCRIPT, unless a test sets one. Through POST /api/profile-pass
on the in-memory backend, the company made by the developer route; staleness pinned to the
expected answers' `as_of`, as test_fake_covers pins it.
"""

import datetime
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.store.memory import InMemoryStore
from test_memory import as_user, restart

from vouch import graph, load_named_profile, profile_pass
from vouch.api import PROFILE_HEADER, create_app, current_user
from vouch.company_profiles import CompanySetup, compliance_profile_of
from vouch.fake_model import ScriptedChatModel, chosen_documents_only
from vouch.graph import claims_audit_result, memory_checkpointer
from vouch.memory import Memory
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.profile import read_profile_once
from vouch.profile_pass import ProfilePassReply, run_profile_pass
from vouch.settings import LIBRARY_PASS_SCRIPT, PROFILE_PASS_SCRIPT, PROFILE_PASS_TOKENS, Settings

PASS = "/api/profile-pass"
DROPPED = "Dropped a citation"  # a guard step's text, as test_fake_covers reads it
EXPECTED = yaml.safe_load((SAMPLE_LIBRARY_DIR / "expected-answers.yaml").read_text())
POLICIES = sorted(p.stem for p in (SAMPLE_LIBRARY_DIR / "policies").glob("*.md"))
RECORDS = sorted(p.stem for p in (SAMPLE_LIBRARY_DIR / "records").glob("*.md"))
LIBRARY = {*POLICIES, *RECORDS}
NONE_CHOSEN = "None of the chosen documents shows this."
MISSING = "A document that shows this; none of the chosen documents does."
OWNER = "Information security officer"
COMPANY = {
    "company": "Beispiel Werke GmbH",
    "country": "Germany",
    "answer_language": "en",
    "protection_level": "high",
    "evidence": {"policies": POLICIES, "records": RECORDS},
}


def expected_verdicts() -> dict[str, dict[str, str]]:
    return {
        c["id"]: {r["id"]: r["verdict"] for r in c["requirements"]} for c in EXPECTED["controls"]
    }


def expected_cites() -> dict[str, dict[str, list[str]]]:
    return {c["id"]: {r["id"]: r["cites"] for r in c["requirements"]} for c in EXPECTED["controls"]}


@pytest.fixture(autouse=True)
def as_of(monkeypatch) -> datetime.date:
    """The pass judges staleness on the expected answers' `as_of`, not on the real date."""
    day = EXPECTED["as_of"]

    class AsOf(datetime.date):
        @classmethod
        def today(cls) -> "AsOf":
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr(profile_pass, "date", AsOf)
    monkeypatch.setattr(graph, "date", AsOf)
    return day


@pytest.fixture(autouse=True)
def _fresh_process():
    restart()
    read_profile_once.cache_clear()
    yield
    restart()


def developer() -> dict[str, str]:
    return {"X-Test-User": f"dev_{uuid.uuid4().hex[:8]}"}


def setup_pass(company: dict[str, Any], settings: Settings | None = None) -> list[dict]:
    """Make the company through the developer route, run its pass, and every new assessment as
    the API sends it."""
    read_profile_once.cache_clear()
    app = create_app(settings or Settings())  # no key: the fake model
    app.dependency_overrides[current_user] = as_user
    user = developer()
    with TestClient(app) as client:
        made = client.post("/api/developer/profiles", json=company, headers=user)
        assert made.status_code == 201, made.text
        under = user | {PROFILE_HEADER: made.json()["id"]}
        ran = client.post(PASS, headers=under)
        assert ran.status_code == 200, ran.text
        return [
            client.get(f"/api/assessments/{a['thread_id']}", headers=under).json()["assessment"]
            for a in ran.json()["assessments"]
        ]


def verdicts(assessment: dict) -> dict[str, str]:
    return {r["id"]: r["verdict"] for r in assessment["requirements"]}


def cited(assessment: dict) -> set[str]:
    return {c["doc_id"] for r in assessment["requirements"] for c in r["citations"]}


def dropped(assessment: dict) -> list[dict]:
    return [s for s in assessment["steps"] if s["text"].startswith(DROPPED)]


def library_reply() -> ProfilePassReply:
    return ProfilePassReply.model_validate(
        json.loads(LIBRARY_PASS_SCRIPT.read_text())["profile_pass"]
    )


# --- chosen_documents_only on its own ------------------------------------------------------------


def judged(rid: str, verdict: str, *doc_ids: str) -> dict:
    citations = [{"doc_id": d, "quote": f"A quote from {d}."} for d in doc_ids]
    return {"requirement_id": rid, "verdict": verdict, "reason": "Shown.", "citations": citations}


def test_chosen_documents_only_keeps_chosen_citations_and_turns_the_rest_not_evidenced():
    reply = ProfilePassReply.model_validate(
        {
            "controls": [
                {
                    "control_id": "1.1.1",
                    "answer": "A draft answer.",
                    "requirements": [
                        judged("M1", "evidenced", "kept", "gone"),
                        judged("M2", "partly-evidenced", "gone"),
                        judged("M3", "evidenced", "gone"),
                        judged("M4", "not-evidenced"),
                    ],
                    "remediation": [
                        {
                            "requirement_id": "M3",
                            "missing": "Its own item.",
                            "owner_role": "IT manager",
                            "due_in_days": 30,
                        }
                    ],
                }
            ]
        }
    )

    limited = chosen_documents_only(reply, {"kept"})

    (control,) = limited.controls
    by_id = {r.requirement_id: r for r in control.requirements}
    assert [c.doc_id for c in by_id["M1"].citations] == ["kept"]
    assert by_id["M1"].verdict == "evidenced"
    assert (by_id["M2"].verdict, by_id["M2"].reason, by_id["M2"].citations) == (
        "not-evidenced",
        NONE_CHOSEN,
        [],
    )
    assert by_id["M3"].verdict == "not-evidenced"
    assert by_id["M4"].verdict == "not-evidenced" and by_id["M4"].reason == "Shown."
    items = {i.requirement_id: i for i in control.remediation}
    assert [i.requirement_id for i in control.remediation].count("M3") == 1
    assert items["M3"].missing == "Its own item."
    assert (items["M2"].missing, items["M2"].owner_role, items["M2"].due_in_days) == (
        MISSING,
        OWNER,
        60,
    )
    assert "M4" not in items and "M1" not in items  # M4 was not evidenced already
    assert control.answer == "A draft answer."
    assert reply.controls[0].requirements[0].citations[1].doc_id == "gone"  # not changed in place


# --- The library script ---------------------------------------------------------------------------


def test_the_library_script_covers_the_twelve_controls_with_the_expected_verdicts_and_citations():
    reply = library_reply()
    verdicts_, cites = expected_verdicts(), expected_cites()

    assert [c.control_id for c in reply.controls] == list(verdicts_)
    for control in reply.controls:
        assert {r.requirement_id: r.verdict for r in control.requirements} == verdicts_[
            control.control_id
        ]
        for r in control.requirements:
            got = {c.doc_id for c in r.citations}
            assert got == set(cites[control.control_id][r.requirement_id]), r.requirement_id
        to_fix = {r.requirement_id for r in control.requirements if r.verdict != "evidenced"}
        assert {i.requirement_id for i in control.remediation} == to_fix, control.control_id


COMPANY_WORDS = re.compile(r"Nordlicht|Alpenwerk|Musterfirma|Sample library|GmbH|\bAG\b|Ltd")


def test_every_text_of_the_library_script_passes_the_output_guard_and_names_no_company():
    texts = []
    for control in library_reply().controls:
        texts.append(control.answer)
        texts += [r.reason for r in control.requirements]
        texts += [f"{i.missing} {i.owner_role}" for i in control.remediation]

    assert [t for t in texts if claims_audit_result(t)] == []
    assert [t for t in texts if COMPANY_WORDS.search(t)] == []


# --- Through the route ----------------------------------------------------------------------------


def test_a_setup_company_with_every_document_gets_the_expected_verdicts_with_nothing_dropped():
    assessments = setup_pass(COMPANY)

    expected = expected_verdicts()
    assert [a["control_id"] for a in assessments] == list(expected)
    for a in assessments:
        assert verdicts(a) == expected[a["control_id"]], a["control_id"]
        assert dropped(a) == []
        assert cited(a) <= LIBRARY
    assert set().union(*(cited(a) for a in assessments)) == LIBRARY


def test_a_company_choosing_some_documents_gets_drafts_citing_only_those():
    chosen = {"information-security-policy", "training-record"}
    company = COMPANY | {
        "evidence": {"policies": ["information-security-policy"], "records": ["training-record"]}
    }

    assessments = setup_pass(company)

    cites = expected_cites()
    for a in assessments:
        assert cited(a) <= chosen
        assert dropped(a) == []
        items = {i["requirement_id"] for i in a["remediation"]}
        for r in a["requirements"]:
            wanted = cites[a["control_id"]][r["id"]]
            if wanted and not set(wanted) & chosen:
                assert r["verdict"] == "not-evidenced", (a["control_id"], r["id"])
                assert r["reason"] == NONE_CHOSEN
                assert r["id"] in items
    by_control = {a["control_id"]: a for a in assessments}
    assert verdicts(by_control["2.1.3"])["S5"] == "evidenced"  # the training record alone
    assert verdicts(by_control["5.1.2"]) == dict.fromkeys(
        verdicts(by_control["5.1.2"]), "not-evidenced"
    )


def test_a_company_marking_controls_not_applicable_gets_no_draft_for_them():
    company = COMPANY | {
        "not_applicable": [
            {"control": "1.6.1", "reason": "No staff on site."},
            {"control": "5.2.9", "reason": "Backups are a provider's."},
        ]
    }

    assessments = setup_pass(company)

    drafted = [a["control_id"] for a in assessments]
    assert "1.6.1" not in drafted and "5.2.9" not in drafted
    assert len(drafted) == 10


def test_with_a_fake_script_set_a_setup_companys_pass_plays_it_unfiltered(tmp_path: Path):
    script = tmp_path / "pass.json"
    script.write_text(PROFILE_PASS_SCRIPT.read_text())  # Nordlicht's, citing Nordlicht's documents

    assessments = setup_pass(COMPANY, Settings(fake_script=script))

    by_control = {a["control_id"]: a for a in assessments}
    assert dropped(by_control["1.1.1"])  # its Nordlicht citations reach the quote check
    assert all(r["reason"] != NONE_CHOSEN for a in assessments for r in a["requirements"])


def test_nordlicht_still_plays_the_bundled_nordlicht_script():
    choice = ModelChoice(Settings(), MemoryModelStore())

    model = choice.for_profile_pass(None, profile=load_named_profile("nordlicht"))

    assert isinstance(model, ScriptedChatModel)
    assert model.script.replies_for(ProfilePassReply) == [
        ProfilePassReply.model_validate(json.loads(PROFILE_PASS_SCRIPT.read_text())["profile_pass"])
    ]


def test_a_very_high_german_company_with_every_document_is_under_the_default_threshold():
    setup = CompanySetup.model_validate(
        COMPANY | {"answer_language": "de", "protection_level": "very high"}
    )
    profile = compliance_profile_of("beispiel", setup)
    model = ModelChoice(Settings(), MemoryModelStore()).for_profile_pass(None, profile=profile)

    result = run_profile_pass(
        profile,
        Memory(InMemoryStore(), "lead", profile.id),
        memory_checkpointer(),
        model,
        max_tokens=PROFILE_PASS_TOKENS,
    )

    assert len(result.assessments) == 12
    assert isinstance(model.prompts[0][-1], HumanMessage)
