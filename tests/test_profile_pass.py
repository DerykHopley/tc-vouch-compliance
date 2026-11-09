"""The profile pass (#371): every applicable control of a profile not yet accepted, in one
guarded model run.

On the scripted fake model, offline: the bundled Nordlicht pass script, or a reply built here.
The engine first (run_profile_pass), then its route, POST /api/profile-pass, on the in-memory
backend with a user new to each test (test_memory's `as_user`). The pass's own daily limit is
test_spending's; its counts table test_database's.
"""

import copy
import json
import shutil
import uuid
from datetime import date
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from test_memory import as_user

from vouch import ScriptedChatModel, load_named_profile
from vouch.api import create_app, current_user
from vouch.graph import (
    CAPPED,
    CITATIONS_DROPPED,
    GUARD,
    WAITING,
    OutputCheckError,
    ReviewDecision,
    load_assessment,
    memory_checkpointer,
    resume,
)
from vouch.memory import Memory, newest_by_control, process_store
from vouch.paths import FIXTURES_DIR
from vouch.profile import DEFAULT_PROFILE, PROFILE_FILE, load_profile, read_profile_once
from vouch.profile_pass import (
    PASS_NODE,
    PROFILE_PASS_FAILED,
    PROFILE_PASS_PROMPT_VERSION,
    ProfilePassRefused,
    ProfilePassReply,
    estimate_tokens,
    run_profile_pass,
)
from vouch.settings import PROFILE_PASS_SCRIPT, Settings
from vouch.spending import PROFILE_PASS

NORDLICHT = load_named_profile("nordlicht")
TODAY = date(2026, 11, 4)
CONTROLS = [
    "1.1.1",
    "1.6.1",
    "1.6.2",
    "2.1.3",
    "4.1.2",
    "4.1.3",
    "4.2.1",
    "5.1.1",
    "5.1.2",
    "5.2.4",
    "5.2.5",
    "5.2.9",
]


def demo_reply() -> dict:
    """The bundled Nordlicht pass reply, as a dict to change."""
    return json.loads(PROFILE_PASS_SCRIPT.read_text())["profile_pass"]


def fake(*replies: dict) -> ScriptedChatModel:
    """The fake playing these pass replies in turn (the last repeats); the bundled one by
    default."""
    played = list(replies) or [demo_reply()]
    return ScriptedChatModel(script={"profile_pass": played})


class Lead:
    """One lead's memory and saver, new to the test."""

    def __init__(self, profile=NORDLICHT) -> None:
        self.profile = profile
        self.memory = Memory(InMemoryStore(), "lead", profile.id)
        self.saver = memory_checkpointer()
        self.counted = 0

    def count(self) -> None:
        self.counted += 1

    def run(self, model, **kwargs):
        return run_profile_pass(
            self.profile,
            self.memory,
            self.saver,
            model,
            today=TODAY,
            count=self.count,
            **kwargs,
        )

    def review(self, thread_id: str, decision: str) -> None:
        resume(thread_id, ReviewDecision(decision=decision), self.saver, scope=self.memory.scope)


def prompt_text(model: ScriptedChatModel, call: int = 0) -> str:
    return "\n".join(str(m.content) for m in model.prompts[call])


def control_in(reply: dict, control_id: str) -> dict:
    return next(c for c in reply["controls"] if c["control_id"] == control_id)


def requirement(assessment, requirement_id: str):
    return next(r for r in assessment.requirements if r.id == requirement_id)


def test_a_pass_over_nordlicht_makes_one_call_and_one_waiting_assessment_per_control():
    lead, model = Lead(), fake()

    result = lead.run(model)

    assert len(model.prompts) == 1
    assert [a.control_id for a in result.assessments] == CONTROLS
    assert {a.status for a in result.assessments} == {WAITING}
    assert result.skipped == []
    assert lead.counted == 1
    listed = {a.control_id: a for a in lead.memory.assessments(lead.saver)}
    assert set(listed) == set(CONTROLS)
    first = result.assessments[0]
    assert first.prompt_version == PROFILE_PASS_PROMPT_VERSION
    assert first.model == "scripted-fake"
    assert load_assessment(first.thread_id, lead.saver, scope=lead.memory.scope) == first


