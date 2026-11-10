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
from test_sample_library_evidence import ABOUT_DOING

from vouch import graph, load_named_profile, profile_pass
from vouch.api import PROFILE_HEADER, create_app, current_user
from vouch.company_profiles import CompanySetup, compliance_profile_of
from vouch.fake_model import (
    NONE_CHOSEN,
    NONE_CHOSEN_ANSWER,
    VERY_HIGH_NONE,
    LibraryPassModel,
    ScriptedChatModel,
    ScriptError,
    chosen_documents_only,
    library_pass_model,
    none_chosen_item,
    part_chosen,
    part_chosen_item,
    very_high_item,
)
from vouch.graph import (
    NOT_DRAFTED,
    NOT_JUDGED,
    RemediationItemReply,
    claims_audit_result,
    memory_checkpointer,
)
from vouch.memory import Memory
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.paths import FIXTURES_DIR, SAMPLE_LIBRARY_DIR
from vouch.profile import ComplianceProfile, read_profile_once
from vouch.profile_pass import ProfilePassReply, run_profile_pass
from vouch.settings import LIBRARY_PASS_SCRIPT, PROFILE_PASS_SCRIPT, PROFILE_PASS_TOKENS, Settings

PASS = "/api/profile-pass"
DROPPED = "Dropped a citation"  # a guard step's text, as test_fake_covers reads it
EXPECTED = yaml.safe_load((SAMPLE_LIBRARY_DIR / "expected-answers.yaml").read_text())
POLICIES = sorted(p.stem for p in (SAMPLE_LIBRARY_DIR / "policies").glob("*.md"))
RECORDS = sorted(p.stem for p in (SAMPLE_LIBRARY_DIR / "records").glob("*.md"))
LIBRARY = {*POLICIES, *RECORDS}
TITLES = {
    p.stem: yaml.safe_load(p.read_text().split("---")[1])["title"]
    for p in SAMPLE_LIBRARY_DIR.glob("*/*.md")
}
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


def setup_profile(company: dict[str, Any]) -> ComplianceProfile:
    return compliance_profile_of("beispiel", CompanySetup.model_validate(company))


def library_reply() -> ProfilePassReply:
    return ProfilePassReply.model_validate(
        json.loads(LIBRARY_PASS_SCRIPT.read_text())["profile_pass"]
    )


# --- chosen_documents_only on its own ------------------------------------------------------------


def judged(rid: str, verdict: str, *doc_ids: str) -> dict:
    citations = [{"doc_id": d, "quote": f"A quote from {d}."} for d in doc_ids]
    return {"requirement_id": rid, "verdict": verdict, "reason": "Shown.", "citations": citations}


def item(rid: str, missing: str) -> dict:
    return {
        "requirement_id": rid,
        "missing": missing,
        "owner_role": "IT manager",
        "due_in_days": 30,
    }


def test_chosen_documents_only_keeps_chosen_citations_and_never_overstates_what_is_left():
    reply = ProfilePassReply.model_validate(
        {
            "controls": [
                {
                    "control_id": "1.1.1",
                    "answer": "A draft answer naming the gone document.",
                    "requirements": [
                        judged("M1", "evidenced", "kept", "gone"),
                        judged("M2", "partly-evidenced", "gone"),
                        judged("M3", "evidenced", "gone"),
                        judged("M4", "not-evidenced"),
                        judged("M5", "evidenced", "kept"),
                        judged("S1", "partly-evidenced", "kept", "gone", "also-kept"),
                    ],
                    "remediation": [
                        item("M3", "Its own item."),
                        item("M4", "M4's own item."),
                        item("S1", "The gone document, up to date."),
                    ],
                },
                {
                    "control_id": "1.6.1",
                    "answer": "Untouched.",
                    "requirements": [judged("M1", "evidenced", "kept")],
                },
            ]
        }
    )

    limited = chosen_documents_only(reply, {"kept", "also-kept"})

    changed, untouched = limited.controls
    by_id = {r.requirement_id: r for r in changed.requirements}
    m1, m2, m3, m4, m5, s1 = (by_id[r] for r in ("M1", "M2", "M3", "M4", "M5", "S1"))
    # Some citations left: kept, capped at partly evidenced, the reason naming only them.
    assert [c.doc_id for c in m1.citations] == ["kept"]
    assert (m1.verdict, m1.reason) == ("partly-evidenced", part_chosen(["kept"]))
    assert (s1.verdict, s1.reason) == ("partly-evidenced", part_chosen(["kept", "also-kept"]))
    # None left: not evidenced.
    for r in (m2, m3):
        assert (r.verdict, r.reason, r.citations) == ("not-evidenced", NONE_CHOSEN, [])
    # Nothing left out: as scripted.
    assert m4 == reply.controls[0].requirements[3]
    assert m5 == reply.controls[0].requirements[4]
    # A changed requirement's scripted item is replaced by the generic one.
    items = {i.requirement_id: i for i in changed.remediation}
    assert len(items) == len(changed.remediation)
    assert items == {
        "M1": part_chosen_item("M1"),
        "M2": none_chosen_item("M2"),
        "M3": none_chosen_item("M3"),
        "M4": item_of("M4", "M4's own item."),
        "S1": part_chosen_item("S1"),
    }
    # The answer, built from what is left only; a control that lost nothing keeps its own.
    assert changed.answer == (
        f"M1: {part_chosen(['kept'])} M5: Shown. S1: {part_chosen(['kept', 'also-kept'])}"
    )
    assert "gone" not in changed.answer
    assert untouched == reply.controls[1]
    assert reply.controls[0].requirements[0].citations[1].doc_id == "gone"  # not changed in place


