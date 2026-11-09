"""The readiness report as a PDF (#308), built on the server with fpdf2 (docs/requirements.md,
2026-11-07): the served report the Markdown export writes (vouch.report.markdown), from the
same rows (vouch.report's head_rows, control_rows and the rest) in the same words and the page's
order, so the two downloads never disagree.

Black on white, on A4, under a letterhead on every page (#352: Vouch and the company over a thin
brass rule; the date and "Page x of y" in the footer): the title, the head as label and value
lines, readiness, the
controls table and each accepted control's quotes, the remediation items under their control,
the not-applicable list, the audit trail table (on landscape A4 pages, for its 11 columns) and
the disclaimer at the foot. Each table's header row repeats on every page it runs onto, its
cells wrap, and fpdf2 never splits a row across pages.

fpdf2 can't draw a row taller than a page, and carried text has no length limit (a lead's note,
a remediation item's missing text). So a cell shows at most MAX_CELL_LINES lines, on one line
of text as the Markdown's cells are; a longer one ends in `… [n]`, and its whole text, line by
line as written, follows the table as note [n]. The PDF always builds.

The text is in the app's type (docs/design.md, Type; fonts/, with their licences): Libre Caslon
Display in capitals for the title and section headings, Manrope for the body and tables, IBM
Plex Mono for ids, IBM Plex Serif italic for quoted evidence, so the report reads as Vouch's.
DejaVu Sans draws a character one of them lacks; one DejaVu lacks too (an emoji in a quote) is
left out, so the PDF is still served. Carried text stays as written: unlike
Markdown, nothing in a PDF's text can turn into a heading or live HTML, so nothing is escaped.

fpdf2 is imported here only, keeping it out of vouch.report.
"""

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import MethodReturnValue
from fpdf.fonts import FontFace, TTFFont

from vouch import report as rows

FONTS = Path(__file__).parent / "fonts"
DISPLAY, SANS, MONO, QUOTE = "caslon", "manrope", "plexmono", "plexserif"  # the app's type
FALLBACK = "dejavu"  # draws what the app's fonts lack
_FILES = {
    (DISPLAY, ""): "LibreCaslonDisplay-Regular.ttf",
    (SANS, ""): "Manrope-Regular.ttf",
    (SANS, "B"): "Manrope-Bold.ttf",
    (MONO, ""): "IBMPlexMono-Regular.ttf",
    (QUOTE, "I"): "IBMPlexSerif-MediumItalic.ttf",
    (FALLBACK, ""): "DejaVuSans.ttf",
    (FALLBACK, "B"): "DejaVuSans-Bold.ttf",
}
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
CELL_SLACK = 0.5  # mm: a cell's lines are measured this much narrower, for its border
HEADINGS = FontFace(emphasis="BOLD", fill_color=None)
IDS = FontFace(family=MONO)  # a table cell holding an id

# The letterhead (#352): docs/design.md's light tokens, as frontend/src/styles/tokens.css holds them
# (tests/test_report_pdf.py keeps the two the same). A printed page is the light theme.
INK, MUTED, RULE, BRASS = "#1B1D21", "#62656B", "#D0CEC8", "#A8834A"
HEAD_TOP = 8  # mm: the letterhead's line, from the page's top edge
FOOT_FROM_BOTTOM = 10  # mm: the footer's line, from the page's bottom edge
TOP = 24  # mm: where a page's text starts, under the letterhead
BOTTOM = 18  # mm: where it stops, over the footer
LETTERHEAD = 7.5  # pt


