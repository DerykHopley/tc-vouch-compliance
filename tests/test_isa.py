"""The base framework, VDA ISA 2027.1: the 12 controls Vouch assesses, unchanged from the Excel."""

from pathlib import Path

import openpyxl
import pytest

from vouch.isa import (
    COLUMN_PREFIX,
    FRAMEWORK_DIR,
    BaseFrameworkError,
    UnknownControlError,
    load_base_framework,
    load_isa_control,
)

EXCEL = FRAMEWORK_DIR / "isa2027.1-en.xlsx"

# Requirements per column (must, should, high, very high), counted by hand from the
# Excel's "Information Security" sheet: one per "+" bullet, "None" counting as zero.
EXPECTED_COUNTS = {
    "1.1.1": (5, 4, 0, 0),
    "1.6.1": (3, 6, 0, 1),
    "1.6.2": (3, 3, 5, 1),
    "2.1.3": (1, 6, 0, 0),
    "4.1.2": (2, 3, 1, 1),
    "4.1.3": (7, 10, 0, 0),
    "4.2.1": (2, 5, 1, 2),
    "5.1.1": (1, 1, 1, 0),
    "5.1.2": (3, 3, 1, 1),
    "5.2.4": (5, 3, 2, 1),
    "5.2.5": (3, 3, 0, 0),
    "5.2.9": (2, 1, 3, 3),
}


def test_all_12_controls_load_in_base_framework_order():
    framework = load_base_framework()

    assert framework.source == "VDA ISA 2027.1"
    assert [c.id for c in framework.controls] == list(EXPECTED_COUNTS)
    assert {c.source for c in framework.controls} == {"VDA ISA 2027.1"}


def test_each_requirement_carries_its_framework_as_source(tmp_path):
    other = tmp_path / "controls.yaml"
    other.write_text(
        "source: Other ISA\ncontrols:\n- id: 5.1.1\n  question: Q?\n  iso_27001: ISO 27001\n"
        "  requirements:\n  - {id: M1, column: must, text: T}\n"
    )

    [control] = load_base_framework(other).controls

    assert [r.source for r in control.requirements] == ["Other ISA"]


@pytest.mark.parametrize("control_id", EXPECTED_COUNTS)
def test_requirement_counts_per_column_match_the_excel(control_id):
    control = load_isa_control(control_id)

    counts = tuple(sum(r.column == col for r in control.requirements) for col in COLUMN_PREFIX)
    assert counts == EXPECTED_COUNTS[control_id]


@pytest.mark.parametrize("control_id", EXPECTED_COUNTS)
def test_requirement_ids_number_each_column_from_1(control_id):
    control = load_isa_control(control_id)

    for column, prefix in COLUMN_PREFIX.items():
        ids = [r.id for r in control.requirements if r.column == column]
        assert ids == [f"{prefix}{n}" for n in range(1, len(ids) + 1)]


# Texts unchanged -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_rows() -> dict:
    """The Excel's rows by control number, read directly, without the extraction code."""
    workbook = openpyxl.load_workbook(EXCEL, read_only=True)
    try:
        return {r[2]: r for r in workbook["Information Security"].iter_rows(values_only=True)}
    finally:
        workbook.close()


def test_shipped_base_framework_equals_a_fresh_extraction_from_the_excel():
    from vouch.isa_extract import extract

    fresh = extract(EXCEL)
    shipped = load_base_framework()

    assert fresh["source"] == shipped.source == "VDA ISA 2027.1"
    assert [
        (c["id"], c["question"], c["iso_27001"],
         [(r["id"], r["column"], r["text"]) for r in c["requirements"]])
        for c in fresh["controls"]
    ] == [
        (c.id, c.question, c.iso_27001, [(r.id, r.column, r.text) for r in c.requirements])
        for c in shipped.controls
    ]  # fmt: skip


@pytest.mark.parametrize("control_id", EXPECTED_COUNTS)
def test_each_text_appears_verbatim_in_its_excel_cell(control_id, raw_rows):
    row = raw_rows[control_id]
    cell_of = {"must": row[9], "should": row[10], "high": row[11], "very high": row[12]}
    control = load_isa_control(control_id)

    assert control.question == row[7]
    assert "27001" in control.iso_27001 and control.iso_27001 in row[14]
    for req in control.requirements:
        assert f"+ {req.text}" in cell_of[req.column], req.id