def item_of(rid: str, missing: str) -> RemediationItemReply:
    return RemediationItemReply.model_validate(item(rid, missing))


def test_a_control_left_with_nothing_shown_gets_the_none_chosen_answer():
    reply = ProfilePassReply.model_validate(
        {
            "controls": [
                {
                    "control_id": "1.1.1",
                    "answer": "The gone document shows it.",
                    "requirements": [judged("M1", "evidenced", "gone")],
                }
            ]
        }
    )

    (control,) = chosen_documents_only(reply, {"kept"}).controls

    assert control.answer == NONE_CHOSEN_ANSWER


def test_the_generic_texts_pass_the_output_guard():
    texts = [
        NONE_CHOSEN,
        NONE_CHOSEN_ANSWER,
        VERY_HIGH_NONE,
        part_chosen(["kept", "also-kept"]),
        *(
            i.missing
            for i in (none_chosen_item("M1"), part_chosen_item("M1"), very_high_item("V1"))
        ),
    ]

    assert [t for t in texts if claims_audit_result(t)] == []


CHOICES = sorted(
    [frozenset({d}) for d in LIBRARY]
    + [frozenset(LIBRARY - {d}) for d in LIBRARY]
    + [frozenset(POLICIES), frozenset(RECORDS)],
    key=sorted,
)


@pytest.mark.parametrize("chosen", CHOICES, ids=lambda c: "+".join(sorted(c)))
def test_the_limited_script_names_no_document_left_out(chosen: frozenset[str]):
    unchosen = [(d, TITLES[d]) for d in sorted(LIBRARY - chosen)]

    for control in chosen_documents_only(library_reply(), chosen).controls:
        texts = [control.answer, *(r.reason for r in control.requirements)]
        texts += [i.missing for i in control.remediation]
        for text in texts:
            for doc_id, title in unchosen:
                assert doc_id not in text and title.lower() not in text.lower(), (
                    control.control_id,
                    doc_id,
                    text,
                )


# --- The library script ---------------------------------------------------------------------------


def very_high_ids() -> dict[str, set[str]]:
    profile = setup_profile(COMPANY | {"protection_level": "very high"})
    return {
        c.id: {r.id for r in profile.requirements_for(c).applicable if r.column == "very high"}
        for c in profile.isa_edition().controls
    }


def test_the_library_script_covers_the_twelve_controls_with_the_expected_verdicts_and_citations():
    reply = library_reply()
    verdicts_, cites, very_high = expected_verdicts(), expected_cites(), very_high_ids()

    assert [c.control_id for c in reply.controls] == list(verdicts_)
    for control in reply.controls:
        cid = control.control_id
        expected = {r.requirement_id: r.verdict for r in control.requirements}
        assert {r: v for r, v in expected.items() if r not in very_high[cid]} == verdicts_[cid]
        for r in control.requirements:
            if r.requirement_id in very_high[cid]:
                continue
            got = {c.doc_id for c in r.citations}
            assert got == set(cites[cid][r.requirement_id]), r.requirement_id
        to_fix = {r.requirement_id for r in control.requirements if r.verdict != "evidenced"}
        assert {i.requirement_id for i in control.remediation} == to_fix, cid


def test_the_library_script_judges_every_very_high_requirement_not_evidenced():
    very_high = very_high_ids()

    for control in library_reply().controls:
        scripted = {r.requirement_id: r for r in control.requirements}
        items = {i.requirement_id: i for i in control.remediation}
        for rid in very_high[control.control_id]:
            assert (scripted[rid].verdict, scripted[rid].reason, scripted[rid].citations) == (
                "not-evidenced",
                VERY_HIGH_NONE,
                [],
            ), (control.control_id, rid)
            assert items[rid] == very_high_item(rid)


