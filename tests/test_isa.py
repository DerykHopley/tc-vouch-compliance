"""The VDA ISA 2027.1 catalogue: the 12 controls Vouch assesses, shipped unchanged from the Excel."""

import pytest

from vouch.controls import UnknownControlError
from vouch.isa import CATALOGUE_DIR, CatalogueError, get_control, load_catalogue

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
COLUMNS = ("must", "should", "high", "very high")
PREFIX = {"must": "M", "should": "S", "high": "H", "very high": "V"}


def test_all_12_controls_load_in_catalogue_order():
    assert [c.id for c in load_catalogue().controls] == list(EXPECTED_COUNTS)


@pytest.mark.parametrize("control_id", EXPECTED_COUNTS)
def test_requirement_counts_per_column_match_the_excel(control_id):
    control = get_control(control_id)

    counts = tuple(sum(r.column == col for r in control.requirements) for col in COLUMNS)
    assert counts == EXPECTED_COUNTS[control_id]


@pytest.mark.parametrize("control_id", EXPECTED_COUNTS)
def test_requirement_ids_number_each_column_from_1(control_id):
    control = get_control(control_id)

    for column in COLUMNS:
        ids = [r.id for r in control.requirements if r.column == column]
        assert ids == [f"{PREFIX[column]}{n}" for n in range(1, len(ids) + 1)]
    assert {r.source for r in control.requirements} == {"VDA ISA 2027.1"}


# Texts unchanged -------------------------------------------------------------------------------

def _raw_rows() -> dict:
    """The Excel's rows by control number, read directly, without the extraction code."""
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.load_workbook(CATALOGUE_DIR / "isa2027.1-en.xlsx", read_only=True)
    try:
        return {r[2]: r for r in workbook["Information Security"].iter_rows(values_only=True)}
    finally:
        workbook.close()


def test_shipped_catalogue_equals_a_fresh_extraction_from_the_excel():
    from vouch.isa_extract import extract

    fresh = extract(CATALOGUE_DIR / "isa2027.1-en.xlsx")
    shipped = load_catalogue()

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
def test_each_text_appears_verbatim_in_its_excel_cell(control_id):
    row = _raw_rows()[control_id]
    cell_of = {"must": row[9], "should": row[10], "high": row[11], "very high": row[12]}
    control = get_control(control_id)

    assert control.question == row[7]
    assert control.iso_27001.startswith("ISO") and control.iso_27001 in row[14]
    for req in control.requirements:
        assert f"+ {req.text}" in cell_of[req.column], req.id


def test_a_bullet_that_wraps_onto_a_second_line_keeps_both_lines():
    # 5.2.5 M1 continues on a line without "+": "... CVE database) \nand evaluated (...)"
    text = get_control("5.2.5").requirements[0].text

    assert text.startswith("Information on technical vulnerabilities")
    assert text.endswith("and evaluated (e.g., Common Vulnerability Scoring System CVSS).")


# Failures --------------------------------------------------------------------------------------

def test_an_unknown_control_is_an_unknown_control_error():
    with pytest.raises(UnknownControlError, match="9.9.9"):
        get_control("9.9.9")


BAD_FILES = {
    "missing-file": None,
    "malformed-yaml": "controls: [\n",
    "not-a-mapping": "- just a list\n",
    "missing-field": "source: VDA ISA 2027.1\ncontrols:\n- id: 5.1.1\n",
    "unknown-column": (
        "source: VDA ISA 2027.1\ncontrols:\n- id: 5.1.1\n  question: Q?\n  iso_27001: ISO\n"
        "  requirements:\n  - {id: X1, column: maybe, text: T}\n"
    ),
}


@pytest.mark.parametrize("case", BAD_FILES)
def test_a_data_file_that_fails_to_parse_is_a_catalogue_error_naming_it(case, tmp_path):
    path = tmp_path / f"{case}.yaml"
    if BAD_FILES[case] is not None:
        path.write_text(BAD_FILES[case])

    with pytest.raises(CatalogueError) as caught:
        load_catalogue(path)

    message = str(caught.value)
    assert str(path) in message
    assert "\n" not in message
    assert message.split(str(path), 1)[1].strip(": ")  # says what's wrong, not just where


# Licence and credit ----------------------------------------------------------------------------

def test_the_licence_notice_and_credit_sit_beside_the_data():
    notice = (CATALOGUE_DIR / "NOTICE.md").read_text(encoding="utf-8")

    assert "VDA ISA" in notice and "2027.1" in notice
    assert "ENX Association" in notice
    assert "CC BY-ND 4.0" in notice
    assert "https://creativecommons.org/licenses/by-nd/4.0/" in notice
    assert "isa2027.1-en.xlsx" in notice
