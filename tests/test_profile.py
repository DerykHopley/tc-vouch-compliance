"""Compliance profiles: YAML files validated on load, deciding which requirements apply."""

from pathlib import Path

import pytest
import yaml

from vouch.isa import load_isa_control
from vouch.profile import NORDLICHT_PROFILE, ProfileError, load_profile

FIXTURES = NORDLICHT_PROFILE.parent


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
    assert profile.evidence_sources["policies"].folder == FIXTURES / "policies"
    assert profile.asset_register == ()
    assert profile.risks == ()


def write_profile(tmp_path: Path, **changes) -> Path:
    """A copy of Nordlicht's profile in tmp_path, its folders made, with these fields changed
    (a value of None removes the field)."""
    data = yaml.safe_load(NORDLICHT_PROFILE.read_text(encoding="utf-8"))
    for field, value in changes.items():
        if value is None:
            data.pop(field)
        else:
            data[field] = value
    for name in ("policies", "records", "patch"):
        (tmp_path / name).mkdir(exist_ok=True)
    path = tmp_path / "profile.yaml"
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


def test_5_2_4_m4_is_not_applicable_for_nordlicht_with_its_reason():
    profile = load_profile(NORDLICHT_PROFILE)

    requirements = profile.requirements_for(load_isa_control("5.2.4"))

    assert ids(requirements.applicable) == ["M1", "M2", "M3", "M5", "S1", "S2", "S3"]
    [excluded] = requirements.not_applicable
    assert excluded.requirement.id == "M4"
    assert "no external IT services" in excluded.reason


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


def with_overlay_requirement(**changes) -> list[dict]:
    return [{**OVERLAY, "requirements": [{**OVERLAY["requirements"][0], **changes}]}]


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