def test_a_bullet_that_wraps_onto_a_second_line_keeps_both_lines():
    # 5.2.5 M1 continues on a line without "+": "... CVE database) \nand evaluated (...)"
    text = load_isa_control("5.2.5").requirements[0].text

    assert text.startswith("Information on technical vulnerabilities")
    assert text.endswith("and evaluated (e.g., Common Vulnerability Scoring System CVSS).")


# Extraction guards -----------------------------------------------------------------------------


def test_the_iso_reference_is_the_27001_entry_as_written():
    from vouch.isa_extract import iso_27001_reference

    cell = "ISO/IEC 27002:2022: 5.1\n------\nISO\xa027001:2022 A.5.18\n------\nNIST CSF 2.0: PR.AA-05"
    assert iso_27001_reference(cell) == "ISO\xa027001:2022 A.5.18"


@pytest.mark.parametrize("cell", [None, "NIST CSF 2.0: GV.PO-01", "ISO/IEC 27002:2022: 5.1"])
def test_a_cell_without_an_iso_27001_entry_is_a_value_error(cell):
    from vouch.isa_extract import iso_27001_reference

    with pytest.raises(ValueError, match="27001"):
        iso_27001_reference(cell)


def test_extraction_refuses_an_excel_whose_columns_moved(tmp_path):
    from vouch.isa_extract import extract

    workbook = openpyxl.load_workbook(EXCEL)
    sheet = workbook["Information Security"]
    sheet.insert_cols(9)  # every requirement column shifts one to the right
    moved = tmp_path / "moved.xlsx"
    workbook.save(moved)

    with pytest.raises(ValueError, match="header"):
        extract(moved)


def test_extraction_refuses_a_control_number_that_appears_twice(tmp_path):
    from vouch.isa_extract import extract

    workbook = openpyxl.load_workbook(EXCEL)
    sheet = workbook["Information Security"]
    row = next(r for r in sheet.iter_rows(values_only=True) if r[2] == "5.1.1")
    sheet.append(row)
    twice = tmp_path / "twice.xlsx"
    workbook.save(twice)

    with pytest.raises(ValueError, match=r"5\.1\.1.*more than once"):
        extract(twice)


# Failures --------------------------------------------------------------------------------------


def test_an_unknown_control_is_an_unknown_control_error():
    with pytest.raises(UnknownControlError, match="9.9.9"):
        load_isa_control("9.9.9")


GOOD_CONTROL = "- id: 5.1.1\n  question: Q?\n  iso_27001: ISO 27001\n"
BAD_FILES = {
    "missing-file": None,
    "malformed-yaml": "controls: [\n",
    "not-a-mapping": "- just a list\n",
    "missing-field": "source: VDA ISA 2027.1\ncontrols:\n- id: 5.1.1\n",
    "controls-not-a-list": "source: VDA ISA 2027.1\ncontrols: 5.1.1\n",
    "unknown-column": (
        f"source: VDA ISA 2027.1\ncontrols:\n{GOOD_CONTROL}"
        "  requirements:\n  - {id: X1, column: maybe, text: T}\n"
    ),
    "null-text": (
        f"source: VDA ISA 2027.1\ncontrols:\n{GOOD_CONTROL}"
        "  requirements:\n  - {id: M1, column: must, text: null}\n"
    ),
    "numeric-id": (
        "source: VDA ISA 2027.1\ncontrols:\n- id: 5.1\n  question: Q?\n  iso_27001: ISO 27001\n"
        "  requirements: []\n"
    ),
    "null-question": (
        "source: VDA ISA 2027.1\ncontrols:\n- id: 5.1.1\n  question:\n  iso_27001: ISO 27001\n"
        "  requirements: []\n"
    ),
}


