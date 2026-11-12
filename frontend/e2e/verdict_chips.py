"""Verdict chips that may wrap (#550): in the working paper's requirements table and the report's
controls table, a chip may wrap at the space between its words under ONE_LINE_FROM, its dot on
its first line (docs/design.md, Verdict chip)."""

from fonts import FONTS_SETTLED

WRAP_WIDTH = 701
"""The width at which the German verdict ("Teilweise nachgewiesen") widened both tables by about
16px (#550)."""
ONE_LINE_FROM = 800
"""From this width a verdict chip never wraps (#550's verify): with every chip on one line the
tables fit from 718px in the web fonts, and from 770px with DejaVu Sans (CI's fallback)."""
SWEEP = (720, ONE_LINE_FROM)
"""The widths between WRAP_WIDTH and 1001 the fit checks also measure: one each side of
ONE_LINE_FROM."""

LINES = """el => {
  const range = document.createRange()
  range.selectNodeContents(el)
  return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size
}"""
"""An element's number of text lines (as test_guided_chat's)."""

# Each verdict chip a selector finds, once the fonts have settled: its white-space, its text's
# line boxes, and its dot. A pseudo-element has no box to read, so a probe takes the dot's place
# for the measure: a span with the `::before`'s size, margins and alignment, the `::before`
# itself switched off meanwhile.
CHIPS = (
    "async (selector) => {"
    + FONTS_SETTLED
    + """  const off = document.createElement('style')
  off.textContent = '.dot-probing::before { display: none !important }'
  document.head.append(off)
  const chips = [...document.querySelectorAll(selector)].map((chip) => {
    const dot = getComputedStyle(chip, '::before')
    const probe = document.createElement('span')
    for (const p of ['display', 'width', 'height', 'margin-top', 'margin-right', 'margin-bottom',
      'margin-left', 'align-self', 'flex-shrink', 'vertical-align']) probe.style.setProperty(p, dot.getPropertyValue(p))
    chip.classList.add('dot-probing')
    chip.prepend(probe)
    const range = document.createRange()
    range.selectNodeContents(chip.lastChild)
    const rects = [...range.getClientRects()].map((r) => ({ top: r.top, bottom: r.bottom }))
    const box = probe.getBoundingClientRect()
    probe.remove()
    chip.classList.remove('dot-probing')
    return { text: chip.textContent, whiteSpace: getComputedStyle(chip).whiteSpace,
      lines: new Set(rects.map((r) => Math.round(r.top))).size, first: rects[0],
      dot: { top: box.top, bottom: box.bottom } }
  })
  off.remove()
  return chips
}"""
)


def assert_dot_on_first_line(chips: list[dict]) -> None:
    """Each chip's dot sits within its text's first line box, wrapped or not."""
    assert chips, "no verdict chip to measure"
    for chip in chips:
        first, dot = chip["first"], chip["dot"]
        assert dot["top"] >= first["top"] - 1 and dot["bottom"] <= first["bottom"] + 1, chip