def test_the_bundled_script_keeps_every_quote():
    result = Lead().run(fake())

    dropped = [s for a in result.assessments for s in a.steps if s["node"] == GUARD]
    assert [s for s in dropped if "Dropped a citation" in s["text"]] == []


def test_each_assessment_says_it_came_from_a_profile_pass_with_the_agents_steps():
    result = Lead().run(fake())

    nodes = [s["node"] for s in result.assessments[0].steps]
    assert nodes[:2] == ["load_control", PASS_NODE]
    assert nodes[-4:] == ["judge", "check_quotes", "remediate", "review"]
    passed = result.assessments[0].steps[1]["text"]
    assert passed.startswith("Assessed in a profile pass: one model run over 12 controls")


def test_the_prompt_holds_every_document_as_data_with_its_kind_and_staleness():
    model = fake()
    Lead().run(model)

    text = prompt_text(model)
    assert "<evidence>" in text
    assert '"doc_id": "encryption-standard"' in text
    assert '"doc_id": "patch-vulnerability-status"' in text
    assert '"kind": "documented"' in text and '"stale": true' in text
    assert "Control 5.2.9:" in text


def test_a_whole_control_not_applicable_gets_no_assessment_and_isnt_in_the_prompt(tmp_path):
    shutil.copytree(FIXTURES_DIR / "nordlicht", tmp_path / "nordlicht")
    path = tmp_path / "nordlicht" / PROFILE_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["not_applicable"].append({"control": "5.2.9", "reason": "No backups are kept here."})
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    model = fake()

    result = Lead(load_profile(path)).run(model)

    assert "5.2.9" not in [a.control_id for a in result.assessments]
    assert len(result.assessments) == 11
    assert "Control 5.2.9" not in prompt_text(model)


def test_with_every_applicable_control_accepted_nothing_is_counted_or_called():
    lead = Lead()
    for assessment in lead.run(fake()).assessments:
        lead.review(assessment.thread_id, "accept")
    model = fake()

    again = lead.run(model)

    assert again.assessments == []
    assert again.skipped == CONTROLS
    assert model.prompts == []
    assert lead.counted == 1  # the first pass only


def test_an_accepted_control_is_skipped_and_a_rejected_one_assessed_again():
    lead = Lead()
    first = {a.control_id: a for a in lead.run(fake()).assessments}
    lead.review(first["1.1.1"].thread_id, "accept")
    lead.review(first["4.1.2"].thread_id, "reject")
    model = fake()

    again = lead.run(model)

    assert again.skipped == ["1.1.1"]
    assert "1.1.1" not in [a.control_id for a in again.assessments]
    assert "Control 1.1.1:" not in prompt_text(model)
    newest = newest_by_control(lead.memory, lead.saver)
    assert newest["4.1.2"].status == WAITING
    assert newest["4.1.2"].thread_id != first["4.1.2"].thread_id


def test_a_reply_for_a_control_not_included_is_ignored():
    reply = demo_reply()
    reply["controls"].append({**control_in(reply, "1.1.1"), "control_id": "9.9.9"})

    result = Lead().run(fake(reply))

    assert "9.9.9" not in [a.control_id for a in result.assessments]


def test_a_control_the_reply_leaves_out_is_not_judged():
    reply = demo_reply()
    reply["controls"] = [c for c in reply["controls"] if c["control_id"] != "5.2.9"]

    result = Lead().run(fake(reply))

    left_out = next(a for a in result.assessments if a.control_id == "5.2.9")
    assert {r.verdict for r in left_out.requirements} == {"not-evidenced"}
    assert {r.reason for r in left_out.requirements} == {"not judged"}