@pytest.mark.parametrize("case", BAD_FILES)
def test_a_data_file_that_fails_to_parse_is_a_base_framework_error_naming_it(case, tmp_path):
    path = tmp_path / f"{case}.yaml"
    if BAD_FILES[case] is not None:
        path.write_text(BAD_FILES[case])

    with pytest.raises(BaseFrameworkError) as caught:
        load_base_framework(path)

    message = str(caught.value)
    assert message.startswith(f"cannot read base framework file {path}: ")
    assert "\n" not in message
    assert message.split(str(path), 1)[1].strip(": ")  # says what's wrong, not just where


def framework_file(tmp_path, requirements: str) -> Path:
    path = tmp_path / "controls.yaml"
    path.write_text(f"source: VDA ISA 2027.1\ncontrols:\n{GOOD_CONTROL}  requirements:\n{requirements}")
    return path


BAD_IDS = {
    "duplicate id": ("  - {id: M1, column: must, text: A}\n  - {id: M1, column: must, text: B}\n", "M1"),
    "letter not the column's": ("  - {id: S1, column: must, text: A}\n", "S1"),
    "numbers skip": ("  - {id: M1, column: must, text: A}\n  - {id: M3, column: must, text: B}\n", "M3"),
    "not from 1": ("  - {id: H2, column: high, text: A}\n", "H2"),
}


@pytest.mark.parametrize(("requirements", "bad_id"), BAD_IDS.values(), ids=BAD_IDS.keys())
def test_requirement_ids_are_checked_on_load_naming_control_and_requirement(
    tmp_path, requirements, bad_id
):
    path = framework_file(tmp_path, requirements)

    with pytest.raises(BaseFrameworkError, match=rf"control 5\.1\.1 {bad_id}: "):
        load_base_framework(path)


def test_a_missing_requirement_field_says_which_control_and_requirement(tmp_path):
    path = framework_file(tmp_path, "  - {id: M1, column: must}\n")

    with pytest.raises(BaseFrameworkError, match=r"control 5\.1\.1 M1: missing field 'text'"):
        load_base_framework(path)


def test_a_missing_control_field_says_which_control(tmp_path):
    path = tmp_path / "controls.yaml"
    path.write_text("source: VDA ISA 2027.1\ncontrols:\n- id: 5.1.1\n  question: Q?\n")

    with pytest.raises(BaseFrameworkError, match=r"control 5\.1\.1: missing field 'iso_27001'"):
        load_base_framework(path)


def test_a_yaml_error_names_its_line(tmp_path):
    path = tmp_path / "controls.yaml"
    path.write_text("source: VDA ISA 2027.1\ncontrols:\n  - id: [5.1.1\n")

    with pytest.raises(BaseFrameworkError, match=r"line \d+"):
        load_base_framework(path)


# German edition --------------------------------------------------------------------------------

EXCEL_DE = FRAMEWORK_DIR / "isa2027.1-de.xlsx"


@pytest.fixture(scope="module")
def raw_rows_de() -> dict:
    """The German Excel's rows by control number, read directly, without the extraction code."""
    workbook = openpyxl.load_workbook(EXCEL_DE, read_only=True)
    try:
        return {r[2]: r for r in workbook["Informationssicherheit"].iter_rows(values_only=True)}
    finally:
        workbook.close()


def test_the_german_edition_has_the_same_controls_requirement_ids_and_columns():
    # Profiles, overlays and eval cases name requirements by id, so both editions must agree.
    english, german = load_base_framework(), load_base_framework(language="de")

    assert german.source == english.source == "VDA ISA 2027.1"
    assert [(c.id, [(r.id, r.column) for r in c.requirements]) for c in german.controls] == [
        (c.id, [(r.id, r.column) for r in c.requirements]) for c in english.controls
    ]


def test_shipped_german_edition_equals_a_fresh_extraction_from_the_german_excel():
    from vouch.isa_extract import extract

    fresh = extract(EXCEL_DE, language="de")
    shipped = load_base_framework(language="de")

    assert fresh["source"] == shipped.source
    assert fresh["excel"] == "isa2027.1-de.xlsx"
    assert [
        (c["id"], c["question"], c["iso_27001"],
         [(r["id"], r["column"], r["text"]) for r in c["requirements"]])
        for c in fresh["controls"]
    ] == [
        (c.id, c.question, c.iso_27001, [(r.id, r.column, r.text) for r in c.requirements])
        for c in shipped.controls
    ]  # fmt: skip


