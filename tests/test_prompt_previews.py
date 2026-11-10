"""The prompt previews (#496, story 13): every prompt the agent gets, built for a company and a
control by the agent's own builders, what only a run knows as `[from the run: …]` placeholders.

Unit tests on vouch.prompts; the API's route is in test_developer_prompts.py."""

import json
import re
from datetime import date

import pytest
from test_lead_statements import CITING_Q12, script_citing, statement

from vouch import ScriptedChatModel, graph, load_named_profile
from vouch.chat import CHAT_PROMPT_VERSION
from vouch.graph import PROMPT_VERSION, assess_steps, overlay_requirements_of
from vouch.profile import NotApplicable
from vouch.profile_pass import PROFILE_PASS_PROMPT_VERSION
from vouch.prompts import PromptPreview, prompt_previews

NORDLICHT = load_named_profile("nordlicht")
ALPENWERK = load_named_profile("alpenwerk")
PLACEHOLDER = re.compile(r"\[from the run: [^\]]+\]")
ORDER = ["gather", "overlay", "judge", "remediation", "profile_pass", "chat"]


def previews(profile=NORDLICHT, control_id: str = "1.1.1") -> dict[str, PromptPreview]:
    return {p.name: p for p in prompt_previews(profile, profile.isa_control(control_id))}


def text(preview: PromptPreview, role: str) -> str:
    return "\n".join(m.text for m in preview.messages if m.role == role)


def test_six_prompts_in_the_agents_order_each_with_its_version_and_title():
    shown = prompt_previews(NORDLICHT, NORDLICHT.isa_control("1.1.1"))

    assert [p.name for p in shown] == ORDER
    assert [p.title for p in shown] == [
        "Gather evidence",
        "Country overlay",
        "Judge",
        "Remediation",
        "Profile pass",
        "Chat",
    ]
    assert [p.version for p in shown] == [PROMPT_VERSION] * 4 + [
        PROFILE_PASS_PROMPT_VERSION,
        CHAT_PROMPT_VERSION,
    ]
    assert all(p.note for p in shown)


def test_the_system_messages_are_those_a_scripted_run_with_one_lead_statement_sent():
    model = ScriptedChatModel(script=script_citing(CITING_Q12))
    control = NORDLICHT.isa_control("1.1.1")
    list(assess_steps(control, NORDLICHT, model, today=date(2026, 11, 9), statements=[statement()]))
    assert len(model.prompts) >= 3  # the script reaches remediation
    gather, judge, remediation = model.prompts[0], model.prompts[-2], model.prompts[-1]

    shown = previews()

    assert text(shown["gather"], "system") == gather[0].content
    assert text(shown["judge"], "system") == judge[0].content
    assert text(shown["remediation"], "system") == remediation[0].content


@pytest.mark.parametrize("name", ["gather", "judge", "remediation", "profile_pass"])
def test_each_human_message_carries_the_control_and_every_applicable_requirement(name):
    control = ALPENWERK.isa_control("1.6.2")
    applicable = ALPENWERK.requirements_for(control).applicable
    assert any(r.column == "overlay" for r in applicable)  # O1, the overlay's, included

    human = text(previews(ALPENWERK, "1.6.2")[name], "human")

    assert f"Control {control.id}: {control.question}" in human
    for requirement in applicable:
        assert f"- {requirement.id} ({requirement.column}" in human
        assert requirement.text in human


def placeholders(preview: PromptPreview) -> set[str]:
    return set(PLACEHOLDER.findall("\n".join(m.text for m in preview.messages)))


def test_the_gather_prompt_shows_the_note_and_a_lead_statement_as_placeholders():
    shown = previews()["gather"]

    assert graph.LEAD_RULE in text(shown, "system")
    assert {
        "[from the run: the lead's rejection note, on a run asked for again]",
        "[from the run: the open question the lead answered]",
        "[from the run: the lead's answer]",
    } <= placeholders(shown)


def test_the_judge_prompt_shows_evidence_the_draft_the_statement_and_the_note_as_placeholders():
    shown = previews()["judge"]

    assert graph.LEAD_RULE in text(shown, "system")
    assert {
        "[from the run: a document's id]",
        "[from the run: the passage's heading]",
        "[from the run: the passage found]",
        "[from the run: the gather step's draft answer]",
        "[from the run: the lead's answer]",
        "[from the run: the lead's rejection note, on a run asked for again]",
    } <= placeholders(shown)