def test_a_made_up_quote_is_dropped_named_by_a_guard_step_and_its_requirement_not_evidenced():
    reply = demo_reply()
    m2 = next(r for r in control_in(reply, "1.1.1")["requirements"] if r["requirement_id"] == "M2")
    made_up = "Management signed this policy in front of the whole company."
    m2["citations"] = [{"doc_id": "information-security-policy", "quote": made_up}]

    result = Lead().run(fake(reply))

    assessed = next(a for a in result.assessments if a.control_id == "1.1.1")
    assert requirement(assessed, "M2").verdict == "not-evidenced"
    assert requirement(assessed, "M2").reason == CITATIONS_DROPPED
    guard = [s["text"] for s in assessed.steps if s["node"] == GUARD]
    named = "Dropped a citation for M2 from information-security-policy: the quote isn't in that"
    assert [text for text in guard if text.startswith("Dropped")] == [f"{named} document."]
    assert "M2" in [item.requirement_id for item in assessed.remediation]


def test_a_requirement_shown_only_by_a_stale_policy_is_capped_at_partly_evidenced():
    reply = demo_reply()
    m1 = next(r for r in control_in(reply, "5.1.1")["requirements"] if r["requirement_id"] == "M1")
    m1["verdict"] = "evidenced"
    m1["reason"] = "The approved methods follow current guidance."

    result = Lead().run(fake(reply))

    assessed = next(a for a in result.assessments if a.control_id == "5.1.1")
    capped = requirement(assessed, "M1")
    assert capped.verdict == "partly-evidenced"
    assert capped.reason.startswith("The approved methods follow current guidance. " + CAPPED)
    assert "encryption-standard is stale" in capped.reason


def test_an_audit_claim_gets_the_whole_reply_asked_for_again_and_the_clean_one_kept():
    claiming = copy.deepcopy(demo_reply())
    control_in(claiming, "4.1.2")["answer"] = "Nordlicht is compliant with this control."
    model = fake(claiming, demo_reply())

    result = Lead().run(model)

    assert len(model.prompts) == 2
    assert "Reply refused" in str(model.prompts[1][-1].content)
    assessed = next(a for a in result.assessments if a.control_id == "4.1.2")
    assert "compliant" not in assessed.answer
    assert "“compliant”" in assessed.steps[1]["text"]


def test_a_second_audit_claim_fails_the_pass_and_keeps_nothing():
    claiming = demo_reply()
    m1 = control_in(claiming, "5.2.9")["requirements"][0]
    m1["reason"] = "The backups passed the audit."
    lead = Lead()

    with pytest.raises(OutputCheckError):
        lead.run(fake(claiming))

    assert lead.memory.entries() == []
    assert lead.counted == 1  # counted before the calls were made


def test_a_prompt_over_the_threshold_is_refused_before_anything_is_counted_or_called():
    model, lead = fake(), Lead()

    with pytest.raises(ProfilePassRefused, match="over the threshold of 1,000"):
        lead.run(model, max_tokens=1000)

    assert model.prompts == []
    assert lead.counted == 0
    assert lead.memory.entries() == []


def test_nordlichts_prompt_is_under_the_default_threshold():
    model = fake()
    Lead().run(model)

    assert 5_000 < estimate_tokens(model.prompts[0]) < 50_000


def test_an_allowed_source_whose_folder_is_gone_refuses_the_pass(tmp_path):
    shutil.copytree(FIXTURES_DIR / "nordlicht", tmp_path / "nordlicht")
    profile = load_profile(tmp_path / "nordlicht" / PROFILE_FILE)
    shutil.rmtree(tmp_path / "nordlicht" / "records")
    model, lead = fake(), Lead(profile)

    with pytest.raises(ProfilePassRefused, match="the records folder can't be read"):
        lead.run(model)

    assert model.prompts == [] and lead.counted == 0


def test_an_allowed_source_whose_folder_now_points_outside_refuses_the_pass(tmp_path):
    shutil.copytree(FIXTURES_DIR / "nordlicht", tmp_path / "nordlicht")
    profile = load_profile(tmp_path / "nordlicht" / PROFILE_FILE)
    policies = tmp_path / "nordlicht" / "policies"
    shutil.move(policies, tmp_path / "elsewhere")
    policies.symlink_to(tmp_path / "elsewhere")
    lead = Lead(profile)

    with pytest.raises(ProfilePassRefused, match="now points outside its allowed folder"):
        lead.run(fake())

    assert lead.counted == 0


