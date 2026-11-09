"""The readiness report as a PDF (#308), built on the server with fpdf2 (docs/requirements.md,
2026-11-07): the served report the Markdown export writes (vouch.report.markdown), from the
same rows (vouch.report's head_rows, control_rows and the rest) in the same words and the page's
order, so the two downloads never disagree.

Plain, black on white, on A4: the title, the head as label and value lines, readiness, the
controls table and each accepted control's quotes, the remediation items under their control,
the not-applicable list, the audit trail table (on landscape A4 pages, for its 11 columns) and
the disclaimer at the foot. Each table's header row repeats on every page it runs onto, its
cells wrap, and fpdf2 never splits a row across pages.

fpdf2 can't draw a row taller than a page, and carried text has no length limit (a lead's note,
a remediation item's missing text). So a cell shows at most MAX_CELL_LINES lines, on one line
of text as the Markdown's cells are; a longer one ends in `… [n]`, and its whole text, line by
line as written, follows the table as note [n]. The PDF always builds.

The text is drawn in DejaVu Sans (fonts/, with its licence), as fpdf2's built-in fonts are
Latin-1 only and the German editions' quotes carry „ “ and –. A character the font lacks (an
emoji in a quote) is left out, so the PDF is still served. Carried text stays as written: unlike
Markdown, nothing in a PDF's text can turn into a heading or live HTML, so nothing is escaped.

fpdf2 is imported here only, keeping it out of vouch.report.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import MethodReturnValue
from fpdf.fonts import FontFace, TTFFont

from vouch import report as rows

FONTS = Path(__file__).parent / "fonts"
FAMILY = "dejavu"
MARGIN = 15  # mm, on every side
TITLE = 16  # pt
SECTION = 12  # pt: Readiness, Controls and the other parts
SUBSECTION = 10  # pt: a control's quotes, a control's remediation items
BODY = 9  # pt
TABLE = 8  # pt
TRAIL = 7  # pt: the trail's 11 columns, on landscape pages
KEEP_WITH_NEXT = 25  # mm: a heading with less room than this below it starts a new page
MAX_CELL_LINES = 12  # a row of 12 lines is well under a page, in any column and font size
CELL_PADDING = 1  # mm
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
        self._notes = 0  # the long cells' notes so far, numbered through the document

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
        if self.y + KEEP_WITH_NEXT > self.page_break_trigger:
            self.add_page()
        self.ln(size * 0.4)
        self.paragraph(text, size, bold=True)
        self.ln(1.5)

    def wrapped(self, text: object, width: float, size: float, bold: bool = False) -> list[str]:
        """The lines a table cell `width` mm wide draws `text` in, in this font size and weight."""
        self.font(size, bold)
        lines = self.multi_cell(
            width,
            1,
            self.drawable(text),
            dry_run=True,
            output=MethodReturnValue.LINES,
            padding=CELL_PADDING,
        )
        assert isinstance(lines, list)  # output=LINES: the wrapped lines
        return lines

    def cell_text(self, text: object, width: float, notes: list[tuple[int, str]]) -> str:
        """A cell's text in the current font: on one line of text (each line break a space),
        cut to MAX_CELL_LINES lines of `width` mm ending in `… [n]` when longer, its whole text
        then added to `notes` as note n."""
        whole = self.drawable(text)
        flat = " ".join(whole.split())
        inner = width - 2 * CELL_PADDING - 0.5  # the border's width, and some slack
        lines = self.multi_cell(
            inner, 1, flat, dry_run=True, output=MethodReturnValue.LINES, padding=0
        )
        assert isinstance(lines, list)  # output=LINES: the wrapped lines
        if len(lines) <= MAX_CELL_LINES:
            return flat
        self._notes += 1
        notes.append((self._notes, whole))
        kept = sum(len(line) + 1 for line in lines[: MAX_CELL_LINES - 1])
        return f"{flat[:kept].rstrip()} … [{self._notes}]"

    def grid(
        self,
        columns: Sequence[str],
        body: Iterable[Sequence[object]],
        widths: Sequence[float],
        size: float = TABLE,
    ) -> None:
        """A table: its header row on each page it runs onto, each cell's text wrapped, a long
        cell's whole text after it as a note."""
        self.font(size)
        notes: list[tuple[int, str]] = []
        cells = [
            [self.cell_text(v, w, notes) for v, w in zip(r, widths, strict=True)] for r in body
        ]
        with self.table(
            col_widths=tuple(widths),
            headings_style=HEADINGS,
            repeat_headings=1,
            line_height=size * 0.45,
            padding=CELL_PADDING,
            text_align="LEFT",
        ) as table:
            for values in [list(columns), *cells]:
                row = table.row()
                for value in values:
                    row.cell(value)
        self.ln(2)
        self.notes(notes, size)

    def notes(self, notes: list[tuple[int, str]], size: float) -> None:
        for n, whole in notes:
            self.paragraph(f"[{n}] {whole}", size)
            self.ln(1)


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

    sheet.paragraph(words["title"], TITLE, bold=True)
    sheet.ln(2)
    _head(sheet, report["head"], words)

    sheet.heading(labels["readiness"], SECTION)
    sentence, counts = rows.readiness_lines(report["readiness"], words)
    sheet.paragraph(sentence)
    sheet.ln(1)
    for count in counts:
        sheet.paragraph(f"• {count}", indent=3)

    sheet.heading(labels["controls"], SECTION)
    _controls(sheet, report["controls"], words)

    sheet.heading(labels["remediation"], SECTION)
    for group in report["remediation"]:
        sheet.heading(f"{group['control_id']} {group['question']}", SUBSECTION)
        sheet.grid(rows.remediation_columns(words), rows.remediation_rows(group), (24, 94, 38, 24))
    if not report["remediation"]:
        sheet.paragraph(sentences["no_remediation"])

    sheet.heading(labels["not_applicable"], SECTION)
    for r in report["not_applicable"]:
        sheet.paragraph(f"{r['control_id']} {r['requirement_id']}", bold=True)
        sheet.paragraph(r["text"])
        sheet.paragraph(f"{labels['reason']}: {r['reason']}", indent=5)
        sheet.ln(2)
    if not report["not_applicable"]:
        sheet.paragraph(sentences["no_not_applicable"])

    entries = report["trail"]["entries"]
    if entries:
        # Its 11 columns need the width: the trail starts a landscape A4 page.
        sheet.add_page(orientation="landscape")
        sheet.heading(labels["trail"], SECTION)
        head, body, widths = trail_table(
            sheet, rows.trail_columns(words), rows.trail_rows(entries, words)
        )
        sheet.grid(head, body, widths, TRAIL)
    else:
        sheet.heading(labels["trail"], SECTION)
        sheet.paragraph(sentences["no_trail"])

    sheet.ln(4)
    sheet.line(sheet.l_margin, sheet.y, sheet.w - sheet.r_margin, sheet.y)
    sheet.ln(2)
    sheet.paragraph(report["disclaimer"])
    return bytes(sheet.output())