class _Sheet(FPDF):
    """An A4 page in DejaVu Sans that draws only the characters the font has."""

    def __init__(self, letterhead: tuple[str, str, str] | None = None) -> None:
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self.letterhead = letterhead  # the company, the page words, the date; None: no letterhead
        top, bottom = (TOP, BOTTOM) if letterhead else (MARGIN, MARGIN)
        self.set_margins(MARGIN, top, MARGIN)
        self.set_auto_page_break(auto=True, margin=bottom)
        for (family, style), file in _FILES.items():
            self.add_font(family, style, FONTS / file)
        self.set_fallback_fonts([FALLBACK])
        # Drawn: what every app font has, or what DejaVu has in either weight (the fallback).
        maps = {key: font.cmap for key, font in self.fonts.items() if isinstance(font, TTFFont)}
        fallback = set(maps[FALLBACK]) & set(maps[f"{FALLBACK}B"])
        app = set.intersection(*(set(m) for k, m in maps.items() if not k.startswith(FALLBACK)))
        self._glyphs = fallback | app
        self._notes = 0  # the long cells' notes so far, numbered through the document

    def header(self) -> None:
        """The letterhead on each page (the owner's pick, option A, 2026-11-09): VOUCH as the app's
        brand mark (Caslon capitals, a brass diamond after) and the company, over a thin brass
        rule."""
        if not self.letterhead:
            return
        width = self.w - self.l_margin - self.r_margin
        self.set_xy(self.l_margin, HEAD_TOP)
        self.font(LETTERHEAD + 3.5, family=DISPLAY)
        self.set_char_spacing(1)
        self.set_text_color(INK)
        self.cell(text="VOUCH")
        self.set_char_spacing(0)
        x, y = self.get_x() + 1.8, HEAD_TOP + 1.6  # the diamond, as the app's .brand has it
        self.set_fill_color(BRASS)
        self.polygon([(x, y - 0.9), (x + 0.9, y), (x, y + 0.9), (x - 0.9, y)], style="F")
        self.set_xy(self.l_margin + width / 2, HEAD_TOP)
        self.font(LETTERHEAD)
        self.set_text_color(MUTED)
        self.cell(width / 2, 4, self.drawable(self.letterhead[0]), align="R")
        self.set_draw_color(BRASS)
        self.set_line_width(0.5)
        self.line(self.l_margin, HEAD_TOP + 6, self.w - self.r_margin, HEAD_TOP + 6)
        self._reset()
        self.set_xy(self.l_margin, self.t_margin)  # the page's text starts under it

    def footer(self) -> None:
        """Page x of y on the right, the report's date on the left, over a hairline rule."""
        if not self.letterhead:
            return
        _, page_of, date = self.letterhead
        y = self.h - FOOT_FROM_BOTTOM
        self.set_draw_color(RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, y - 1.5, self.w - self.r_margin, y - 1.5)
        pages = page_of.format(page=self.page_no(), pages=self.str_alias_nb_pages)
        width = self.w - self.l_margin - self.r_margin
        self.set_xy(self.l_margin, y)
        self.font(LETTERHEAD)
        self.set_text_color(INK)
        self.cell(width / 2, 4, self.drawable(date), align="L")
        self.set_text_color(MUTED)
        self.cell(width / 2, 4, self.drawable(pages), align="R")
        self._reset()

    def _reset(self) -> None:
        self.set_text_color(0)
        self.set_draw_color(0)
        self.set_fill_color(255)
        self.set_line_width(0.2)

    def drawable(self, text: object) -> str:
        """The text without the characters the font lacks; line breaks kept."""
        return "".join(c for c in str(text) if c == "\n" or ord(c) in self._glyphs)

    def font(self, size: float, bold: bool = False, family: str = SANS) -> None:
        """`family` in `size`: Manrope by default; its bold where it has one; quotes italic."""
        style = "I" if family == QUOTE else "B" if bold and (family, "B") in _FILES else ""
        self.set_font(family, style, size)

    def paragraph(
        self,
        text: object,
        size: float = BODY,
        bold: bool = False,
        indent: float = 0,
        family: str = SANS,
    ) -> None:
        self.font(size, bold, family)
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
        """A heading, never alone at a page's foot: a page break first when little room is left.
        A section's in Caslon capitals, as the app's h2; a smaller one in bold Manrope."""
        if self.y + KEEP_WITH_NEXT > self.page_break_trigger:
            self.add_page()
        self.ln(size * 0.4)
        if size >= SECTION:
            self.paragraph(str(text).upper(), size, family=DISPLAY)
        else:
            self.paragraph(text, size, bold=True)
        self.ln(1.5)

    def wrapped(self, text: object, width: float) -> list[str]:
        """The lines a table cell `width` mm wide draws `text` in, in the current font: the
        table's own padding (with it, fpdf2 leaves out its cell margin). The one measure of a
        cell's lines, for the line cap and for the trail's widths and their test."""
        lines = self.multi_cell(
            width - CELL_SLACK,
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
        lines = self.wrapped(flat, width)
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
        ids: Collection[int] = (),
    ) -> None:
        """A table: its header row on each page it runs onto, each cell's text wrapped, a long
        cell's whole text after it as a note. The columns numbered in `ids` hold ids, in Plex
        Mono (their headers stay in Manrope)."""
        notes: list[tuple[int, str]] = []
        cells: list[list[str]] = []
        for r in body:
            cells.append([])
            for i, (v, w) in enumerate(zip(r, widths, strict=True)):
                self.font(size, family=MONO if i in ids else SANS)
                cells[-1].append(self.cell_text(v, w, notes))
        self.font(size)
        with self.table(
            col_widths=tuple(widths),
            headings_style=HEADINGS,
            repeat_headings=1,
            line_height=size * 0.45,
            padding=CELL_PADDING,
            text_align="LEFT",
        ) as table:
            for n, values in enumerate([list(columns), *cells]):
                row = table.row()
                for i, value in enumerate(values):
                    row.cell(value, style=IDS if n and i in ids else None)
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
    head = report["head"]
    sheet = _Sheet((head["company"], sentences["page_of"], f"{labels['date']} {head['date']}"))
    sheet.set_title(sheet.drawable(words["title"]))
    sheet.set_lang(report["head"]["words_language"])
    sheet.set_creator("Vouch")
    sheet.add_page()

    sheet.paragraph(str(words["title"]).upper(), TITLE, family=DISPLAY)
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
        sheet.grid(
            rows.remediation_columns(words),
            rows.remediation_rows(group),
            (24, 94, 38, 24),
            ids=[0],
        )
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
        sheet.grid(head, body, widths, TRAIL, TRAIL_IDS)
    else:
        sheet.heading(labels["trail"], SECTION)
        sheet.paragraph(sentences["no_trail"])

    sheet.ln(4)
    sheet.line(sheet.l_margin, sheet.y, sheet.w - sheet.r_margin, sheet.y)
    sheet.ln(2)
    sheet.paragraph(report["disclaimer"])
    return bytes(sheet.output())