def test_a_pass_assessment_is_reviewed_through_resume_like_an_agent_runs():
    lead = Lead()
    assessment = lead.run(fake()).assessments[0]

    lead.review(assessment.thread_id, "accept")

    reviewed = load_assessment(assessment.thread_id, lead.saver, scope=lead.memory.scope)
    assert reviewed.status == "accepted"
    assert reviewed.steps[-1]["text"] == "The compliance lead accepted the assessment."


# --- The route: POST /api/profile-pass -----------------------------------------------------------

PASS = "/api/profile-pass"


def pass_app(settings: Settings | None = None):
    """A new app on the in-memory backend, the user set per request by a test header."""
    read_profile_once.cache_clear()
    app = create_app(settings or Settings())
    app.dependency_overrides[current_user] = as_user
    return app


def new_user() -> dict[str, str]:
    return {"X-Test-User": f"pass_{uuid.uuid4().hex[:8]}"}


def records_of(app, user: dict[str, str]) -> list:
    """The user's kept usage records (the in-memory spending store's)."""
    return [r for r in app.state.spending.store.records() if r.user_id == user["X-Test-User"]]


@pytest.fixture
def pass_calls(monkeypatch) -> list[type]:
    """Every structured reply the fake gives, by its schema: a pass's call is a ProfilePassReply."""
    calls: list[type] = []
    real = ScriptedChatModel.next_reply

    def counted(self, schema, messages):
        calls.append(schema)
        return real(self, schema, messages)

    monkeypatch.setattr(ScriptedChatModel, "next_reply", counted)
    return calls


def claiming_script(tmp_path: Path, replies: int) -> Path:
    """The bundled pass script, its 4.1.2 answer claiming an audit result in each of the first
    `replies` replies, the clean one after."""
    clean = demo_reply()
    claiming = copy.deepcopy(clean)
    control_in(claiming, "4.1.2")["answer"] = "Nordlicht is compliant with this control."
    script = tmp_path / "pass.json"
    script.write_text(json.dumps({"profile_pass": [claiming] * replies + [clean]}))
    return script


def review_through_api(client: TestClient, user: dict[str, str], thread_id: str, decision: str):
    reviewed = client.post(
        f"/api/assessments/{thread_id}/review", json={"decision": decision}, headers=user
    )
    assert reviewed.status_code == 200, reviewed.text


def test_the_route_makes_one_call_and_lists_one_waiting_assessment_per_control(pass_calls):
    client, lead = TestClient(pass_app()), new_user()

    response = client.post(PASS, headers=lead)

    assert response.status_code == 200, response.text
    body = response.json()
    assert pass_calls == [ProfilePassReply]
    assert [a["control_id"] for a in body["assessments"]] == CONTROLS
    assert {a["status"] for a in body["assessments"]} == {WAITING}
    assert body["skipped"] == []
    assert body["usage"]["model"] == "fake" and body["usage"]["input_tokens"] > 0
    listed = client.get("/api/assessments", headers=lead).json()["assessments"]
    assert {a["thread_id"] for a in listed} == {a["thread_id"] for a in body["assessments"]}
    cards = client.get("/api/overview", headers=lead).json()["controls"]
    assert {card["status"] for card in cards} == {WAITING}


def test_the_route_skips_an_accepted_control_and_assesses_a_rejected_one_again(pass_calls):
    client, lead = TestClient(pass_app()), new_user()
    first = {a["control_id"]: a for a in client.post(PASS, headers=lead).json()["assessments"]}
    review_through_api(client, lead, first["1.1.1"]["thread_id"], "accept")
    review_through_api(client, lead, first["4.1.2"]["thread_id"], "reject")

    again = client.post(PASS, headers=lead).json()

    assert again["skipped"] == ["1.1.1"]
    redone = {a["control_id"]: a for a in again["assessments"]}
    assert list(redone) == [c for c in CONTROLS if c != "1.1.1"]
    assert redone["4.1.2"]["thread_id"] != first["4.1.2"]["thread_id"]
    shown = client.get("/api/overview", headers=lead).json()["controls"]
    cards = {card["id"]: card["status"] for card in shown}
    assert cards["1.1.1"] == "accepted" and cards["4.1.2"] == WAITING
    assert pass_calls == [ProfilePassReply, ProfilePassReply]


