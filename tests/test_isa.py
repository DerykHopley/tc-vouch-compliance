"""The base framework, VDA ISA 2027.1: the 12 controls Vouch assesses, unchanged from the Excel."""

import pytest

from vouch.controls import UnknownControlError
from vouch.isa import (
    COLUMN_PREFIX,
    FRAMEWORK_DIR,
    BaseFrameworkError,
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
    openpyxl = pytest.importorskip("openpyxl")
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
    openpyxl = pytest.importorskip("openpyxl")
    from vouch.isa_extract import extract

    workbook = openpyxl.load_workbook(EXCEL)
    sheet = workbook["Information Security"]
    sheet.insert_cols(9)  # every requirement column shifts one to the right
    moved = tmp_path / "moved.xlsx"
    workbook.save(moved)

    with pytest.raises(ValueError, match="header"):
        extract(moved)


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


def test_a_yaml_error_names_its_line(tmp_path):
    path = tmp_path / "controls.yaml"
    path.write_text("source: VDA ISA 2027.1\ncontrols:\n  - id: [5.1.1\n")

    with pytest.raises(BaseFrameworkError, match=r"line \d+"):
        load_base_framework(path)


# Licence and credit ----------------------------------------------------------------------------


def test_the_licence_notice_and_credit_sit_beside_the_data():
    notice = (FRAMEWORK_DIR / "NOTICE.md").read_text(encoding="utf-8")

    assert "VDA ISA" in notice and "2027.1" in notice
    assert "ENX Association" in notice
    assert "CC BY-ND 4.0" in notice
    assert "https://creativecommons.org/licenses/by-nd/4.0/" in notice
    assert "isa2027.1-en.xlsx" in notice
