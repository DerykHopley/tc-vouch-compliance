"""The draft answer's two sections (#670): "What the evidence shows" and "What is missing",
headed in the profile's answer language.

Unit tests on the gather prompt (vouch.graph.gather_prompt) and on every fake script's answer:
the demo scripts the local app plays, the eval's scripts and the demo-files script. The answers
also pass the output guard's checks (language, audit claim) with their headings and bullets,
and each quote in them is copied unchanged from a passage the script cites."""

import json
import re
from pathlib import Path

import pytest

from vouch import load_named_profile
from vouch.citations import occurs
from vouch.graph import claims_audit_result, gather_prompt
from vouch.language import mismatch
from vouch.paths import REPO_DIR

HEADINGS = {  # an independent copy: the owner's English shape, and the app's German words
    "en": ("What the evidence shows", "What is missing"),
    "de": ("Was die Nachweise zeigen", "Was fehlt"),
}
QUOTED = re.compile(r"“([^”]*)”|„([^“]*)“")


def system_text(profile_id: str, control_id: str = "5.1.1") -> str:
    profile = load_named_profile(profile_id)
    control = profile.isa_control(control_id)
    system, _ = gather_prompt(control, profile.requirements_for(control), profile)
    return str(system.content)


@pytest.mark.parametrize("profile_id, language", [("nordlicht", "en"), ("alpenwerk", "de")])
def test_the_gather_prompt_asks_for_the_two_sections_headed_in_the_answer_language(
    profile_id, language
):
    text = system_text(profile_id)

    shows, missing = HEADINGS[language]
    assert f"### {shows}" in text
    assert f"### {missing}" in text
    assert text.index(f"### {shows}") < text.index(f"### {missing}")
    assert "one bullet per gap" in text
    assert "naming its document" in text


def test_a_language_without_headings_of_its_own_gets_the_english_ones_to_translate():
    profile = load_named_profile("nordlicht").model_copy(update={"answer_language": "fr"})
    control = profile.isa_control("5.1.1")
    system, _ = gather_prompt(control, profile.requirements_for(control), profile)

    assert "### What the evidence shows" in str(system.content)
    assert "translated into the language with ISO 639-1 code 'fr'" in str(system.content)


# --- Every fake script's answer has the shape --------------------------------------------------


def gather_scripts() -> list[Path]:
    demo = [p for p in (REPO_DIR / "src/vouch/demo").glob("*.json") if "profile-pass" not in p.name]
    evals = sorted((REPO_DIR / "evals/scripts").glob("*/*.json"))
    demo_files = sorted((REPO_DIR / "docs/demo-files").glob("*.json"))
    return sorted(demo) + evals + demo_files


def language_of(path: Path) -> str:
    return "de" if path.stem.endswith("-de") or path.parent.name == "alpenwerk" else "en"


def answer_of(path: Path) -> str:
    (answer,) = [t["answer"] for t in json.loads(path.read_text())["gather"] if "answer" in t]
    return answer


def cited_quotes(path: Path) -> list[str]:
    judge = json.loads(path.read_text()).get("judge", {})
    return [c["quote"] for r in judge.get("requirements", []) for c in r.get("citations", [])]


SCRIPTS = gather_scripts()


def test_the_scripts_found_are_every_gather_script():
    assert len(SCRIPTS) == 8 + 36 + 1  # the demo's, the eval's three profiles', the demo-files'


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: str(p.relative_to(REPO_DIR)))
def test_a_fake_scripts_answer_has_the_two_sections_in_its_language(path):
    shows, missing = HEADINGS[language_of(path)]
    answer = answer_of(path)

    found = re.fullmatch(
        rf"### {shows}\n\n(?P<shows>.+?)\n\n### {missing}\n\n(?P<missing>.+)", answer, re.DOTALL
    )
    assert found, answer
    paragraphs = found["shows"].split("\n\n")
    assert all(p.strip() and not p.startswith(("#", "- ")) for p in paragraphs)
    lines = found["missing"].split("\n")
    bullets = [line for line in lines if line.startswith("- ")]
    assert bullets == lines or (len(lines) == 1 and not bullets), lines


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: str(p.relative_to(REPO_DIR)))
def test_a_fake_scripts_answer_passes_the_output_guard_and_quotes_only_what_it_cites(path):
    answer, quotes = answer_of(path), cited_quotes(path)

    assert mismatch(answer, language_of(path)) is None
    assert claims_audit_result(answer) is None  # its quotes too, whatever a search gathered
    for span in QUOTED.finditer(answer):
        quoted = span.group(1) or span.group(2)
        assert any(occurs(quoted, q) for q in quotes), quoted
