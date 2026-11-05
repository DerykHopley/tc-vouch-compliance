"""Extract Vouch's 12 VDA ISA controls from each ISA Excel edition into its controls file,
texts unchanged.

Run `uv run python -m vouch.isa_extract` to regenerate both files after replacing an Excel.
openpyxl is a dev dependency: only this extraction and its test read the Excel; the app reads
the YAML.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from vouch import isa
from vouch.isa import COLUMN_PREFIX, FRAMEWORK_DIR, SOURCE

CONTROL_IDS = (
    "1.1.1", "1.6.1", "1.6.2", "2.1.3", "4.1.2", "4.1.3",
    "4.2.1", "5.1.1", "5.1.2", "5.2.4", "5.2.5", "5.2.9",
)  # fmt: skip

# Columns of the "Information Security" sheet (0-based), the same in both editions;
# extract() checks them against the edition's header row, so an edition that moves a column
# fails instead of shipping the wrong text.
COL_ID, COL_QUESTION, COL_STANDARDS = 2, 7, 14
REQUIREMENT_COL = {"must": 9, "should": 10, "high": 11, "very high": 12}


@dataclass(frozen=True)
class Edition:
    """One language's ISA Excel: its file, and its sheet and header names. The controls file
    it is extracted into is `vouch.isa.edition_files()`'s for the same language."""

    excel: Path
    sheet: str
    headers: dict[int, str]  # column -> header text; COL_ID's finds the header row
    no_requirement: str  # what a requirement cell without bullets says


EDITIONS = {
    "en": Edition(
        excel=FRAMEWORK_DIR / "isa2027.1-en.xlsx",
        sheet="Information Security",
        headers={
            COL_ID: "Control number",
            COL_QUESTION: "Control question",
            REQUIREMENT_COL["must"]: "Requirements\n(must)",
            REQUIREMENT_COL["should"]: "Requirements\n(should)",
            REQUIREMENT_COL["high"]: "Additional requirements\nfor high protection needs",
            REQUIREMENT_COL["very high"]: "Additional requirements\nfor very high protection needs",
            COL_STANDARDS: "Reference to other standards",
        },
        no_requirement="None",
    ),
    "de": Edition(
        excel=FRAMEWORK_DIR / "isa2027.1-de.xlsx",
        sheet="Informationssicherheit",
        headers={
            COL_ID: "Kontrollnummer",
            COL_QUESTION: "Kontrollfrage",
            REQUIREMENT_COL["must"]: "Anforderungen \n(muss)",  # the space is the Excel's
            REQUIREMENT_COL["should"]: "Anforderungen \n(sollte)",
            REQUIREMENT_COL["high"]: "Zusatzanforderungen\nbei hohem Schutzbedarf",
            REQUIREMENT_COL["very high"]: "Zusatzanforderungen\nbei sehr hohem Schutzbedarf",
            COL_STANDARDS: "Verweisung auf andere Normen",
        },
        no_requirement="Keine",
    ),
}


def check_headers(rows: list[tuple], edition: Edition = EDITIONS["en"]) -> None:
    """Raise ValueError unless some row carries every expected header in its expected column."""
    first = edition.headers[COL_ID]
    for row in rows:
        if first in row:
            moved = {
                col: name for col, name in edition.headers.items()
                if col >= len(row) or row[col] != name
            }  # fmt: skip
            if not moved:
                return
            col, name = next(iter(moved.items()))
            found = row[col] if col < len(row) else None
            raise ValueError(
                f"{edition.sheet} sheet: expected header {name!r} in column "
                f"{col + 1}, found {found!r}"
            )
    raise ValueError(f"{edition.sheet} sheet: no header row with {first!r}")


def split_requirements(cell: str | None, edition: Edition = EDITIONS["en"]) -> list[str]:
    """One text per "+" bullet; lines that don't start with "+" continue the bullet above.

    Each text is a verbatim slice of the cell: only the "+ " marker and the whitespace
    after the bullet's last line are left out. An empty cell, or one saying the edition's
    word for none ("None", "Keine"), has no requirements.
    """
    if cell is None or cell.strip() == edition.no_requirement:
        return []
    if not cell.startswith("+"):
        raise ValueError(f"requirement cell does not start with a '+' bullet: {cell[:40]!r}")
    return [
        part.removeprefix("+").removeprefix(" ").rstrip() for part in re.split(r"\n(?=\+)", cell)
    ]


def iso_27001_reference(cell: str | None) -> str:
    """The ISO 27001 entry of the "Reference to other standards" cell, as written."""
    for entry in (cell or "").split("------"):
        if entry.strip().startswith("ISO") and "27001" in entry:
            return entry.strip()
    raise ValueError(f"no ISO 27001 reference in {(cell or '')[:40]!r}")


def extract(excel: Path | None = None, language: str = "en") -> dict:
    """The base framework in this language's edition as plain data, in CONTROL_IDS order,
    read from the edition's Excel, or from the one at `excel`."""
    import openpyxl  # dev dependency: see the module docstring

    edition = EDITIONS[language]
    excel = edition.excel if excel is None else excel
    workbook = openpyxl.load_workbook(excel, read_only=True)
    try:
        sheet_rows = list(workbook[edition.sheet].iter_rows(values_only=True))
    finally:
        workbook.close()  # read-only workbooks hold the file open until closed
    check_headers(sheet_rows, edition)
    rows: dict = {}
    for row in sheet_rows:
        if len(row) <= COL_ID or row[COL_ID] is None:
            continue
        if row[COL_ID] in rows:
            raise ValueError(f"{edition.sheet} sheet: control {row[COL_ID]} appears more than once")
        rows[row[COL_ID]] = row
    controls = []
    for control_id in CONTROL_IDS:
        if control_id not in rows:
            raise ValueError(f"{edition.sheet} sheet: no row for control {control_id}")
        row = rows[control_id]
        requirements = [
            {"id": f"{prefix}{n}", "column": column, "text": text}
            for column, prefix in COLUMN_PREFIX.items()
            for n, text in enumerate(
                split_requirements(row[REQUIREMENT_COL[column]], edition), start=1
            )
        ]
        controls.append({
            "id": control_id,
            "question": row[COL_QUESTION],
            "iso_27001": iso_27001_reference(row[COL_STANDARDS]),
            "requirements": requirements,
        })  # fmt: skip
    return {"source": SOURCE, "excel": excel.name, "controls": controls}


def main() -> int:
    """Extract every edition first, then write them all, so a failing edition leaves each
    controls file as it was."""
    files = isa.edition_files()
    texts = {}
    for language, edition in EDITIONS.items():
        header = (
            f"# Generated by `python -m vouch.isa_extract` from {edition.excel.name}; do not edit.\n"
            "# Texts are unchanged from the VDA ISA; licence and credit: NOTICE.md beside this file.\n"
        )
        data = extract(language=language)
        texts[language] = header + yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, width=1000
        )
    for language, text in texts.items():
        files[language].write_text(text, encoding="utf-8")
        print(f"wrote {files[language]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
