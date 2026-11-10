"""The Ask Vouch button covers nothing (#462), measured.

`under_the_button(page)` lists every visible field, button and line of text whose box meets the
floating Ask Vouch button's box, with the page as it stands, so a page's browser test can assert
it's empty. A line of text is measured by its own line boxes, not its element's box, so a paragraph
that only reaches the button's height with a short last line is not counted."""

from playwright.sync_api import Page

# Each line box of each visible text node, and each visible input, button, select and textarea,
# outside the button itself, that meets the button's box. 1px of slack, for sub-pixel rounding.
UNDER = """() => {
  const fab = document.querySelector('.fab')
  if (!fab) return ['no Ask Vouch button']
  const f = fab.getBoundingClientRect()
  const meets = r => r.width > 0 && r.height > 0 &&
    r.left < f.right - 1 && r.right > f.left + 1 && r.top < f.bottom - 1 && r.bottom > f.top + 1
  const shown = el => {
    if (fab.contains(el) || el.closest('[inert], [aria-hidden=true]')) return false
    const s = getComputedStyle(el)
    return s.visibility !== 'hidden' && s.display !== 'none'
  }
  const hits = []
  const fields = 'input:not([type=hidden]), button, select, textarea'
  for (const el of document.querySelectorAll(fields)) {
    if (shown(el) && meets(el.getBoundingClientRect())) {
      const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('name') || '').trim()
      hits.push(`${el.tagName.toLowerCase()} "${name.slice(0, 40)}"`)
    }
  }
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

# How far the page's content ends above the button's top once scrolled to the bottom: the lowest
# visible box inside main; negative when it ends under the button.
CLEARANCE = """() => {
  window.scrollTo(0, document.documentElement.scrollHeight)
  const fab = document.querySelector('.fab').getBoundingClientRect()
  const ends = [...document.querySelectorAll('main *')].map(el => el.getBoundingClientRect())
    .filter(r => r.width > 1 && r.height > 1).map(r => r.bottom)
  return fab.top - Math.max(...ends)
}"""


# Each field and button in main brought into view as the browser does when it is tabbed to or
# scrolled to (scrollIntoView, which keeps clear of the page's scroll padding), then measured
# against the button: the ones that still meet it.
BROUGHT_INTO_VIEW = """() => {
  const f = document.querySelector('.fab').getBoundingClientRect()
  const hits = []
  for (const el of document.querySelectorAll('main :is(input:not([type=hidden]), button, select, textarea, a[href])')) {
    if (el.closest('[inert], [aria-hidden=true]') || getComputedStyle(el).visibility === 'hidden') continue
    if (!el.getClientRects().length) continue
    el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    const r = el.getBoundingClientRect()
    if (r.left < f.right - 1 && r.right > f.left + 1 && r.top < f.bottom - 1 && r.bottom > f.top + 1) {
      const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('name') || '').trim()
      hits.push(`${el.tagName.toLowerCase()} "${name.slice(0, 40)}"`)
    }
  }
  return hits
}"""


def under_the_button(page: Page) -> list[str]:
    """Each field, button or line of text under the Ask Vouch button, as `tag "name"`."""
    return page.evaluate(UNDER)


def scrolled_to_the_bottom(page: Page) -> float:
    """Scrolls to the page's bottom and returns how many px its last content ends above the
    button (negative: under it)."""
    return page.evaluate(CLEARANCE)


def under_the_button_when_brought_into_view(page: Page) -> list[str]:
    """Each field, button or link in main that, brought into view, still sits under the button."""
    return page.evaluate(BROUGHT_INTO_VIEW)