_PIECE = re.compile(r"[^\s/-]*[/-]?")
"""A piece of an id a line may break after: up to and with a "-" or "/"."""
BREAK = "\u200b"
"""A zero-width space: where a line may break inside an id, after its "-" or "/"."""


def trail_table(
    sheet: _Sheet, columns: Sequence[str], body: Sequence[Sequence[object]]
) -> tuple[list[str], list[list[str]], tuple[float, ...]]:
    """The audit trail's header, rows and column widths across the page (#353), so no line
    breaks inside a word. Each column is at least as wide as its widest word or id (a header's
    in bold); the rest of the page is shared out by how much more each column's longest cell
    wants. When the words can't all fit, an id may break after a "-" or "/" instead (a BREAK
    there), each column then as wide as its widest piece; past that, the page is shared."""
    head = [str(c) for c in columns]
    rows = [[str(v) for v in r] for r in body]
    widths = _fitted(sheet, head, rows, str.split)
    if widths is None:
        head = [_breakable(c) for c in head]
        rows = [[_breakable(v) for v in r] for r in rows]
        widths = _fitted(sheet, head, rows, lambda text: _PIECE.findall(text.replace(BREAK, "")))
    if widths is None:  # pieces too wide for the page: scaled to it
        least = _least(sheet, head, rows, lambda text: _PIECE.findall(text.replace(BREAK, "")))
        widths = tuple(w * sheet.epw / sum(least) for w in least)
    return head, rows, widths


