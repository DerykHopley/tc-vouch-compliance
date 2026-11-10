"""Patch's vulnerability status as implemented evidence: the `vulnerability_status` tool reads
a recorded Patch response from the folder the profile allows; when it can't (missing,
malformed, too slow) the evidence is pending, and nothing crashes (spec #2, ticket #24)."""

import datetime
import logging
import shutil
import threading
import time
from pathlib import Path

import pytest
import yaml

from vouch import ScriptedChatModel, assess, load_named_profile, load_profile
from vouch.front_matter import split_front_matter
from vouch.graph import (
    NOT_JUDGED,
    PENDING_REASON,
    ConfirmFindings,
    Judgement,
    RemediationPlan,
    ReviewDecision,
    ShouldScore,
    confirm_findings,
    memory_checkpointer,
    resume,
)
from vouch.profile import NORDLICHT_PROFILE

NORDLICHT_DIR = NORDLICHT_PROFILE.parent
PATCH = "patch-vulnerability-status"
RESPONSE = NORDLICHT_DIR / "patch" / f"{PATCH}.md"
LABEL = "Patch's vulnerability status"
PENDING = PENDING_REASON.format(sources=LABEL)


def quote(text: str) -> dict[str, str]:
    return {"doc_id": PATCH, "quote": text}


# True quotes from the recorded response, one table row or paragraph each.
CVSS = quote("CVE-2026-31244 | 9.8 | Email gateway")
ASSETS = quote("Directory, ERP | High")
TREATED = quote("Risk accepted, see RA-2026-03")
MITIGATION = quote(
    "Until the replacement, the CAD vault server is reachable only from the design VLAN"
)
SCHEDULED = quote("Update scheduled for the maintenance window on 2026-11-14")

JUDGEMENT = [  # the expected answers for 5.2.5 (fixtures/nordlicht/expected-answers.yaml)
    {"requirement_id": "M1", "verdict": "evidenced", "reason": "Scored CVEs.", "citations": [CVSS]},
    {"requirement_id": "M2", "verdict": "evidenced", "reason": "Assets.", "citations": [ASSETS]},
    {
        "requirement_id": "M3",
        "verdict": "evidenced",
        "reason": "Each item treated.",
        "citations": [TREATED, SCHEDULED],
    },
    {
        "requirement_id": "S1",
        "verdict": "partly-evidenced",
        "reason": "Patching happens; nothing defines it.",
        "citations": [SCHEDULED],
    },
    {
        "requirement_id": "S2",
        "verdict": "evidenced",
        "reason": "Mitigation recorded.",
        "citations": [MITIGATION],
    },
    {"requirement_id": "S3", "verdict": "not-evidenced", "reason": "No check of installation."},
]


def patch_model(*gather: dict, judge: list[dict] | None = None) -> ScriptedChatModel:
    turns = list(gather) or [{"tool": "vulnerability_status"}]
    return ScriptedChatModel(
        script={
            "gather": [*turns, {"answer": "Patch tracks the vulnerabilities."}],
            "judge": {"requirements": JUDGEMENT if judge is None else judge},
        }
    )


def copy_profile(tmp_path: Path, **sources: str) -> Path:
    """Nordlicht's profile and evidence copied to tmp_path, allowing only the sources named."""
    folder = tmp_path / "nordlicht"
    shutil.copytree(NORDLICHT_DIR, folder)
    path = folder / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["evidence_sources"] = {name: {"folder": where} for name, where in sources.items()}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def guard_steps(result) -> list[str]:
    return [s["text"] for s in result.steps if s["node"] == "guard"]


def expected_5_2_5() -> dict[str, str]:
    data = yaml.safe_load((NORDLICHT_DIR / "expected-answers.yaml").read_text(encoding="utf-8"))
    control = next(c for c in data["controls"] if c["id"] == "5.2.5")
    return {r["id"]: r["verdict"] for r in control["requirements"]}


