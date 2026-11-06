"""A printed PDF's words and rules with their boxes (#296), for checks print emulation can't make:
emulation lays the page out without page breaks, so only a real PDF shows how a page break
moves things. Boxes are in points from each page's top left."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTContainer, LTCurve, LTPage, LTTextLine

# Chromium's embedded fonts carry no FontBBox; pdfminer warns on each, and the boxes come from the glyphs.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


@dataclass(frozen=True)
class Box:
    page: int
    text: str  # a word's text; "" for a rule
    left: float
    top: float
    right: float
    bottom: float
    size: float = 0.0  # a word's glyph size, as pdfminer measures it; 0 for a rule

    def overlaps_vertically(self, other: "Box", slack: float = 0.5) -> bool:
        return (
            self.page == other.page
            and self.top < other.bottom - slack
            and other.top < self.bottom - slack
        )


@dataclass(frozen=True)
class Printed:
    words: list[Box]  # in reading order, split at spaces and at the gaps between table cells
    rules: list[Box]  # drawn horizontal lines and thin rectangles: a table's or a heading's rule
    page_width: float


def _box(page_n: int, page: LTPage, text: str, x0, y0, x1, y1, size: float = 0.0) -> Box:
    return Box(page_n, text, x0, page.height - y1, x1, page.height - y0, size)


def _walk(element) -> Iterator:
    """Every layout object under `element`, into text boxes and figures at any depth; a text
    line is yielded whole."""
    yield element
    if isinstance(element, LTContainer) and not isinstance(element, LTTextLine):
        for child in element:
            yield from _walk(child)


def _words(n: int, page: LTPage, line: LTTextLine) -> Iterator[Box]:
    chars: list[LTChar] = []
    for item in [*line, None]:
        if isinstance(item, LTChar) and item.get_text().strip():
            chars.append(item)
            continue
        if chars:
            text = "".join(c.get_text() for c in chars)
            x0, y0 = min(c.x0 for c in chars), min(c.y0 for c in chars)
            x1, y1 = max(c.x1 for c in chars), max(c.y1 for c in chars)
            yield _box(n, page, text, x0, y0, x1, y1, max(c.size for c in chars))
            chars = []


def read(pdf: bytes, rule_thinner_than: float = 2.0) -> Printed:
    """The PDF parsed once: its words, its rules and its page width."""
    words: list[Box] = []
    rules: list[Box] = []
    width = 0.0
    for n, page in enumerate(extract_pages(BytesIO(pdf))):
        width = width or page.width
        for element in _walk(page):
            if isinstance(element, LTTextLine):
                words.extend(_words(n, page, element))
            elif (
                isinstance(element, LTCurve) and element.height < rule_thinner_than < element.width
            ):
                rules.append(_box(n, page, "", element.x0, element.y0, element.x1, element.y1))
    return Printed(words, rules, width)