def test_the_remediation_prompt_lists_every_requirement_not_evidenced_with_the_judges_reason():
    shown = previews()["remediation"]
    human = text(shown, "human")

    assert human.count("[from the run: the judge's reason]") == len(
        NORDLICHT.requirements_for(NORDLICHT.isa_control("1.1.1")).applicable
    )
    assert "not evidenced" in human


def test_the_profile_pass_prompt_lists_the_control_and_one_document_placeholder():
    shown = previews()["profile_pass"]
    human = text(shown, "human")

    assert human.count("Control ") == 1
    assert {
        "[from the run: a document's id]",
        "[from the run: the document's whole text]",
        "[from the run: the lead's answer]",
    } <= placeholders(shown)


def test_the_chat_prompt_puts_the_lead_on_the_controls_working_paper():
    shown = previews()["chat"]
    control = NORDLICHT.isa_control("1.1.1")

    assert [m.role for m in shown.messages] == ["system"]
    assert f"the working paper of VDA ISA control 1.1.1: “{control.question}”" in text(
        shown, "system"
    )


def test_no_prompt_holds_a_real_documents_text():
    folders = [s.folder for s in NORDLICHT.evidence_sources.values()]
    lines = {
        line.strip()
        for folder in folders
        for path in folder.glob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if len(line.strip()) > 40  # a sentence, not a heading word every prompt might share
    }
    said = "\n".join(m.text for p in previews().values() for m in p.messages)

    assert lines
    assert [line for line in lines if line in said] == []


def test_for_alpenwerk_the_prompts_name_german_and_1_6_2s_overlay_prompt_lists_o1():
    shown = previews(ALPENWERK, "1.6.2")

    assert "written in German" in text(shown["gather"], "system")
    overlay = shown["overlay"]
    assert overlay.sent is True
    assert [m.role for m in overlay.messages] == ["human"]
    o1 = next(
        r
        for r in overlay_requirements_of(ALPENWERK.requirements_for(ALPENWERK.isa_control("1.6.2")))
        if r.id == "O1"
    )
    assert f"- O1 ({o1.source}): {o1.text}" in text(overlay, "human")


def test_for_a_control_with_no_overlay_requirement_the_overlay_prompt_is_not_sent():
    overlay = previews(NORDLICHT, "1.1.1")["overlay"]

    assert overlay.sent is False
    assert overlay.messages == ()
    assert overlay.note == "Not sent for this control: it has no overlay requirement."


def test_the_lead_statements_id_is_a_placeholder_too_wherever_it_shows():
    question_id = "[from the run: the question's id, such as Q12]"
    shown = previews()

    for name in ("gather", "judge", "profile_pass"):
        said = "\n".join(m.text for m in shown[name].messages)
        assert question_id in said, name
        assert '"Q1"' not in said, name


def test_for_a_control_with_nothing_to_fix_the_remediation_prompt_is_not_sent():
    # Every requirement of 1.1.1 not applicable: nothing is left for remediation to draft.
    control = NORDLICHT.isa_control("1.1.1")
    bare = NORDLICHT.model_copy(
        update={
            "not_applicable": (
                *NORDLICHT.not_applicable,
                NotApplicable(control="1.1.1", reason="Not ours."),
            )
        }
    )

    remediation = {p.name: p for p in prompt_previews(bare, control)}["remediation"]

    assert remediation.sent is False
    assert remediation.messages == ()
    assert remediation.note == "Not sent for this control: it has no requirement to fix."


def test_a_changed_rule_in_the_builder_shows_in_the_preview(monkeypatch):
    monkeypatch.setattr(graph, "LEAD_RULE", "A rule changed in the builder alone.")

    shown = previews()

    assert "A rule changed in the builder alone." in text(shown["gather"], "system")
    assert "A rule changed in the builder alone." in text(shown["judge"], "system")


def test_the_previews_serialise_as_the_api_sends_them():
    sent = previews()["gather"].to_dict()

    assert set(sent) == {"name", "title", "version", "sent", "note", "messages"}
    assert sent["messages"][0]["role"] == "system"
    json.dumps(sent)