# The recorded response.


def test_the_recorded_response_is_a_versioned_implemented_document():
    data, body = split_front_matter(RESPONSE.read_text(encoding="utf-8"))

    assert data is not None
    assert data["id"] == PATCH
    assert data["kind"] == "implemented"
    assert str(data["version"]).strip()
    for part in ("Tracked vulnerabilities", "Overdue", "Risk-acceptance decisions"):
        assert part in body


def test_the_recorded_response_never_shows_that_installation_was_checked():
    text = RESPONSE.read_text(encoding="utf-8").lower()

    for word in ("verif", "confirm", "checked", "tested", "validated"):
        assert word not in text


# 5.2.5 at the assess seam.


def test_5_2_5_is_assessed_with_patchs_response_cited_as_implemented_evidence():
    horizon = datetime.date(2027, 6, 30)  # the demo's horizon: the response is current then
    result = assess("5.2.5", load_named_profile("nordlicht"), patch_model(), today=horizon)

    verdicts = {r.id: r.verdict for r in result.requirements}
    assert verdicts == expected_5_2_5()
    assert result.verdict == "evidenced"
    assert result.should_score == ShouldScore(evidenced=1, total=3)
    citations = [c for r in result.requirements for c in r.citations]
    assert {c.doc_id for c in citations} == {PATCH}
    assert {(c.kind, c.stale) for c in citations} == {("implemented", False)}
    m1 = next(r for r in result.requirements if r.id == "M1")
    assert m1.citations[0].section == "Tracked vulnerabilities"
    assert m1.citations[0].version == "2026-11-03T07:00Z"
    assert guard_steps(result) == []  # nothing refused, flagged or dropped
    texts = [s["text"] for s in result.steps]
    assert "Asking for Patch's vulnerability status." in texts
    assert any(t.startswith("Searched Patch's vulnerability status: ") for t in texts)
    assert result.warnings == []


class RecordingModel(ScriptedChatModel):
    """The scripted fake, recording every structured call's messages (the judge's)."""

    received: list[list]

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        inner = super().with_structured_output(schema, include_raw=include_raw, **kwargs)

        def recorded(messages):
            self.received.append(list(messages))
            return inner.invoke(messages)

        return type(inner)(recorded)


def test_the_response_reaches_the_judge_as_evidence_data():
    model = RecordingModel(script=patch_model().script, received=[])

    assess("5.2.5", load_named_profile("nordlicht"), model)

    judge_input = str(model.received[0][-1].content)
    assert "<evidence>" in judge_input
    assert "CVE-2026-31244" in judge_input
    assert '"source": "patch", "kind": "implemented", "stale": false' in judge_input


# Evidence pending: no crash; with nothing else read, every requirement waits for Patch.


def assert_pending(result) -> None:
    assert result.verdict == "not-evidenced"
    assert {(r.verdict, r.reason) for r in result.requirements} == {("not-evidenced", PENDING)}
    assert "evidence_pending" in result.warnings
    assert result.remediation == []  # a pending requirement isn't a gap to fix


@pytest.mark.parametrize(
    "broken",
    [
        None,  # missing
        "Just text, no front matter.\n",
        "---\nid: patch-vulnerability-status\nkind: implemented\n---\n# Tracked\n\nRows.\n",
        "---\nid: patch-vulnerability-status\nkind: documented\nversion: '1'\n---\n# T\n\nRows.\n",
        "---\nid: patch-vulnerability-status\nkind: implemented\nversion: '1'\n---\n\n",
        "---\n[not, a, mapping]\n---\n# Tracked\n\nRows.\n",
        b"\xff\xfe\x00 not utf-8",
    ],
    ids=[
        "missing",
        "no-front-matter",
        "no-version",
        "not-implemented",
        "empty",
        "bad-yaml",
        "not-text",
    ],
)
def test_a_missing_or_malformed_response_gives_evidence_pending(broken, tmp_path):
    profile_path = copy_profile(tmp_path, patch="patch")
    response = profile_path.parent / "patch" / f"{PATCH}.md"
    if broken is None:
        response.unlink()
    elif isinstance(broken, bytes):
        response.write_bytes(broken)
    else:
        response.write_text(broken, encoding="utf-8")
    model = patch_model()

    result = assess("5.2.5", load_profile(profile_path), model)

    assert_pending(result)
    assert Judgement not in model.turns  # nothing to judge, so the model isn't asked
    assert RemediationPlan not in model.turns  # nor to draft remediation for it
    texts = [s["text"] for s in result.steps]
    assert any(
        t.startswith("Patch's vulnerability status could not be read (")
        and t.endswith("), so the evidence is pending.")
        for t in texts
    )