def test_a_pass_assessment_is_reviewed_through_the_route_and_counts_on_the_overview():
    client, lead = TestClient(pass_app()), new_user()
    first = client.post(PASS, headers=lead).json()["assessments"][0]

    review_through_api(client, lead, first["thread_id"], "accept")

    opened = client.get(f"/api/assessments/{first['thread_id']}", headers=lead).json()
    assert opened["assessment"]["status"] == "accepted"
    assert opened["assessment"]["steps"][1]["node"] == PASS_NODE
    assert client.get("/api/overview", headers=lead).json()["readiness"]["accepted"] == 1


def test_the_route_keeps_the_clean_reply_after_one_audit_claim(tmp_path, pass_calls):
    client = TestClient(pass_app(Settings(fake_script=claiming_script(tmp_path, replies=1))))

    response = client.post(PASS, headers=new_user())

    assert response.status_code == 200, response.text
    assert pass_calls == [ProfilePassReply, ProfilePassReply]


def test_a_second_audit_claim_is_a_502_with_nothing_kept_and_the_usage_recorded_once(tmp_path):
    app = pass_app(Settings(fake_script=claiming_script(tmp_path, replies=2)))
    client, lead = TestClient(app), new_user()

    response = client.post(PASS, headers=lead)

    assert response.status_code == 502
    assert response.json() == {"detail": PROFILE_PASS_FAILED}
    assert client.get("/api/assessments", headers=lead).json()["assessments"] == []
    records = records_of(app, lead)
    assert [r.control_id for r in records] == [PROFILE_PASS]
    assert records[0].usage.input_tokens > 0  # the calls were made, and are counted


def test_a_successful_pass_records_its_usage_once_under_profile_pass():
    app = pass_app()
    client, lead = TestClient(app), new_user()

    client.post(PASS, headers=lead)

    assert [r.control_id for r in records_of(app, lead)] == [PROFILE_PASS]


def test_a_prompt_over_the_threshold_is_a_422_offering_the_agent_with_nothing_counted(pass_calls):
    app = pass_app(Settings(profile_pass_tokens=1000))
    client, lead = TestClient(app), new_user()

    refused = [client.post(PASS, headers=lead) for _ in range(4)]

    assert [r.status_code for r in refused] == [422] * 4  # never the limit's 429: none counted
    body = refused[0].json()
    assert body["fallback"] == "per-control"
    assert "over the threshold of 1,000" in body["detail"]
    assert pass_calls == []
    assert records_of(app, lead) == []
    assert client.get("/api/assessments", headers=lead).json()["assessments"] == []


def test_with_every_control_accepted_the_route_answers_200_with_nothing_counted(pass_calls):
    app = pass_app()
    client, lead = TestClient(app), new_user()
    for assessment in client.post(PASS, headers=lead).json()["assessments"]:
        review_through_api(client, lead, assessment["thread_id"], "accept")
    pass_calls.clear()

    again = [client.post(PASS, headers=lead) for _ in range(3)]  # 2 passes left, if counted

    assert [r.status_code for r in again] == [200] * 3
    assert again[0].json() | {"usage": None} == {
        "assessments": [],
        "skipped": CONTROLS,
        "usage": None,
    }
    assert pass_calls == []
    assert len(records_of(app, lead)) == 1  # the first pass's


def test_another_run_of_the_lead_going_is_a_409(pass_calls):
    app = pass_app()
    client, lead = TestClient(app), new_user()
    scope = Memory(process_store(), lead["X-Test-User"], DEFAULT_PROFILE).scope
    app.state.running.try_hold(scope)

    response = client.post(PASS, headers=lead)

    assert response.status_code == 409
    assert pass_calls == []
