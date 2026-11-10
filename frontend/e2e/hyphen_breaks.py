"""Words a browser broke across lines without a hyphen (#511, #488, #521, #530): for the checks
that a text in a narrow column hyphenates in its language rather than splitting a word anywhere."""

from fonts import FONTS_SETTLED
from playwright.sync_api import Page

# Each word in the elements the selector (the page function's argument) matches that breaks across
# lines without a hyphen, read once the fonts have loaded and layout has settled. A word's letters
# are grouped by the line each is on; on every line but its last, the word's rects there are as wide
# as those letters set unbroken, plus the hyphen the browser inserted. Chromium gives that hyphen a
# rect of its own and Firefox widens the line's rect by it (#488), so the width is what both agree
# on: a break with less than half a hyphen's extra width has none. `page.evaluate(UNHYPHENATED_BREAKS,
# selector)` gives `{words, breaks}`: how many words it measured, and the ones broken bare.
UNHYPHENATED_BREAKS = (
    "async (selector) => {"
    + FONTS_SETTLED
    + r"""  const words = []
  const breaks = []
  const rangeOf = (node, start, end) => {
    const range = document.createRange()
    range.setStart(node, start)
    range.setEnd(node, end)
    return range
  }
  for (const element of document.querySelectorAll(selector)) {
    // The element's own font, unbroken: what its letters measure with no hyphen at the line's end.
    const natural = (text) => {
      const probe = document.createElement('span')
      probe.style.cssText = 'position: absolute; visibility: hidden; white-space: pre'
      probe.textContent = text
      element.append(probe)
      const width = probe.getBoundingClientRect().width
      probe.remove()
      return width
    }
    const hyphen = natural('-')
    const walk = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    const nodes = []
    for (let node = walk.nextNode(); node; node = walk.nextNode()) nodes.push(node)
    for (const node of nodes) {
      for (const word of node.data.matchAll(/\p{L}+/gu)) {
        const lines = new Map() // a line's top -> the word's letters on it
        for (let i = 0; i < word[0].length; i++) {
          // Its own rect is its last: Chromium adds the hyphen's, on the line above, to a line's first letter.
          const own = [...rangeOf(node, word.index + i, word.index + i + 1).getClientRects()].at(-1)
          const top = Math.round(own.top)
          lines.set(top, (lines.get(top) ?? '') + word[0][i])
        }
        const rects = [...rangeOf(node, word.index, word.index + word[0].length).getClientRects()]
        words.push(word[0])
        const tops = [...lines.keys()].sort((a, b) => a - b)
        const bare = tops.slice(0, -1).some((top) => {
          const here = rects.filter((r) => Math.abs(r.top - top) < 2)
          const width = Math.max(...here.map((r) => r.right)) - Math.min(...here.map((r) => r.left))
          return width - natural(lines.get(top)) < hyphen / 2
        })
        if (bare) breaks.push(word[0])
      }
    }
  }
  return { words: words.length, breaks }
}"""
)


def assert_hyphenated_in_german(page: Page, columns: dict[str, str]) -> None:
    """Every column (a name and its elements' selector) has words to measure, none broken without
    a hyphen (UNHYPHENATED_BREAKS), and each element marked German (`lang="de"`) and hyphenating
    (`hyphens: auto`). The failure names each column that doesn't, with what it found."""
    marks = "els => els.map((e) => ({ lang: e.lang, hyphens: getComputedStyle(e).hyphens }))"
    wrong = {}
    for name, selector in columns.items():
        found = page.evaluate(UNHYPHENATED_BREAKS, selector)
        marked = page.locator(selector).evaluate_all(marks)
        unmarked = [m for m in marked if m != {"lang": "de", "hyphens": "auto"}]
        if not found["words"] or found["breaks"] or unmarked:
            wrong[name] = {"words": found["words"], "breaks": found["breaks"], "unmarked": unmarked}
    assert wrong == {}, f"not hyphenated in German: {wrong}"