def slow_patch(monkeypatch, seconds: float = 1) -> list[threading.Thread]:
    """Patch answering only after `seconds`, with a 0.05-second timeout; returns the threads
    that read it."""
    from vouch import patch

    readers: list[threading.Thread] = []

    def slow(folder):
        readers.append(threading.current_thread())
        time.sleep(seconds)
        raise AssertionError("the slow reply should never be used")

    monkeypatch.setattr(patch, "TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(patch, "read_response", slow)
    return readers


def test_a_slow_response_gives_evidence_pending_after_the_timeout(monkeypatch):
    readers = slow_patch(monkeypatch)
    started = time.monotonic()

    result = assess("5.2.5", load_named_profile("nordlicht"), patch_model())

    assert time.monotonic() - started < 0.9  # it didn't wait for the slow reply
    assert_pending(result)
    texts = [s["text"] for s in result.steps]
    assert "Patch didn't answer within 0.05 seconds, so the evidence is pending." in texts
    assert [t.daemon for t in readers] == [True]  # a hung read never blocks the process exit


def test_patch_still_pending_is_not_asked_again_in_the_same_run(monkeypatch):
    readers = slow_patch(monkeypatch)
    model = patch_model({"tool": "vulnerability_status"}, {"tool": "vulnerability_status"})

    result = assess("5.2.5", load_named_profile("nordlicht"), model)

    assert len(readers) == 1  # the second call was answered at once
    texts = [s["text"] for s in result.steps]
    assert (
        "Patch's vulnerability status is already pending in this run, so it wasn't asked again."
        in texts
    )
    assert_pending(result)


@pytest.mark.parametrize(
    "error",
    [ConnectionError("reset by peer"), RecursionError(), MemoryError(), OSError("disk")],
    ids=["connection", "recursion", "memory", "os"],
)
def test_any_error_reading_patch_gives_evidence_pending_and_is_logged(error, monkeypatch, caplog):
    from vouch import patch

    def failing(folder):
        raise error

    monkeypatch.setattr(patch, "read_response", failing)

    with caplog.at_level(logging.WARNING, logger="vouch.patch"):
        result = assess("5.2.5", load_named_profile("nordlicht"), patch_model())

    assert_pending(result)
    texts = [s["text"] for s in result.steps]
    assert (
        "Patch's vulnerability status could not be read (the read failed unexpectedly), so "
        "the evidence is pending." in texts
    )
    assert type(error).__name__ in caplog.text


def test_a_later_answer_from_patch_clears_its_pending_state(monkeypatch):
    from vouch import patch

    real, calls = patch.read_response, []

    def missing_once(folder):
        calls.append(folder)
        if len(calls) == 1:
            raise patch.ResponseError("there is no recorded response")
        return real(folder)

    monkeypatch.setattr(patch, "read_response", missing_once)
    model = patch_model({"tool": "vulnerability_status"}, {"tool": "vulnerability_status"})

    result = assess("5.2.5", load_named_profile("nordlicht"), model)

    assert len(calls) == 2  # not timed out, so asking again may help
    assert {r.id: r.verdict for r in result.requirements} == expected_5_2_5()
    assert "evidence_pending" not in result.warnings
    assert all("pending" not in r.reason for r in result.requirements)


# Pending is about one source: what else was read is still judged.

POLICY = {
    "doc_id": "information-security-policy",
    "quote": "The information security officer works out our security requirements each year",
}


def pending_with_policies(monkeypatch, judge: list[dict]) -> "RecordingModel":
    slow_patch(monkeypatch)
    script = patch_model(
        {"tool": "search_policies", "args": {"query": "security requirements"}},
        {"tool": "vulnerability_status"},
        judge=judge,
    ).script
    return RecordingModel(script=script, received=[])


def test_a_pending_patch_keeps_the_verdicts_other_evidence_shows(monkeypatch):
    model = pending_with_policies(
        monkeypatch,
        [
            {
                "requirement_id": "M2",
                "verdict": "partly-evidenced",
                "reason": "Requirements are worked out yearly.",
                "citations": [POLICY],
            },
            {"requirement_id": "M1", "verdict": "not-evidenced", "reason": "Nothing shows it."},
        ],
    )

    result = assess("5.2.5", load_named_profile("nordlicht"), model)

    by_id = {r.id: r for r in result.requirements}
    assert by_id["M2"].verdict == "partly-evidenced"
    # Partly evidenced, the pending source may hold the rest: noted, and no draft (#212).
    assert by_id["M2"].reason == f"Requirements are worked out yearly. ({PENDING})"
    assert result.remediation == []
    assert by_id["M2"].citations[0].doc_id == "information-security-policy"
    assert by_id["M1"].verdict == "not-evidenced"
    assert by_id["M1"].reason == f"Nothing shows it. ({PENDING})"
    assert "evidence_pending" in result.warnings
    judge_input = str(model.received[0][-1].content)
    evidence = judge_input[judge_input.index("<evidence>") : judge_input.index("</evidence>")]
    assert f"{LABEL} could not be read, so its evidence is pending" in evidence


def test_requirements_pending_on_patch_get_no_remediation_but_the_review_lists_them(
    monkeypatch,
):
    model = pending_with_policies(monkeypatch, [])
    saver = memory_checkpointer()

    proposed = assess("5.2.5", load_named_profile("nordlicht"), model, saver)

    assert proposed.remediation == []
    assert RemediationPlan not in model.turns
    remediate = next(s["text"] for s in proposed.steps if s["node"] == "remediate")
    assert remediate == (
        "No remediation item drafted: the evidence for M1, M2, M3, S1, S2, S3 is pending."
    )

    confirm_findings(proposed.thread_id, ConfirmFindings(), saver)
    reviewed = resume(proposed.thread_id, ReviewDecision(decision="accept"), saver)

    assert reviewed.remediation == []
    assert {r.reason for r in reviewed.requirements} == {f"{NOT_JUDGED} ({PENDING})"}


def test_the_default_timeout_outlasts_patchs_wake_up():
    from vouch import patch

    assert patch.TIMEOUT_SECONDS > 90  # Patch can take about 90 seconds to wake


# The allow-list.


def test_a_profile_that_doesnt_allow_patch_cant_call_it(tmp_path, monkeypatch):
    from vouch import patch

    read: list[Path] = []
    monkeypatch.setattr(patch, "read_response", read.append)
    profile = load_profile(copy_profile(tmp_path, policies="policies"))
    model = patch_model(
        {"tool": "vulnerability_status"},
        {"tool": "search_policies", "args": {"query": "vulnerability"}},
        judge=[],
    )

    result = assess("5.2.5", profile, model)

    assert guard_steps(result) == [
        "Refused vulnerability_status: the compliance profile doesn't allow the patch source."
    ]
    texts = [s["text"] for s in result.steps]
    assert "Asked for Patch's vulnerability status, which this profile doesn't allow." in texts
    assert {r.verdict for r in result.requirements} == {"not-evidenced"}
    assert read == []  # Patch was never read