def _breakable(text: str) -> str:
    return re.sub(r"(?<=[/-])(?=\S)", BREAK, text)


def _width(sheet: _Sheet, text: str, bold: bool) -> float:
    sheet.font(TRAIL, bold)
    return sheet.get_string_width(sheet.drawable(text)) + 2 * CELL_PADDING + 2 * sheet.c_margin


def _columns(head: list[str], rows: list[list[str]]) -> list[list[tuple[str, bool]]]:
    return [[(c, True)] + [(r[i], False) for r in rows] for i, c in enumerate(head)]


def _least(
    sheet: _Sheet, head: list[str], rows: list[list[str]], parts: Callable[[str], list[str]]
) -> list[float]:
    return [
        max([_width(sheet, p, bold) for text, bold in col for p in parts(text) if p], default=0)
        for col in _columns(head, rows)
    ]


def _fitted(
    sheet: _Sheet, head: list[str], rows: list[list[str]], parts: Callable[[str], list[str]]
) -> tuple[float, ...] | None:
    """Widths at least the widest of each column's `parts`, the rest of the page shared by
    what each column's longest cell wants on one line; None when the parts don't fit."""
    least = _least(sheet, head, rows, parts)
    spare = sheet.epw - sum(least)
    if spare < 0:
        return None
    whole = [
        max(_width(sheet, " ".join(t.replace(BREAK, "").split()), b) for t, b in col)
        for col in _columns(head, rows)
    ]
    wants = [max(w - m, 0) for w, m in zip(whole, least, strict=True)]
    share = spare / (sum(wants) or 1)
    given = [m + min(want, want * share) for m, want in zip(least, wants, strict=True)]
    left = (sheet.epw - sum(given)) / len(given)  # every cell on one line: the rest evenly
    return tuple(g + left for g in given)


def _head(sheet: _Sheet, head: Mapping[str, Any], words: Mapping[str, Any]) -> None:
    """The head as label and value lines: a table without borders."""
    widths = (45, sheet.epw - 45)
    sheet.font(BODY)
    notes: list[tuple[int, str]] = []
    pairs = [
        (sheet.drawable(f"{label}:"), sheet.cell_text(value, widths[1], notes))
        for label, value in rows.head_rows(head, words)
    ]
    with sheet.table(
        col_widths=widths,
        first_row_as_headings=False,
        borders_layout="NONE",
        line_height=BODY * 0.5,
        padding=(0.5, CELL_PADDING, 0.5, 0),
        text_align="LEFT",
    ) as table:
        for label, value in pairs:
            row = table.row()
            row.cell(label, style=HEADINGS)
            row.cell(value)
    sheet.notes(notes, BODY)


def _controls(sheet: _Sheet, controls: list[Mapping[str, Any]], words: Mapping[str, Any]) -> None:
    """The controls table, then each accepted control's quotes under a heading of its own."""
    widths = (18, 58, 20, 34, 30, 20)
    sheet.grid(rows.control_columns(words), rows.control_rows(controls, words), widths)
    for c in rows.quoted(controls):
        sheet.heading(f"{words['labels']['quotes']} {c['id']}", SUBSECTION)
        for q in c["quotes"]:
            sheet.paragraph(q["quote"], indent=5)  # line by line as written
            sheet.paragraph(f"— {rows.quote_source(q, words)}", TABLE, indent=5)
            sheet.ln(2)