@pytest.mark.parametrize("control_id", EXPECTED_COUNTS)
def test_each_german_text_appears_verbatim_in_its_german_excel_cell(control_id, raw_rows_de):
    row = raw_rows_de[control_id]
    cell_of = {"must": row[9], "should": row[10], "high": row[11], "very high": row[12]}
    control = load_isa_control(control_id, language="de")

    assert control.question == row[7]
    assert "27001" in control.iso_27001 and control.iso_27001 in row[14]
    for req in control.requirements:
        assert f"+ {req.text}" in cell_of[req.column], req.id


def test_a_german_control_reads_in_german():
    control = load_isa_control("5.1.1", language="de")

    assert control.question == "Inwieweit wird die Nutzung kryptografischer Verfahren verwaltet?"
    assert control.requirements[2].text.startswith("Anforderungen an die Schlüsselhoheit")


def test_an_empty_german_cell_says_keine_and_counts_as_no_requirement():
    from vouch.isa_extract import EDITIONS, split_requirements

    assert split_requirements("Keine", EDITIONS["de"]) == []
    with pytest.raises(ValueError, match="bullet"):
        split_requirements("Keine", EDITIONS["en"])  # English empty cells say "None"


def test_the_extraction_writes_each_edition_to_the_controls_file_vouch_reads():
    from vouch.isa import edition_files
    from vouch.isa_extract import EDITIONS

    assert set(EDITIONS) == set(edition_files())


def test_a_failing_edition_leaves_every_controls_file_unwritten(tmp_path, monkeypatch):
    from dataclasses import replace

    from vouch import isa, isa_extract

    english, german = tmp_path / "controls.yaml", tmp_path / "controls-de.yaml"
    english.write_text("before", encoding="utf-8")
    german.write_text("before", encoding="utf-8")
    monkeypatch.setattr(isa, "CONTROLS_FILE", english)
    monkeypatch.setattr(isa, "CONTROLS_DE_FILE", german)
    workbook = openpyxl.load_workbook(EXCEL_DE)
    workbook["Informationssicherheit"].insert_cols(9)
    moved = tmp_path / "moved.xlsx"
    workbook.save(moved)
    monkeypatch.setitem(isa_extract.EDITIONS, "de", replace(isa_extract.EDITIONS["de"], excel=moved))

    with pytest.raises(ValueError, match="header"):
        isa_extract.main()

    assert english.read_text(encoding="utf-8") == "before"
    assert german.read_text(encoding="utf-8") == "before"


def test_german_extraction_refuses_an_excel_whose_columns_moved(tmp_path):
    from vouch.isa_extract import extract

    workbook = openpyxl.load_workbook(EXCEL_DE)
    workbook["Informationssicherheit"].insert_cols(9)
    moved = tmp_path / "moved.xlsx"
    workbook.save(moved)

    with pytest.raises(ValueError, match="header"):
        extract(moved, language="de")


def test_a_language_without_an_edition_falls_back_to_english():
    assert load_isa_control("5.1.1", language="lt") == load_isa_control("5.1.1")


def test_english_is_the_default_edition():
    assert load_isa_control("5.1.1") == load_isa_control("5.1.1", language="en")
    assert load_isa_control("5.1.1").question.startswith("To what extent")


# Licence and credit ----------------------------------------------------------------------------


def test_the_licence_notice_and_credit_sit_beside_the_data():
    notice = (FRAMEWORK_DIR / "NOTICE.md").read_text(encoding="utf-8")

    assert "VDA ISA" in notice and "2027.1" in notice
    assert "ENX Association" in notice
    assert "CC BY-ND 4.0" in notice
    assert "https://creativecommons.org/licenses/by-nd/4.0/" in notice
    assert "isa2027.1-en.xlsx" in notice
    assert "isa2027.1-de.xlsx" in notice and "controls-de.yaml" in notice
