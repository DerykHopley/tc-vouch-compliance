"""A printed PDF's words and rules with their boxes (#296), for checks print emulation can't make:
emulation lays the page out without page breaks, so only a real PDF shows how a page break
moves things. Boxes are in points from each page's top left."""

import logging
from dataclasses import dataclass
from io import BytesIO

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTCurve, LTPage, LTTextContainer, LTTextLine

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

    def overlaps_vertically(self, other: "Box", slack: float = 0.5) -> bool:
        return (
            self.page == other.page
            and self.top < other.bottom - slack
            and other.top < self.bottom - slack
        )


def _lines(element):
    if isinstance(element, LTTextLine):
        yield element
    elif isinstance(element, LTTextContainer):
        for child in element:
            yield from _lines(child)


def _box(page_n: int, page: LTPage, text: str, x0, y0, x1, y1) -> Box:
    return Box(page_n, text, x0, page.height - y1, x1, page.height - y0)


def words(pdf: bytes) -> list[Box]:
    """Every word in reading order, split at spaces and at the gaps between table cells."""
    found: list[Box] = []
    for n, page in enumerate(extract_pages(BytesIO(pdf))):
        for element in page:
            for line in _lines(element):
                chars: list[LTChar] = []
                for item in [*line, None]:
                    if isinstance(item, LTChar) and item.get_text().strip():
                        chars.append(item)
                        continue
                    if chars:
                        text = "".join(c.get_text() for c in chars)
                        x0, y0 = min(c.x0 for c in chars), min(c.y0 for c in chars)
                        x1, y1 = max(c.x1 for c in chars), max(c.y1 for c in chars)
                        found.append(_box(n, page, text, x0, y0, x1, y1))
                        chars = []
    return found


def rules(pdf: bytes, thinner_than: float = 2.0) -> list[Box]:
    """Every drawn horizontal line or thin rectangle: a table's or a heading's rule."""
    found: list[Box] = []
    for n, page in enumerate(extract_pages(BytesIO(pdf))):
        for element in page:
            if isinstance(element, LTCurve) and element.height < thinner_than < element.width:
                found.append(_box(n, page, "", element.x0, element.y0, element.x1, element.y1))
    return found


def page_width(pdf: bytes) -> float:
    return next(extract_pages(BytesIO(pdf))).width
