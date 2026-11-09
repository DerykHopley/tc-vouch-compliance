"""The readiness report as a PDF (#308): vouch.report_pdf.pdf writes the served report the
Markdown export writes, in the same words, on A4 with a bundled Unicode font. Its text is read
back with pypdf: each page's text, its whitespace collapsed, so a wrapped paragraph reads as one
line. The endpoint's own cases are in test_report.py, beside the Markdown export's.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from pypdf import PdfReader
from test_memory import app_on
from test_report import (
    built_report,
    entry,
    report_of,
    section,
    seed_nordlicht,
    table_rows,
)
from test_report import pdf_letterheads as letterheads
from test_report import pdf_pages as pages
from test_report import pdf_text as text

from vouch import audit, report, report_pdf
from vouch.graph import PROMPT_VERSION, Verdict
from vouch.model_choice import DEFAULT_MODEL
from vouch.profile import load_named_profile

users = test_memory.users
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# Alpenwerk's role concept, word for word (fixtures/alpenwerk/policies/berechtigungskonzept.md).
ROLES_QUOTE = (
    "Zugriffsrechte werden über Rollen vergeben, zum Beispiel „Konstruktion“, "
    "„Arbeitsvorbereitung“, „Einkauf“ oder „Buchhaltung“."
)


def accepted(built: dict[str, Any], index: int, *quotes: str, **fields: Any) -> None:
    """The report's control at `index` made accepted, citing `quotes` (the policy's v3.0, § 2)."""
    built["controls"][index] = built["controls"][index] | {
        "status": "accepted",
        "verdict": "partly-evidenced",
        "reviewer": "Dana Lead",
        "reviewed_at": "2026-11-05T10:00:00+00:00",
        "quotes": [
            {
                "doc_id": "berechtigungskonzept",
                "version": "3.0",
                "section": "2",
                "quote": quote,
                "kind": "documented",
                "stale": False,
            }
            for quote in quotes
        ],
        **fields,
    }


def alpenwerk_report() -> dict[str, Any]:
    """Alpenwerk's report, drawn up on 2026-11-06, with its first control accepted on a quote
    carrying „ “."""
    profile = load_named_profile("alpenwerk")
    built = dict(
        report.readiness_report(
            profile,
            profile.isa_edition().controls,
            {},
            decided={},
            trail=audit.Page([], None),
            today=datetime(2026, 11, 6, tzinfo=UTC).date(),
        )
    )
    accepted(built, 0, ROLES_QUOTE)
    return built


def test_alpenwerks_pdf_is_german_with_its_umlauts_and_quote_marks_as_written():
    data = report_pdf.pdf(alpenwerk_report(), report.words_for("de"))

    assert data.startswith(b"%PDF-")
    read = text(data)
    assert read.startswith("BEREITSCHAFTSBERICHT")  # the title in capitals, as the app has it
    for label in ("Länderergänzungen", "Geprüft von", "Maßnahmen", "Prüfpfad"):
        assert label in read, label
    assert ROLES_QUOTE in read
    assert "Land: Österreich" in read and "AT NIS2 (illustrativ)" in read  # #379
    assert "Readiness" not in read and "Reviewed by" not in read  # no English word


def test_the_pdf_carries_the_markdowns_controls_remediation_and_disclaimer(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        seed_nordlicht(client, alice)
        shown = report_of(client, alice)
    words = shown.pop("words")
    md = report.markdown(shown, words)

    read = text(report_pdf.pdf(shown, words))

    controls = table_rows(section(md, "Controls"))
    assert len(controls) == 12
    for control_id, _question, status, verdict, reviewer, day in controls:
        assert control_id in read
        assert status in read
        if status == "Accepted":
            assert f"{verdict} {reviewer} {day}" in read, control_id
    items = table_rows(section(md, "Remediation items"))
    assert items
    for requirement, missing, owner_role, due_date in items:
        assert f"{requirement} " in read
        assert " ".join(missing.split()) in read
        assert f"{owner_role} {due_date}" in read
    assert read.endswith(shown["disclaimer"])
    assert report.remediation_count(shown["readiness"]["remediation_items"], words) in read


def test_with_nothing_accepted_the_pdf_has_each_empty_lists_sentence():
    built = built_report()
    built["not_applicable"] = []  # Nordlicht's one entry left out

    read = text(report_pdf.pdf(built, report.words_for("en")))

    sentences = report.words_for("en")["sentences"]
    for key in ("no_remediation", "no_not_applicable", "no_trail", "no_overlays"):
        assert sentences[key] in read, key
    assert read.endswith(sentences["disclaimer_nothing_reviewed"])
    assert "Quotes" not in read


def test_a_long_report_runs_onto_more_pages_with_each_tables_header_on_each_page():
    built = built_report(entries=[replace(entry("u", n), id=n + 1) for n in range(80)])
    first = built["controls"][0]
    built["controls"] = [first | {"id": f"C-{n:03}"} for n in range(60)]
    labels = report.words_for("en")["labels"]
    tables = {
        "C-": ["control", "question", "status", "verdict", "reviewer", "reviewed_at"],
        "Entry ": ["at", "who", "action", "verdict_before", "note", "model", "prompt_version"],
    }

    read = pages(report_pdf.pdf(built, report.words_for("en")))

    assert len(read) >= 3
    for marker, columns in tables.items():
        spans = [page for page in read if marker in page]
        assert len(spans) >= 2, marker
        for page in spans:
            for column in columns:
                for word in labels[column].split():
                    assert word in page, (marker, word)


def test_a_quote_with_a_character_the_font_lacks_still_gives_the_pdf_without_it():
    built = alpenwerk_report()
    accepted(built, 1, "Geprüft 🙂 „gut“ – fertig.")

    data = report_pdf.pdf(built, report.words_for("de"))

    read = text(data)
    assert "Geprüft „gut“ – fertig." in read
    assert "🙂" not in read


# --- Long carried text (#308 review): a row taller than a page would stop fpdf2 ---------------

LONG_NOTE = " ".join(f"Prüfschritt {n} mit dem ISB besprochen." for n in range(80))  # ~3000


def test_a_trail_note_of_60_lines_still_gives_the_pdf_with_every_line():
    note = "".join(f"ok {n}\n" for n in range(60))
    built = built_report(entries=[replace(entry("u", 1), id=1, note=note)])

    read = text(report_pdf.pdf(built, report.words_for("en")))

    assert " ".join(note.split()) in read


def test_a_trail_note_of_3000_characters_still_gives_the_pdf_with_all_of_it():
    assert len(LONG_NOTE) > 3000
    built = built_report(entries=[replace(entry("u", 1), id=1, note=LONG_NOTE)])

    read = text(report_pdf.pdf(built, report.words_for("de")))

    assert LONG_NOTE in read


def test_a_long_missing_text_and_company_still_give_the_pdf_with_all_of_it():
    built = alpenwerk_report()
    built["head"] = built["head"] | {"company": LONG_NOTE}
    built["remediation"] = [
        {
            "control_id": "1.1.1",
            "question": "Q?",
            "items": [
                {
                    "requirement_id": "M1",
                    "missing": "Fehlt\n" * 70 + LONG_NOTE,
                    "owner_role": "ISB",
                    "due_date": "2026-12-01",
                }
            ],
        }
    ]

    read = text(report_pdf.pdf(built, report.words_for("de")))

    assert read.count(LONG_NOTE) == 2
    assert "Fehlt " * 70 in read


# --- The audit trail's narrow columns (#353): no word broken mid-word -------------------------

ACTIONS: tuple[audit.Action, ...] = ("accept", "reject", "override", "approve", "discard")
VERDICTS: tuple[Verdict, ...] = ("evidenced", "partly-evidenced", "not-evidenced", "not-applicable")


def fixture_trail(profile_id: str) -> list[audit.AuditEntry]:
    """A trail with every id the profile's PDF can carry: each of its policies as evidence, each
    action and verdict, the default model and the assessment's prompt version."""
    docs = sorted(p.stem for p in (FIXTURES / profile_id / "policies").glob("*.md"))
    return [
        replace(
            entry("u", n),
            id=n + 1,
            profile_id=profile_id,
            control_id="5.2.4",
            requirement_id="M1" if n % 2 else None,
            action=ACTIONS[n % len(ACTIONS)],
            verdict_before=VERDICTS[n % len(VERDICTS)],
            verdict_after=VERDICTS[(n + 1) % len(VERDICTS)],
            evidence=(audit.EvidenceVersion(doc, "2.1"),),
            model=DEFAULT_MODEL,
            prompt_version=PROMPT_VERSION,
        )
        for n, doc in enumerate(docs)
    ]


