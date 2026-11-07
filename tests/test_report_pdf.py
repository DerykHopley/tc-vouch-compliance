"""The readiness report as a PDF (#308): vouch.report_pdf.pdf writes the served report the
Markdown export writes, in the same words, on A4 with a bundled Unicode font. Its text is read
back with pypdf: each page's text, its whitespace collapsed, so a wrapped paragraph reads as one
line. The endpoint's own cases are in test_report.py, beside the Markdown export's.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import test_memory
from fastapi.testclient import TestClient
from test_memory import app_on
from test_report import (
    built_report,
    entry,
    report_of,
    section,
    seed_nordlicht,
    table_rows,
)
from test_report import pdf_pages as pages
from test_report import pdf_text as text

from vouch import audit, report, report_pdf
from vouch.profile import load_named_profile

users = test_memory.users

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
    assert read.startswith("Bereitschaftsbericht")
    for label in ("Länderergänzungen", "Geprüft von", "Maßnahmen", "Prüfpfad"):
        assert label in read, label
    assert ROLES_QUOTE in read
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
