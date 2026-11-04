"""Patch's vulnerability status as implemented evidence: the `vulnerability_status` tool reads
a recorded Patch response from the folder the profile allows; when it can't (missing,
malformed, too slow) the evidence is pending, and nothing crashes (spec #2, ticket #24)."""

import shutil
import time
from pathlib import Path

import pytest
import yaml

from vouch import ScriptedChatModel, assess, load_named_profile, load_profile
from vouch.front_matter import split_front_matter
from vouch.graph import Judgement, ShouldScore
from vouch.profile import NORDLICHT_PROFILE

NORDLICHT_DIR = NORDLICHT_PROFILE.parent
PATCH = "patch-vulnerability-status"
RESPONSE = NORDLICHT_DIR / "patch" / f"{PATCH}.md"
PENDING = "evidence pending: Patch's vulnerability status could not be read"


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
    result = assess("5.2.5", load_named_profile("nordlicht"), patch_model())

    verdicts = {r.id: r.verdict for r in result.requirements}
    assert verdicts == expected_5_2_5()
    assert result.verdict == "evidenced"
    assert result.should_score == ShouldScore(evidenced=1, total=3)
    cited = {c.doc_id for r in result.requirements for c in r.citations}
    assert cited == {PATCH}
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


# Evidence pending: no crash, every requirement not evidenced with the reason.


def assert_pending(result) -> None:
    assert result.verdict == "not-evidenced"
    assert {(r.verdict, r.reason) for r in result.requirements} == {("not-evidenced", PENDING)}
    assert "evidence_pending" in result.warnings


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
    texts = [s["text"] for s in result.steps]
    assert any(
        t.startswith("Patch's vulnerability status could not be read (")
        and t.endswith("), so the evidence is pending.")
        for t in texts
    )


def test_a_slow_response_gives_evidence_pending_after_the_timeout(monkeypatch):
    from vouch import patch

    def slow(folder):
        time.sleep(1)
        raise AssertionError("the slow reply should never be used")

    monkeypatch.setattr(patch, "TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(patch, "read_response", slow)
    started = time.monotonic()

    result = assess("5.2.5", load_named_profile("nordlicht"), patch_model())

    assert time.monotonic() - started < 0.9  # it didn't wait for the slow reply
    assert_pending(result)
    texts = [s["text"] for s in result.steps]
    assert "Patch didn't answer within 0.05 seconds, so the evidence is pending." in texts


def test_the_default_timeout_outlasts_patchs_wake_up():
    from vouch import patch

    assert patch.TIMEOUT_SECONDS > 90  # Patch can take about 90 seconds to wake


# The allow-list.


def test_a_profile_that_doesnt_allow_patch_cant_call_it(tmp_path, monkeypatch):
    from vouch import patch

    def never(folder):
        raise AssertionError("Patch was read without the profile allowing it")

    monkeypatch.setattr(patch, "read_response", never)
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