def at_a_boundary(cell: str, lines: list[str]) -> list[str]:
    """The lines (all but the last) that end inside a word of `cell`: not before a space, and
    not after a "-" or "/". A BREAK, drawn as nothing, is left out of both."""
    cell = cell.replace(report_pdf.BREAK, "")
    broken, at = [], 0
    for line in (line.replace(report_pdf.BREAK, "") for line in lines[:-1]):
        at = cell.index(line, at) + len(line)
        if not (line.endswith(("-", "/")) or cell[at : at + 1] == " "):
            broken.append(line)
    return broken


def breaks_inside_words(sheet, head, cells, widths) -> list[str]:
    """Each trail cell's line that ends inside a word, as the table draws it."""
    broken = []
    for bold, row in [(True, head), *((False, r) for r in cells)]:
        for i, (cell, width) in enumerate(zip(row, widths, strict=True)):
            ids = not bold and i in report_pdf.TRAIL_IDS  # drawn in Plex Mono
            sheet.font(report_pdf.TRAIL, bold, report_pdf.MONO if ids else report_pdf.SANS)
            lines = sheet.wrapped(cell, width)
            broken += [f"{cell!r} at {line!r}" for line in at_a_boundary(cell, lines)]
    return broken


@pytest.mark.parametrize(("profile_id", "language"), [("nordlicht", "en"), ("alpenwerk", "de")])
def test_no_audit_trail_cell_breaks_a_word_and_the_trail_fits_the_page(profile_id, language):
    words = report.words_for(language)
    columns = report.trail_columns(words)
    body = report.trail_rows([e.to_dict() for e in fixture_trail(profile_id)], words)
    sheet = report_pdf._Sheet()
    sheet.add_page(orientation="landscape")

    head, cells, widths = report_pdf.trail_table(sheet, columns, body)

    assert sum(widths) <= sheet.epw + 0.01
    assert breaks_inside_words(sheet, head, cells, widths) == []

    data = report_pdf.pdf(built_report(entries=fixture_trail(profile_id)), words)
    assert PROMPT_VERSION in text(data)  # the PDF still draws, with its whole ids


