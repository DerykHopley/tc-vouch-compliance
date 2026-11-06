"""The touch-target floor (docs/design.md, Accessibility floor: at least 44 by 44px), measured.

`small_targets(page)` lists every visible control under 44px on the page as it stands, so a page's
browser test can assert it's empty. From #45, which failed verify twice on a rule nothing measured."""

from playwright.sync_api import Page

MIN = 44

# Visible controls a finger aims at, under the floor in either direction. No exemption for a link in
# running text (WCAG 2.5.8 has one; design.md doesn't): #45's chat actions were such links.
SMALL = """min => [...document.querySelectorAll(
  'a[href], button, [role=button], [role=tab], [role=switch], input:not([type=hidden]), select, summary'
)].filter(el => {
  const r = el.getBoundingClientRect()
  if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') return false
  if (el.closest('[inert], [aria-hidden=true]')) return false
  return r.width < min || r.height < min
}).map(el => {
  const r = el.getBoundingClientRect()
  const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('name') || '').trim()
  return `${el.tagName.toLowerCase()} "${name.slice(0, 40)}" ${Math.round(r.width)}x${Math.round(r.height)}`
})"""


def small_targets(page: Page) -> list[str]:
    """Each visible control under the floor, as `tag "name" WxH`."""
    return page.evaluate(SMALL, MIN)
