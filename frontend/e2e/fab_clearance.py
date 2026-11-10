"""The Ask Vouch button covers nothing (#462), measured.

Three measures, each with the page as it stands, so a page's browser test can assert on them:
- `under_the_button(page)`: every visible field, button, link and line of text whose box meets the
  floating Ask Vouch button's box. A line of text is measured by its own line boxes, not its
  element's box, so a paragraph whose short last line stops left of the button is not counted;
- `under_the_button_when_brought_into_view(page)`: the fields, buttons and links in main that still
  meet it once each is brought into view as the browser does when it is tabbed or scrolled to;
- `clearance_at_the_bottom(page)`: scrolled to the bottom, how far the page's content ends above
  the button."""

from playwright.sync_api import Page

# Shared by the three scripts: the button's box, whether a box meets it (1px of slack, for
# sub-pixel rounding), the controls measured, whether one is shown, and its name in a report.
COMMON = """
const fab = document.querySelector('.fab')
if (!fab) throw new Error('no Ask Vouch button on the page')
const button = () => fab.getBoundingClientRect()
const meets = r => {
  const f = button()
  return r.width > 0 && r.height > 0 &&
    r.left < f.right - 1 && r.right > f.left + 1 && r.top < f.bottom - 1 && r.bottom > f.top + 1
}
const CONTROLS = 'input:not([type=hidden]), button, select, textarea, a[href]'
const shown = el => !fab.contains(el) && !el.closest('[inert], [aria-hidden=true]') &&
  el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
const named = el => {
  const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('name') || '').trim()
  return `${el.tagName.toLowerCase()} "${name.slice(0, 40)}"`
}
"""

UNDER = (
    "() => {"
    + COMMON
    + """
  const hits = [...document.querySelectorAll(CONTROLS)]
    .filter(el => shown(el) && meets(el.getBoundingClientRect())).map(named)
  const walker = document.createTreeWalker(document.querySelector('#root') || document.body, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim() || !node.parentElement || !shown(node.parentElement)) continue
    const range = document.createRange()
    range.selectNodeContents(node)
    if ([...range.getClientRects()].some(meets)) {
      hits.push(`text "${node.textContent.trim().slice(0, 40)}" in ${node.parentElement.tagName.toLowerCase()}`)
    }
  }
  return hits
}"""
)

# scrollIntoView keeps clear of the page's scroll padding, as tabbing to a field does.
BROUGHT_INTO_VIEW = (
    "() => {"
    + COMMON
    + """
  const hits = []
  for (const el of document.querySelectorAll(`main :is(${CONTROLS})`)) {
    if (!shown(el)) continue
    el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    if (meets(el.getBoundingClientRect())) hits.push(named(el))
  }
  return hits
}"""
)

# The lowest visible box inside main; negative when it ends under the button.
CLEARANCE = (
    "() => {"
    + COMMON
    + """
  window.scrollTo(0, document.documentElement.scrollHeight)
  const ends = [...document.querySelectorAll('main *')].map(el => el.getBoundingClientRect())
    .filter(r => r.width > 1 && r.height > 1).map(r => r.bottom)
  if (!ends.length) throw new Error('nothing in main to measure')
  return button().top - Math.max(...ends)
}"""
)


def under_the_button(page: Page) -> list[str]:
    """Each field, button, link or line of text under the Ask Vouch button, as `tag "name"`."""
    return page.evaluate(UNDER)


def under_the_button_when_brought_into_view(page: Page) -> list[str]:
    """Each field, button or link in main that, brought into view, still sits under the button."""
    return page.evaluate(BROUGHT_INTO_VIEW)


def clearance_at_the_bottom(page: Page) -> float:
    """Scrolls to the page's bottom and returns how many px its content ends above the button
    (negative: under it)."""
    return page.evaluate(CLEARANCE)