def test_ids_too_long_to_fit_whole_break_only_after_a_dash_or_slash():
    # Every column's longest id at once: whole, they're wider than the page.
    long_id = "information-security-incident-management/procedure-annex-2026-v12"
    words = report.words_for("de")
    columns = report.trail_columns(words)
    body = [[long_id] * len(columns)]
    sheet = report_pdf._Sheet()
    sheet.add_page(orientation="landscape")

    head, cells, widths = report_pdf.trail_table(sheet, columns, body)

    assert sum(widths) <= sheet.epw + 0.01
    assert report_pdf.BREAK in cells[0][0]
    assert breaks_inside_words(sheet, head, cells, widths) == []
    built = built_report(entries=[replace(entry("u", 1), id=1, model=long_id)])
    shown = text(report_pdf.pdf(built, report.words_for("en"))).replace(report_pdf.BREAK, "")
    assert long_id in shown  # the id as written, its BREAKs drawn as nothing


def test_a_note_with_a_token_too_long_for_any_column_breaks_only_that_token():
    # #353's verify: a 96-character hash in a note once shrank every column, so even the headers
    # broke mid-word. Now only the note's own column gives way.
    token = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" + "0" * 32
    words = report.words_for("en")
    body = report.trail_rows(
        [replace(e, note=f"Hash {token}").to_dict() for e in fixture_trail("nordlicht")], words
    )
    sheet = report_pdf._Sheet()
    sheet.add_page(orientation="landscape")

    head, cells, widths = report_pdf.trail_table(sheet, report.trail_columns(words), body)

    assert sum(widths) <= sheet.epw + 0.01
    broken = breaks_inside_words(sheet, head, cells, widths)
    assert broken and all(token[:8] in line for line in broken)  # the token's cells, no other


