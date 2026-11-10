"""Pinning the chat leaves the page a usable width (#465).

Pinning is offered from a 1402px page (chatWidth.ts, PIN_FROM): the narrowest chat, 360px, beside
the 1042px the working paper needs (its 250px index, two 16px gutters and the judged requirements
table's 760px). Pinned there, the top bar keeps to one row, nothing in the paper scrolls sideways
and its heading breaks only between words. The chat header's page tag shows the page's name in
full in every state, never cut.

On the chat's server: the paper's server spends its whole daily assessment limit on its own
checks. The layout on the demo control (assessed, so the paper holds the wide judged table); the
page tag on 5.2.4, the ticket's example, which needs no assessment."""

import re
from itertools import pairwise

import pytest
from playwright.sync_api import Page, expect
from test_chat import CHAT, pin
from test_working_paper import ASSESSED, assessed

PIN_FROM = 1402  # chatWidth.ts
PIN = "Pin beside the page"

# Words of the element's text that the browser split over two lines: none when it breaks only at
# spaces.
SPLIT_WORDS = """el => {
  const split = []
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const m of node.data.matchAll(/\\S+/g)) {
      const range = document.createRange()
      range.setStart(node, m.index)
      range.setEnd(node, m.index + m[0].length)
      const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.top)))
      if (lines.size > 1) split.push(m[0])
    }
  }
  return split
}"""
SIDEWAYS = "el => el.scrollWidth - el.clientWidth"


def page_width(page: Page, width: int, height: int = 900) -> None:
    """The viewport sized so the page itself (the root's client width, without a scrollbar) is
    `width`: the width chatWidth.ts checks against."""
    page.set_viewport_size({"width": width, "height": height})
    gutter = page.evaluate("window.innerWidth - document.documentElement.clientWidth")
    if gutter:
        page.set_viewport_size({"width": width + gutter, "height": height})
    assert page.evaluate("document.documentElement.clientWidth") == width


def opened_paper(page: Page, url: str, control_id: str) -> None:
    page.goto(f"{url}/controls/{control_id}")
    expect(page.get_by_role("main").get_by_role("heading", level=2)).to_contain_text(control_id)
    page.wait_for_load_state("networkidle")


def top_bar_height(page: Page) -> float:
    box = page.get_by_role("banner").bounding_box()
    assert box is not None
    return box["height"]


def test_pinned_at_the_narrowest_pinnable_width_the_paper_keeps_its_layout(page: Page, chat_url):
    assessed(page.request, chat_url, ASSESSED)
    page.set_viewport_size({"width": PIN_FROM, "height": 900})
    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        opened_paper(page, chat_url, ASSESSED)
    page_width(page, PIN_FROM)
    one_row = top_bar_height(page)  # the top bar with the whole width to itself

    pin(page)

    paper = page.locator(".wp .paper")
    assert top_bar_height(page) == one_row
    assert page.locator(".table-wrap").evaluate(SIDEWAYS) <= 0, "the requirements table scrolls"
    assert paper.evaluate(SIDEWAYS) <= 0
    assert page.evaluate("document.documentElement.scrollWidth") <= PIN_FROM
    assert paper.get_by_role("heading", level=2).evaluate(SPLIT_WORDS) == []
    # The page ends where the chat begins.
    chat = page.locator(CHAT).bounding_box()
    main = page.get_by_role("main").bounding_box()
    assert chat is not None and main is not None
    assert main["x"] + main["width"] <= chat["x"]


def test_below_the_narrowest_pinnable_width_the_chat_stays_floating(page: Page, chat_url):
    page.set_viewport_size({"width": PIN_FROM, "height": 900})
    opened_paper(page, chat_url, ASSESSED)
    page_width(page, PIN_FROM - 1)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    expect(page.locator(CHAT)).to_have_class("bubble enlarged")
    expect(page.get_by_role("button", name=PIN)).to_have_count(0)


def whole_tag(page: Page) -> None:
    tag = page.locator(CHAT).locator(".page-context")
    expect(tag).to_have_text("on 5.2.4 · Working paper")
    scroll, client = tag.evaluate("el => [el.scrollWidth, el.clientWidth]")
    assert scroll == client, "the page tag is cut"
    # Inside the chat, not clipped by its edge.
    inner = tag.bounding_box()
    outer = page.locator(CHAT).bounding_box()
    assert inner is not None and outer is not None
    assert outer["x"] <= inner["x"] and inner["x"] + inner["width"] <= outer["x"] + outer["width"]


@pytest.mark.parametrize("width", [1440, 1024])
def test_the_page_tag_shows_the_page_in_full_in_every_state(page: Page, chat_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    opened_paper(page, chat_url, "5.2.4")
    page.get_by_role("button", name="Ask Vouch").click()
    whole_tag(page)  # the bubble
    page.get_by_role("button", name="Make larger").click()
    whole_tag(page)
    pin_offered = page.get_by_role("button", name=PIN)
    if width < PIN_FROM:
        expect(pin_offered).to_have_count(0)
        return
    pin_offered.click()
    whole_tag(page)
    handle = page.get_by_role("separator", name="Chat width")
    handle.focus()
    page.keyboard.press("Home")  # the narrowest chat, 360px
    expect(handle).to_have_attribute("aria-valuenow", "360")
    whole_tag(page)
    # The header's controls stay on one row beside it.
    middles = (
        page.locator(CHAT)
        .locator(".bubble-head > .btn")
        .evaluate_all(
            "items => items.map(i => { const r = i.getBoundingClientRect(); return Math.round(r.top + r.height / 2) })"
        )
    )
    assert max(middles) - min(middles) <= 4, middles
    # Each whole, none squeezed under its words or over its neighbour.
    buttons = page.locator(CHAT).locator(".bubble-head > .btn")
    assert (
        buttons.evaluate_all(
            "bs => bs.filter(b => b.scrollWidth > b.clientWidth).map(b => b.textContent)"
        )
        == []
    )
    edges = buttons.evaluate_all(
        "bs => bs.map(b => { const r = b.getBoundingClientRect(); return [r.left, r.right] })"
    )
    assert all(right <= left for (_, right), (left, _) in pairwise(edges)), edges