TRAIL_IDS = (2, 3, 8, 9, 10)  # the trail's control, requirement, evidence, model, prompt version
_PIECE = re.compile(r"[^\s/-]*[/-]?")  # a piece of an id: up to and with a "-" or "/"
# A zero-width space after an id's "-" or "/": where fpdf2 may break a line inside it. Only when
# whole ids don't fit (it stays in the PDF's text where no line breaks; the Markdown never has it).
BREAK = "\u200b"


def trail_table(
    sheet: _Sheet, columns: Sequence[str], body: Sequence[Sequence[object]]
) -> tuple[list[str], list[list[str]], tuple[float, ...]]:
    """The audit trail's header, rows and column widths across the page (#353), so no line
    breaks inside a word. Each column is at least as wide as its widest word or id (a header's
    in bold); the rest of the page is shared out by how much more each column's longest cell
    wants. When the words can't all fit, the widest columns give way first, down to their
    widest piece: an id may then break after a "-" or "/" (a BREAK there). Only a column whose
    piece is wider than any column can be breaks inside a word, and only there."""
    head = [str(c) for c in columns]
    rows = [[str(v) for v in r] for r in body]
    words = _least(sheet, head, rows, str.split)
    if sum(words) <= sheet.epw:
        return head, rows, _shared(sheet, head, rows, words)
    head = [_breakable(c) for c in head]
    rows = [[_breakable(v) for v in r] for r in rows]
    pieces = _least(sheet, head, rows, _pieces)
    if sum(pieces) <= sheet.epw:  # whole words where they fit, pieces in the widest columns
        least = _capped(words, pieces, sheet.epw)
    else:  # a piece too wide for the page: its column alone breaks inside a word
        least = _capped(pieces, [0.0] * len(pieces), sheet.epw)
    return head, rows, _shared(sheet, head, rows, least)