# --- The letterhead (#352): on every page, clear of the tables ----------------------------------


def long_report(profile_id: str) -> dict[str, Any]:
    """The profile's report running onto several pages: a long trail, on a landscape page too."""
    built = alpenwerk_report() if profile_id == "alpenwerk" else built_report()
    entries = [replace(e, id=n + 1) for n, e in enumerate(fixture_trail(profile_id) * 6)]
    built["trail"] = built["trail"] | {"entries": [e.to_dict() for e in entries]}
    return built


@pytest.mark.parametrize(
    ("profile_id", "language", "company", "footer"),
    [
        ("nordlicht", "en", "Nordlicht Komponenten GmbH", "Drawn up on 2026-11-06 Page {} of {}"),
        ("alpenwerk", "de", "Alpenwerk Präzision GmbH", "Erstellt am 2026-11-06 Seite {} von {}"),
    ],
)
def test_every_page_has_the_letterhead_and_page_x_of_y_with_the_date(
    profile_id, language, company, footer
):
    data = report_pdf.pdf(long_report(profile_id), report.words_for(language))

    shown = letterheads(data)

    assert len(shown) >= 3
    for n, (head, foot) in enumerate(shown, 1):
        assert head == f"VOUCH {company}"
        assert foot == footer.format(n, len(shown))


def test_no_text_falls_in_the_letterhead_or_the_footer_on_any_page():
    data = report_pdf.pdf(long_report("alpenwerk"), report.words_for("de"))
    mm = 72 / 25.4
    for number, page in enumerate(PdfReader(BytesIO(data)).pages, 1):
        height = float(page.mediabox.height)
        top, bottom = height - report_pdf.TOP * mm, report_pdf.BOTTOM * mm
        outside: list[str] = []

        def seen(text, cm, tm, font, size, outside=outside, top=top, bottom=bottom):
            y = tm[5] * cm[3] + cm[5]
            if text.strip() and not bottom <= y <= top:
                outside.append(text)

        page.extract_text(visitor_text=seen)
        drawn = re.sub(r"\s+", "", "".join(outside))
        head, foot = letterheads(data)[number - 1]
        assert drawn == re.sub(r"\s+", "", head + foot), number  # only the letterhead's own


def test_the_pdfs_colours_are_the_light_themes_tokens():
    css = (Path(__file__).resolve().parents[1] / "frontend/src/styles/tokens.css").read_text()
    light = dict(re.findall(r"--([a-z-]+): (#[0-9A-Fa-f]{6})", css.split("}", 1)[0]))
    used = {
        "ink": report_pdf.INK,
        "muted": report_pdf.MUTED,
        "rule": report_pdf.RULE,
        "brass": report_pdf.BRASS,
    }

    assert {name: light[name] for name in used} == used


def test_the_pdf_is_set_in_the_apps_type():
    # The owner (#352): "the main content should follow the general font styles".
    data = report_pdf.pdf(long_report("alpenwerk"), report.words_for("de"))
    used = set()
    for page in PdfReader(BytesIO(data)).pages:
        fonts = page["/Resources"]["/Font"]
        used |= {str(fonts[name].get_object()["/BaseFont"]) for name in fonts}

    for family in ("LibreCaslonDisplay", "Manrope", "IBMPlexMono", "IBMPlexSerif"):
        assert any(family in name for name in used), (family, used)


def test_a_quote_keeps_a_character_its_italic_lacks_and_a_long_company_stays_clear_of_vouch():
    # #352's review: Plex Serif italic has no α or ✗; DejaVu draws them, in its upright style.
    built = alpenwerk_report()
    accepted(built, 0, "Die Rolle α ist gesperrt ✗ und wird jährlich überprüft.")
    company = "Alpenwerk Präzisions- und Sondermaschinenbau GmbH, " * 4  # wider than the line
    built["head"] = built["head"] | {"company": company}

    data = report_pdf.pdf(built, report.words_for("de"))

    assert "Die Rolle α ist gesperrt ✗ und wird jährlich überprüft." in text(data)
    head, _ = letterheads(data)[0]
    assert head.startswith("VOUCH Alpenwerk") and head.endswith("…")  # clipped, one line