def test_the_library_script_marks_the_requirements_about_doing_as_the_library_tests_do():
    marked = {
        c.control_id: sorted(r.requirement_id for r in c.requirements if r.about_doing)
        for c in library_reply().controls
    }

    assert {c: ids for c, ids in marked.items() if ids} == {
        c: sorted(ids) for c, ids in ABOUT_DOING.items()
    }


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
            elif set(wanted) - chosen and set(wanted) & chosen:
                assert r["verdict"] != "evidenced", (a["control_id"], r["id"])
    by_control = {a["control_id"]: a for a in assessments}
    assert verdicts(by_control["2.1.3"])["S5"] == "evidenced"  # the training record alone
    assert verdicts(by_control["5.1.2"]) == dict.fromkeys(
        verdicts(by_control["5.1.2"]), "not-evidenced"
    )


def test_a_requirement_that_lost_some_of_its_citations_is_no_longer_evidenced():
    company = COMPANY | {"evidence": {"policies": ["authentication-standard"], "records": []}}

    assessments = setup_pass(company)

    (control,) = [a for a in assessments if a["control_id"] == "4.1.2"]
    for rid in ("S1", "S2", "S3", "H1"):  # the standard and the sign-in settings record, scripted
        (r,) = [r for r in control["requirements"] if r["id"] == rid]
        assert r["verdict"] == "partly-evidenced", rid
        assert r["reason"] == part_chosen(["authentication-standard"])
    assert "sign-in-settings-record" not in control["answer"]
    assert all(dropped(a) == [] for a in assessments)


def test_a_company_choosing_only_policies_gets_no_requirement_about_doing_evidenced():
    company = COMPANY | {"evidence": {"policies": POLICIES, "records": []}}

    assessments = setup_pass(company)

    for a in assessments:
        doing = verdicts(a)
        for rid in ABOUT_DOING.get(a["control_id"], []):
            assert doing[rid] != "evidenced", (a["control_id"], rid)


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


def test_the_sample_librarys_reference_profile_still_plays_the_bundled_nordlicht_script():
    choice = ModelChoice(Settings(), MemoryModelStore())

    model = choice.for_profile_pass(None, profile=load_named_profile("sample-library"))

    assert isinstance(model, ScriptedChatModel)
    assert model.script.replies_for(ProfilePassReply) == [
        ProfilePassReply.model_validate(json.loads(PROFILE_PASS_SCRIPT.read_text())["profile_pass"])
    ]


def test_a_file_profile_mixing_library_and_other_folders_plays_the_bundled_nordlicht_script():
    mixed = ComplianceProfile.model_validate(
        {
            "id": FIXTURES_DIR.name,
            "company": "Mixed",
            "country": "Germany",
            "answer_language": "en",
            "protection_level": "high",
            "base_framework": "VDA ISA 2027.1",
            "overlays": [],
            "not_applicable": [],
            "evidence_sources": {
                "policies": {"folder": "sample-library/policies"},
                "records": {"folder": "nordlicht/records"},
            },
        },
        context={"base_dir": FIXTURES_DIR},
    )

    model = ModelChoice(Settings(), MemoryModelStore()).for_profile_pass(None, profile=mixed)

    assert model.script.replies_for(ProfilePassReply)[0] == ProfilePassReply.model_validate(
        json.loads(PROFILE_PASS_SCRIPT.read_text())["profile_pass"]
    )


def test_a_setup_company_gets_the_library_pass_and_its_profile_is_marked_stored():
    profile = setup_profile(COMPANY)

    model = ModelChoice(Settings(), MemoryModelStore()).for_profile_pass(None, profile=profile)

    assert profile.stored and not load_named_profile("sample-library").stored
    assert isinstance(model, LibraryPassModel)
    assert model.profile.stored  # still, once passed into the model


def test_the_library_pass_reads_its_script_only_when_first_called(monkeypatch, tmp_path: Path):
    from vouch import fake_model

    monkeypatch.setattr(fake_model, "LIBRARY_PASS_SCRIPT", tmp_path / "missing.json")

    model = library_pass_model(setup_profile(COMPANY))  # nothing read yet

    with pytest.raises(ScriptError):
        model.with_structured_output(ProfilePassReply).invoke([HumanMessage("Assess.")])


def test_the_chosen_documents_are_the_ones_each_source_uses():
    profile = setup_profile(
        COMPANY | {"evidence": {"policies": ["backup-policy"], "records": ["training-record"]}}
    )

    used = {n: [p.stem for p in s.usable_documents()] for n, s in profile.evidence_sources.items()}

    assert used == {"policies": ["backup-policy"], "records": ["training-record"]}


def test_a_very_high_german_company_with_every_document_is_under_the_default_threshold():
    profile = setup_profile(COMPANY | {"answer_language": "de", "protection_level": "very high"})
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
    for a in result.assessments:
        assert [r.id for r in a.requirements if r.reason == NOT_JUDGED] == [], a.control_id
        assert [i.requirement_id for i in a.remediation if i.missing == NOT_DRAFTED] == []
