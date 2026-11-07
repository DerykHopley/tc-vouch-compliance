"""Compliance profiles: YAML files validated on load, deciding which requirements apply."""

from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from isa_editions import GERMAN_5_1_1

from vouch import isa
from vouch.isa import SOURCE, load_isa_control
from vouch.profile import NORDLICHT_PROFILE, ComplianceProfile, ProfileError, load_profile

NORDLICHT_DIR = NORDLICHT_PROFILE.parent


def test_nordlichts_profile_loads():
    profile = load_profile(NORDLICHT_PROFILE)

    assert profile.id == "nordlicht"
    assert profile.company == "Nordlicht Komponenten GmbH"
    assert profile.country == "Germany"
    assert profile.answer_language == "en"
    assert profile.protection_level == "normal"
    assert profile.base_framework == "VDA ISA 2027.1"
    assert profile.overlays == ()
    assert set(profile.evidence_sources) == {"policies", "records", "patch"}
    assert profile.evidence_sources["policies"].folder == NORDLICHT_DIR / "policies"
    assert profile.asset_register == ()
    assert profile.risks == ()


def write_profile(tmp_path: Path, **changes) -> Path:
    """A copy of Nordlicht's profile in tmp_path/nordlicht, its folders made, with these fields
    changed (a value of None removes the field)."""
    data = yaml.safe_load(NORDLICHT_PROFILE.read_text(encoding="utf-8"))
    for field, value in changes.items():
        if value is None:
            data.pop(field)
        else:
            data[field] = value
    folder = tmp_path / "nordlicht"
    for name in ("policies", "records", "patch"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    path = folder / "profile.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def ids(requirements) -> list[str]:
    return [r.id for r in requirements]


def test_at_normal_protection_high_and_very_high_requirements_dont_apply():
    profile = load_profile(NORDLICHT_PROFILE)

    applicable = profile.requirements_for(load_isa_control("1.6.2")).applicable

    assert ids(applicable) == ["M1", "M2", "M3", "S1", "S2", "S3"]


def test_at_high_protection_high_requirements_apply_but_not_very_high(tmp_path):
    profile = load_profile(write_profile(tmp_path, protection_level="high"))

    applicable = profile.requirements_for(load_isa_control("1.6.2")).applicable

    assert ids(applicable) == [
        "M1", "M2", "M3", "S1", "S2", "S3", "H1", "H2", "H3", "H4", "H5",
    ]  # fmt: skip


def test_at_very_high_protection_high_and_very_high_requirements_apply(tmp_path):
    profile = load_profile(write_profile(tmp_path, protection_level="very high"))

    applicable = profile.requirements_for(load_isa_control("1.6.2")).applicable

    assert ids(applicable)[-6:] == ["H1", "H2", "H3", "H4", "H5", "V1"]


def test_5_1_2_s3_is_not_applicable_for_nordlicht_with_its_reason():
    profile = load_profile(NORDLICHT_PROFILE)

    requirements = profile.requirements_for(load_isa_control("5.1.2"))

    assert ids(requirements.applicable) == ["M1", "M2", "M3", "S1", "S2"]
    [excluded] = requirements.not_applicable
    assert excluded.requirement.id == "S3"
    assert "no remote access to its company network" in excluded.reason


def test_5_2_4_m4_applies_to_nordlicht_now_it_uses_outside_services():
    profile = load_profile(NORDLICHT_PROFILE)

    requirements = profile.requirements_for(load_isa_control("5.2.4"))

    assert ids(requirements.applicable) == ["M1", "M2", "M3", "M4", "M5", "S1", "S2", "S3"]
    assert requirements.not_applicable == ()
    assert [(e.control, e.requirement) for e in profile.not_applicable] == [("5.1.2", "S3")]


def test_a_not_applicable_control_leaves_none_of_its_requirements_applicable(tmp_path):
    reason = "Nordlicht has no awareness programme in scope."
    entry = {"control": "2.1.3", "reason": reason}
    profile = load_profile(write_profile(tmp_path, not_applicable=[entry]))

    requirements = profile.requirements_for(load_isa_control("2.1.3"))

    assert requirements.applicable == ()
    assert ids(r.requirement for r in requirements.not_applicable) == [
        "M1", "S1", "S2", "S3", "S4", "S5", "S6",
    ]  # fmt: skip
    assert {r.reason for r in requirements.not_applicable} == {reason}


OVERLAY = {
    "name": "AT NIS2 (illustrative)",
    "requirements": [
        {"control": "1.6.2", "id": "O1", "text": "An early warning goes out within a day."},
    ],
}


def test_an_overlay_adds_its_requirement_to_the_named_control(tmp_path):
    profile = load_profile(write_profile(tmp_path, overlays=[OVERLAY]))

    applicable = profile.requirements_for(load_isa_control("1.6.2")).applicable
    other = profile.requirements_for(load_isa_control("1.6.1")).applicable

    assert ids(applicable) == ["M1", "M2", "M3", "S1", "S2", "S3", "O1"]
    added = applicable[-1]
    assert (added.column, added.source) == ("overlay", "AT NIS2 (illustrative)")
    assert added.text == "An early warning goes out within a day."
    assert "O1" not in ids(other)


def with_overlay_requirement(**changes: Any) -> list[dict[str, Any]]:
    requirements: Any = OVERLAY["requirements"]
    return [{**OVERLAY, "requirements": [{**requirements[0], **changes}]}]


NA_M4 = {"control": "5.2.4", "requirement": "M4", "reason": "x"}

# Each foreseeable bad profile, and the field (with its problem) its error must name.
INVALID = {
    "missing field": ({"company": None}, "company: Field required"),
    "unknown protection level": (
        {"protection_level": "medium"},
        "protection_level: Input should be 'normal', 'high' or 'very high'",
    ),
    "overlay naming an unknown control": (
        {"overlays": with_overlay_requirement(control="9.9.9")},
        "overlays.0.requirements.0.control: unknown control 9.9.9 in VDA ISA 2027.1",
    ),
    "overlay requirement with empty text": (
        {"overlays": with_overlay_requirement(text=" ")},
        "overlays.0.requirements.0.text: String should have at least 1 character",
    ),
    "not-applicable entry with no reason": (
        {"not_applicable": [{"control": "5.2.4", "requirement": "M4"}]},
        "not_applicable.0.reason: Field required",
    ),
    "not-applicable entry with an empty reason": (
        {"not_applicable": [{"control": "5.2.4", "requirement": "M4", "reason": ""}]},
        "not_applicable.0.reason: String should have at least 1 character",
    ),
    "not-applicable entry naming an unknown control": (
        {"not_applicable": [{"control": "9.9.9", "reason": "x"}]},
        "not_applicable.0.control: unknown control 9.9.9 in VDA ISA 2027.1",
    ),
    "not-applicable entry naming an unknown requirement": (
        {"not_applicable": [{"control": "5.2.4", "requirement": "M9", "reason": "x"}]},
        "not_applicable.0.requirement: control 5.2.4 has no requirement M9",
    ),
    "unknown base framework": (
        {"base_framework": "ISA 6"},
        "base_framework: Input should be 'VDA ISA 2027.1'",
    ),
    "the same overlay requirement id twice on a control": (
        {"overlays": [OVERLAY, {**OVERLAY, "name": "Second overlay"}]},
        "overlays.1.requirements.0.id: control 1.6.2 already has O1",
    ),
    "not-applicable requirement the protection level doesn't bring in": (
        {"not_applicable": [{"control": "5.2.4", "requirement": "H1", "reason": "x"}]},
        "not_applicable.0.requirement: 5.2.4 H1 doesn't apply at normal protection",
    ),
    "the same not-applicable requirement twice": (
        {"not_applicable": [NA_M4, NA_M4]},
        "not_applicable.1: overlaps not_applicable.0 (control 5.2.4)",
    ),
    "a not-applicable control and one of its requirements": (
        {"not_applicable": [NA_M4, {"control": "5.2.4", "reason": "x"}]},
        "not_applicable.1: overlaps not_applicable.0 (control 5.2.4)",
    ),
    "id not the profile's folder name": (
        {"id": "someone-else"},
        "id: must be the profile's folder name, nordlicht",
    ),
    "answer language not a two-letter code": (
        {"answer_language": "English"},
        "answer_language: String should match pattern",
    ),
    "empty company": ({"company": " "}, "company: String should have at least 1 character"),
    "empty id": ({"id": ""}, "id: String should have at least 1 character"),
    "empty country": ({"country": ""}, "country: String should have at least 1 character"),
    "absolute folder": (
        {"evidence_sources": {"policies": {"folder": "/tmp"}}},
        "evidence_sources.policies.folder: must stay inside the profile's folder",
    ),
    "folder escaping the profile's folder": (
        {"evidence_sources": {"policies": {"folder": "../nordlicht/../.."}}},
        "evidence_sources.policies.folder: must stay inside the profile's folder",
    ),
    "folder that doesn't exist": (
        {"evidence_sources": {"policies": {"folder": "no-such-folder"}}},
        "evidence_sources.policies.folder: folder ",
    ),
}


@pytest.mark.parametrize(("changes", "expected"), INVALID.values(), ids=INVALID.keys())
def test_an_invalid_profile_fails_with_an_error_naming_the_field(tmp_path, changes, expected):
    path = write_profile(tmp_path, **changes)

    with pytest.raises(ProfileError) as raised:
        load_profile(path)

    assert str(path) in str(raised.value)
    assert expected in str(raised.value)


def test_the_missing_folder_error_says_which_folder(tmp_path):
    path = write_profile(tmp_path, evidence_sources={"records": {"folder": "gone"}})

    with pytest.raises(ProfileError, match=r"evidence_sources\.records\.folder: folder .*gone"):
        load_profile(path)


def test_a_missing_profile_file_is_a_profile_error(tmp_path):
    with pytest.raises(ProfileError, match="profile.yaml"):
        load_profile(tmp_path / "profile.yaml")


def test_folders_are_never_resolved_against_the_working_directory():
    data = yaml.safe_load(NORDLICHT_PROFILE.read_text(encoding="utf-8"))

    with pytest.raises(TypeError, match="base_dir"):
        ComplianceProfile.model_validate(data)  # no context: no profile folder to resolve from


def test_a_broken_base_framework_comes_out_as_a_profile_error(tmp_path, monkeypatch):
    broken = tmp_path / "controls.yaml"
    broken.write_text("controls: [\n")
    monkeypatch.setattr(isa, "CONTROLS_FILE", broken)

    with pytest.raises(ProfileError, match="cannot read base framework file"):
        load_profile(NORDLICHT_PROFILE)


def test_the_base_framework_a_profile_accepts_is_the_one_vouch_ships():
    assert get_args(ComplianceProfile.model_fields["base_framework"].annotation) == (SOURCE,)


# ISA edition by answer language ----------------------------------------------------------------


def test_a_german_profile_reads_the_german_isa_edition(tmp_path):
    profile = load_profile(write_profile(tmp_path, answer_language="de", protection_level="high"))

    control = profile.isa_control("5.1.1")

    assert control.question == GERMAN_5_1_1
    [h1] = [r for r in profile.requirements_for(control).applicable if r.column == "high"]
    assert h1.text.startswith("Anforderungen an die Schlüsselhoheit")


def test_an_english_profile_reads_the_english_isa_edition():
    assert load_profile(NORDLICHT_PROFILE).isa_control("5.1.1") == load_isa_control("5.1.1")


def test_a_profile_in_a_language_without_an_isa_edition_reads_english(tmp_path):
    profile = load_profile(write_profile(tmp_path, answer_language="lt"))

    assert profile.isa_control("5.1.1") == load_isa_control("5.1.1")


def test_an_unknown_control_in_a_profiles_edition_is_an_unknown_control_error(tmp_path):
    profile = load_profile(write_profile(tmp_path, answer_language="de"))

    with pytest.raises(isa.UnknownControlError, match="9.9.9"):
        profile.isa_control("9.9.9")


def german_edition(tmp_path, monkeypatch, controls: str) -> None:
    """Swap the German edition's controls file for one holding these controls."""
    path = tmp_path / "controls-de.yaml"
    path.write_text(f"source: VDA ISA 2027.1\ncontrols:\n{controls}", encoding="utf-8")
    monkeypatch.setattr(isa, "CONTROLS_DE_FILE", path)


def test_a_german_profiles_controls_are_checked_against_the_german_edition(tmp_path, monkeypatch):
    german_edition(
        tmp_path,
        monkeypatch,
        "- {id: 5.2.4, question: Q, iso_27001: ISO 27001, requirements: []}\n",
    )
    path = write_profile(tmp_path, answer_language="de")  # Nordlicht's 5.1.2 S3 is not applicable

    with pytest.raises(ProfileError, match=r"not_applicable\.0\.control: unknown control 5\.1\.2"):
        load_profile(path)


def test_a_german_profiles_requirement_ids_are_checked_against_the_german_edition(
    tmp_path, monkeypatch
):
    german_edition(
        tmp_path,
        monkeypatch,
        "- id: 5.1.2\n  question: Q?\n  iso_27001: ISO 27001\n"
        "  requirements:\n  - {id: M1, column: must, text: T}\n",
    )
    path = write_profile(tmp_path, answer_language="de")

    with pytest.raises(ProfileError, match=r"control 5\.1\.2 has no requirement S3"):
        load_profile(path)


def test_a_broken_german_edition_is_a_profile_error_naming_it(tmp_path, monkeypatch):
    broken = tmp_path / "controls-de.yaml"
    broken.write_text("controls: [\n", encoding="utf-8")
    monkeypatch.setattr(isa, "CONTROLS_DE_FILE", broken)

    with pytest.raises(ProfileError, match="controls-de.yaml"):
        load_profile(write_profile(tmp_path, answer_language="de"))
