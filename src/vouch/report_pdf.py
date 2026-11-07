"""The readiness report as a PDF (#308), built on the server with fpdf2 (docs/requirements.md,
2026-11-07): the served report the Markdown export writes (vouch.report.markdown), in the same
words and the page's order, so the two downloads never disagree.

Plain, black on white, on A4: the title, the head as label and value lines, readiness, the
controls table and each accepted control's quotes, the remediation items under their control,
the not-applicable list, the audit trail table (on landscape A4 pages, for its 11 columns) and
the disclaimer at the foot. Each table's
header row repeats on every page it runs onto, its cells wrap, and fpdf2 never splits a row
across pages.

The text is drawn in DejaVu Sans (fonts/, with its licence), as fpdf2's built-in fonts are
Latin-1 only and the German editions' quotes carry „ “ and –. A character the font lacks (an
emoji in a quote) is left out, so the PDF is still served. Carried text stays as written: unlike
Markdown, nothing in a PDF's text can turn into a heading or live HTML, so nothing is escaped.

fpdf2 is imported here only, keeping it out of vouch.report.
"""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.fonts import FontFace, TTFFont

from vouch.report import day_of, minute_of, remediation_count

FONTS = Path(__file__).parent / "fonts"
FAMILY = "dejavu"
MARGIN = 15  # mm, on every side
BODY = 9  # pt
TABLE = 8  # pt
TRAIL = 7  # pt: the trail's 11 columns, on landscape pages
HEADINGS = FontFace(emphasis="BOLD", fill_color=None)


class _Sheet(FPDF):
    """An A4 page in DejaVu Sans that draws only the characters the font has."""

    def __init__(self) -> None:
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=MARGIN)
        self.add_font(FAMILY, "", FONTS / "DejaVuSans.ttf")
        self.add_font(FAMILY, "B", FONTS / "DejaVuSans-Bold.ttf")
        regular, bold = self.fonts[FAMILY], self.fonts[f"{FAMILY}B"]
        assert isinstance(regular, TTFFont) and isinstance(bold, TTFFont)
        self._glyphs = set(regular.cmap) & set(bold.cmap)  # drawn in either weight

    def drawable(self, text: object) -> str:
        """The text without the characters the font lacks; line breaks kept."""
        return "".join(c for c in str(text) if c == "\n" or ord(c) in self._glyphs)

    def font(self, size: float, bold: bool = False) -> None:
        self.set_font(FAMILY, "B" if bold else "", size)

    def paragraph(self, text: object, size: float = BODY, bold: bool = False, indent: float = 0):
        self.font(size, bold)
        self.set_x(self.l_margin + indent)
        self.multi_cell(
            self.epw - indent,
            size * 0.5,
            self.drawable(text),
            align="L",
            new_x="LMARGIN",
            new_y="NEXT",
        )

    def heading(self, text: object, size: float) -> None:
        """A heading, never alone at a page's foot: a page break first when little room is left."""
        if self.y + 25 > self.page_break_trigger:
            self.add_page()
        self.ln(size * 0.4)
        self.paragraph(text, size, bold=True)
        self.ln(1.5)

    def grid(
        self,
        columns: Sequence[str],
        rows: Iterable[Sequence[object]],
        widths: Sequence[float],
        size: float = TABLE,
    ) -> None:
        """A table: its header row on each page it runs onto, each cell's text wrapped."""
        self.font(size)
        with self.table(
            col_widths=tuple(widths),
            headings_style=HEADINGS,
            repeat_headings=1,
            line_height=size * 0.45,
            padding=1,
            text_align="LEFT",
        ) as table:
            for row in [columns, *rows]:
                cells = table.row()
                for value in row:
                    cells.cell(self.drawable(value))
        self.ln(2)


def pdf(report: Mapping[str, Any], words: Mapping[str, Any]) -> bytes:
    """The served report (`vouch.report.readiness_report`, redacted) as a PDF, in its `words`
    (`vouch.report.words_for`): the page's parts in the page's order, an empty list's sentence in
    its place. No model, no database."""
    labels, sentences = words["labels"], words["sentences"]
    sheet = _Sheet()
    sheet.set_title(sheet.drawable(words["title"]))
    sheet.set_lang(report["head"]["words_language"])
    sheet.set_creator("Vouch")
    sheet.add_page()

    sheet.paragraph(words["title"], 16, bold=True)
    sheet.ln(2)
    _head(sheet, report["head"], words)

    sheet.heading(labels["readiness"], 12)
    _readiness(sheet, report["readiness"], words)

    sheet.heading(labels["controls"], 12)
    _controls(sheet, report["controls"], words)

    sheet.heading(labels["remediation"], 12)
    if report["remediation"]:
        _remediation(sheet, report["remediation"], words)
    else:
        sheet.paragraph(sentences["no_remediation"])

    sheet.heading(labels["not_applicable"], 12)
    if report["not_applicable"]:
        _not_applicable(sheet, report["not_applicable"], words)
    else:
        sheet.paragraph(sentences["no_not_applicable"])

    if report["trail"]["entries"]:
        # Its 11 columns need the width: the trail starts a landscape A4 page.
        sheet.add_page(orientation="landscape")
        sheet.heading(labels["trail"], 12)
        _trail(sheet, report["trail"]["entries"], words)
    else:
        sheet.heading(labels["trail"], 12)
        sheet.paragraph(sentences["no_trail"])

    sheet.ln(4)
    sheet.line(sheet.l_margin, sheet.y, sheet.w - sheet.r_margin, sheet.y)
    sheet.ln(2)
    sheet.paragraph(report["disclaimer"])
    return bytes(sheet.output())