def _capped(widths: list[float], floors: list[float], total: float) -> list[float]:
    """`widths` with the widest cut to one cap, never under their `floors`, so they sum to
    `total`: the narrow columns keep their width and the widest give way."""
    low, high = 0.0, max(widths)
    for _ in range(60):  # halving: well under a hundredth of a millimetre
        cap = (low + high) / 2
        if sum(max(f, min(w, cap)) for w, f in zip(widths, floors, strict=True)) > total:
            high = cap
        else:
            low = cap
    return [max(f, min(w, low)) for w, f in zip(widths, floors, strict=True)]


def _breakable(text: str) -> str:
    """`text` with a BREAK after each "-" or "/" that something follows."""
    return re.sub(r"(?<=[/-])(?=\S)", BREAK, text)


def _pieces(text: str) -> list[str]:
    """The pieces of `text` no line breaks inside once BREAKs are in: words cut after "-", "/"."""
    return _PIECE.findall(text.replace(BREAK, ""))


def _width(sheet: _Sheet, text: str, family: str, bold: bool) -> float:
    """The cell width `text` fits on one line of, in the trail's size: the measure
    `_Sheet.wrapped` makes, plus a hair for rounding."""
    sheet.font(TRAIL, bold, family)
    return sheet.get_string_width(sheet.drawable(text)) + 2 * CELL_PADDING + CELL_SLACK + 0.1


def _columns(head: list[str], rows: list[list[str]]) -> list[list[tuple[str, str, bool]]]:
    """The table by column: each column's header (bold Manrope) and then its cells, as (text,
    family, bold), an id column's in Plex Mono."""
    return [
        [(c, SANS, True)] + [(r[i], MONO if i in TRAIL_IDS else SANS, False) for r in rows]
        for i, c in enumerate(head)
    ]


def _least(
    sheet: _Sheet, head: list[str], rows: list[list[str]], parts: Callable[[str], list[str]]
) -> list[float]:
    """Each column's least width: its widest part."""
    return [
        max(
            [_width(sheet, p, f, bold) for text, f, bold in col for p in parts(text) if p],
            default=0,
        )
        for col in _columns(head, rows)
    ]


def _shared(
    sheet: _Sheet, head: list[str], rows: list[list[str]], least: list[float]
) -> tuple[float, ...]:
    """The `least` widths with the rest of the page shared by what each column's longest cell
    wants on one line, and anything left after that evenly."""
    spare = max(sheet.epw - sum(least), 0)
    whole = [
        max(_width(sheet, " ".join(t.replace(BREAK, "").split()), f, b) for t, f, b in col)
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
    sheet.grid(rows.control_columns(words), rows.control_rows(controls, words), widths, ids=[0])
    for c in rows.quoted(controls):
        sheet.heading(f"{words['labels']['quotes']} {c['id']}", SUBSECTION)
        for q in c["quotes"]:
            sheet.paragraph(q["quote"], indent=5, family=QUOTE)  # line by line as written
            sheet.paragraph(f"— {rows.quote_source(q, words)}", TABLE, indent=5, family=MONO)
            sheet.ln(2)