def _head(sheet: _Sheet, head: Mapping[str, Any], words: Mapping[str, Any]) -> None:
    labels = words["labels"]
    overlays = ", ".join(head["overlays"]) or words["sentences"]["no_overlays"]
    rows = [
        (labels["company"], head["company"]),
        (labels["country"], head["country"]),
        (labels["base_framework"], head["base_framework"]),
        (labels["protection_level"], words["protection_levels"][head["protection_level"]]),
        (labels["overlays"], overlays),
        # The words name English and German only: another answer language shows as its code.
        (
            labels["answer_language"],
            words["languages"].get(head["answer_language"], head["answer_language"]),
        ),
        (labels["date"], head["date"]),
    ]
    sheet.font(BODY)
    with sheet.table(
        col_widths=(45, sheet.epw - 45),
        first_row_as_headings=False,
        borders_layout="NONE",
        line_height=BODY * 0.5,
        padding=(0.5, 1, 0.5, 0),
        text_align="LEFT",
    ) as table:
        for label, value in rows:
            row = table.row()
            row.cell(sheet.drawable(f"{label}:"), style=HEADINGS)
            row.cell(sheet.drawable(value))


def _readiness(sheet: _Sheet, r: Mapping[str, Any], words: Mapping[str, Any]) -> None:
    sentence = words["sentences"]["accepted_of"].format(
        accepted=r["accepted"], controls=r["controls"]
    )
    sheet.paragraph(sentence)
    sheet.ln(1)
    counts = [f"{n} {words['verdicts'][v]}" for v, n in r["by_verdict"].items()]
    counts += [
        f"{r['open']} {words['statuses']['open']}",
        remediation_count(r["remediation_items"], words),
    ]
    for count in counts:
        sheet.paragraph(f"• {count}", indent=3)


def _controls(sheet: _Sheet, controls: list[Mapping[str, Any]], words: Mapping[str, Any]) -> None:
    """The controls table, then each accepted control's quotes under a heading of its own."""
    labels = words["labels"]
    columns = ["control", "question", "status", "verdict", "reviewer", "reviewed_at"]
    rows: list[list[object]] = []
    for c in controls:
        status = words["statuses"][c["status"]]
        if c["status"] == "accepted" and c["verdict"]:
            reviewed = day_of(c["reviewed_at"]) if c["reviewed_at"] else ""
            verdict = words["verdicts"][c["verdict"]]
            rows.append([c["id"], c["question"], status, verdict, c["reviewer"] or "", reviewed])
        else:
            open_control = words["sentences"]["open_control"]
            rows.append([c["id"], c["question"], status, open_control, "", ""])
    sheet.grid([labels[k] for k in columns], rows, (18, 58, 20, 34, 30, 20))
    for c in controls:
        if c["status"] == "accepted" and c["quotes"]:
            sheet.heading(f"{labels['quotes']} {c['id']}", 10)
            for q in c["quotes"]:
                _quote(sheet, q, words)


def _quote(sheet: _Sheet, q: Mapping[str, Any], words: Mapping[str, Any]) -> None:
    """A quote, indented, line by line as written, then its source."""
    source = f"{q['doc_id']} v{q['version']} · {q['section']}"
    if q["stale"]:
        source += f" · {words['labels']['stale']}"
    sheet.paragraph(q["quote"], indent=5)
    sheet.paragraph(f"— {source}", TABLE, indent=5)
    sheet.ln(2)


def _remediation(sheet: _Sheet, groups: list[Mapping[str, Any]], words: Mapping[str, Any]):
    labels = words["labels"]
    columns = [labels[k] for k in ("requirement", "missing", "owner_role", "due_date")]
    for group in groups:
        sheet.heading(f"{group['control_id']} {group['question']}", 10)
        rows = [
            [i["requirement_id"], i["missing"], i["owner_role"], i["due_date"]]
            for i in group["items"]
        ]
        sheet.grid(columns, rows, (24, 94, 38, 24))


def _not_applicable(sheet: _Sheet, listed: list[Mapping[str, Any]], words: Mapping[str, Any]):
    reason = words["labels"]["reason"]
    for r in listed:
        sheet.paragraph(f"{r['control_id']} {r['requirement_id']}", bold=True)
        sheet.paragraph(r["text"])
        sheet.paragraph(f"{reason}: {r['reason']}", indent=5)
        sheet.ln(2)


def _trail(sheet: _Sheet, entries: list[Mapping[str, Any]], words: Mapping[str, Any]) -> None:
    labels, verdicts = words["labels"], words["verdicts"]
    columns = [
        "at",
        "who",
        "control",
        "requirement",
        "action",
        "verdict_before",
        "verdict_after",
        "note",
        "evidence",
        "model",
        "prompt_version",
    ]
    rows = [
        [
            minute_of(e["at"]),
            e["reviewer_name"],
            e["control_id"],
            e["requirement_id"] or "",
            words["actions"][e["action"]],
            verdicts[e["verdict_before"]] if e["verdict_before"] else "",
            verdicts[e["verdict_after"]] if e["verdict_after"] else "",
            e["note"],
            ", ".join(f"{d['doc_id']} v{d['version']}" for d in e["evidence"]),
            e["model"],
            e["prompt_version"],
        ]
        for e in entries
    ]
    widths = (22, 22, 16, 20, 20, 26, 26, 44, 29, 20, 22)  # 267 mm: landscape A4's width
    sheet.grid([labels[k] for k in columns], rows, widths, TRAIL)
