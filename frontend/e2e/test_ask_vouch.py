"""Keyboard focus with the Ask Vouch bubble open (#66).

Below 700px the bubble is a full-screen sheet: the page behind can't be seen, so Tab must never
reach it, whatever had focus first. jsdom ignores `inert` and focus starting points, so these
paths are checked in a real browser.

They run on DOSSIER_URL: #47 moved them off the main server while the request rate was capped at
60 a minute. Fix #138 lifted that cap for the suite, so they could move back.
"""

import pytest
from playwright.sync_api import Page, expect

SHEET = "aside[aria-label='Chat with Vouch']"
FOCUS_OUTSIDE_SHEET = f"""() => {{
  const el = document.activeElement
  if (!el || el === document.body) return null
  return el.closest("{SHEET}") ? null : (el.textContent || el.tagName).trim()
}}"""
TABS = 7  # the top bar's stops, the profile menu among them (the fixtures hold two profiles)
REACH = (
    40  # Shift+Tabs from inside the bubble back to the top bar at 1400px (about 20 on the Dossier)
)


def open_sheet(page: Page, dossier_url: str, width: int, double: bool = False) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(dossier_url)
    ask = page.get_by_role("button", name="Ask Vouch")
    if double:
        ask.dblclick()
    else:
        ask.click()
    expect(page.locator(SHEET)).to_be_visible()


def tab_round(page: Page) -> list[str]:
    """Tab, then Shift+Tab, several times each: what each press focused outside the sheet."""
    escaped = []
    for key in ["Tab"] * TABS + ["Shift+Tab"] * TABS:
        page.keyboard.press(key)
        outside = page.evaluate(FOCUS_OUTSIDE_SHEET)
        if outside:
            escaped.append(f"{key} -> {outside}")
    return escaped


@pytest.mark.parametrize("where", ["blank area", "title"])
def test_at_400px_tab_stays_in_the_sheet_after_a_click_inside_it(page: Page, dossier_url, where):
    open_sheet(page, dossier_url, 400)
    sheet = page.locator(SHEET)
    if where == "title":
        sheet.get_by_text("Vouch", exact=True).click()
    else:
        sheet.click(position={"x": 200, "y": 400})  # the empty chat log
    assert tab_round(page) == []


def test_at_400px_tab_stays_in_the_sheet_after_a_double_click_on_ask_vouch(page: Page, dossier_url):
    open_sheet(page, dossier_url, 400, double=True)
    assert tab_round(page) == []


def test_at_400px_tab_reaches_close_inside_the_sheet(page: Page, dossier_url):
    open_sheet(page, dossier_url, 400)
    page.locator(SHEET).click(position={"x": 200, "y": 400})
    page.keyboard.press("Tab")  # the header's first control, "New conversation" (#251), then Close
    expect(page.get_by_role("button", name="New conversation")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Close the chat")).to_be_focused()


def test_at_1400px_the_page_stays_reachable_beside_the_bubble(page: Page, dossier_url):
    # Backwards from inside the bubble, so it holds in every browser: the bubble is the page's last
    # stop, and Tab past the end wraps round in Chromium but not in headless Firefox (#225). The
    # keyboard, not a script's focus(), must reach the top bar's controls beside the open bubble.
    open_sheet(page, dossier_url, 1400)
    page.locator(SHEET).click(position={"x": 100, "y": 300})
    reached = set()
    for _ in range(REACH):
        page.keyboard.press("Shift+Tab")
        reached.add(page.evaluate(FOCUS_OUTSIDE_SHEET))
        if "Overview" in reached:
            break
    assert "Overview" in reached, reached
    assert "LU" in reached, reached  # the user menu: local mode's initials (#222)


def test_at_1440px_the_chats_header_keeps_to_one_line(page: Page, paper_url):
    # On a working paper, the longest page tag, with "New conversation" beside it (#251): in the
    # bubble and pinned beside the page, no control wraps onto a second line, taking the chat's room.
    # The page tag may sit under the name, inside the title (#465). Pinning needs 1402px.
    page.set_viewport_size({"width": 1440, "height": 800})
    page.goto(f"{paper_url}/controls/4.1.2")
    page.get_by_role("button", name="Ask Vouch").click()
    sheet = page.locator(SHEET)
    expect(sheet).to_be_visible()

    def middles() -> list[int]:
        return sheet.locator(".bubble-head > *").evaluate_all(
            "items => items.map(i => { const r = i.getBoundingClientRect(); return Math.round(r.top + r.height / 2) })"
        )

    for then in (None, "Make larger", "Pin beside the page"):
        if then:
            sheet.get_by_role("button", name=then).click()
        if then != "Make larger":  # the enlarged bubble is 640px: room enough
            row = middles()
            assert max(row) - min(row) <= 4, (
                then,
                row,
            )  # one row, centred; wrapped sits 44px lower


def test_the_close_glyph_keeps_a_44px_box_in_every_chat_state(page: Page, dossier_url):
    # Close is a × glyph (#72): the button around it stays at least 44 by 44px in the 400px sheet
    # and in the 1440px bubble, enlarged and pinned (pinning needs 1402px, #465).
    def box(state: str) -> tuple[str, float, float]:
        # Measured once the sheet's rise has finished: a resize to 400px starts it again, and a
        # box read mid-move is a fraction short (43.99…).
        page.locator(SHEET).evaluate("el => Promise.all(el.getAnimations().map((a) => a.finished))")
        b = page.get_by_role("button", name="Close the chat").bounding_box()
        assert b is not None, state
        return (state, b["width"], b["height"])

    open_sheet(page, dossier_url, 1440)
    sheet = page.locator(SHEET)
    boxes = [box("bubble")]
    sheet.get_by_role("button", name="Make larger").click()
    boxes.append(box("enlarged"))
    sheet.get_by_role("button", name="Pin beside the page").click()
    expect(sheet.get_by_role("button", name="Pin beside the page")).to_have_attribute(
        "aria-pressed", "true"
    )
    boxes.append(box("pinned"))
    page.set_viewport_size({"width": 400, "height": 800})  # every state is the full-screen sheet
    expect(sheet.get_by_role("button", name="Pin beside the page")).to_be_hidden()
    boxes.append(box("sheet"))
    small = [b for b in boxes if b[1] < 44 or b[2] < 44]
    assert small == [], boxes


def test_closing_the_sheet_gives_the_page_back(page: Page, dossier_url):
    open_sheet(page, dossier_url, 400)
    page.get_by_role("button", name="Close the chat").click()
    expect(page.get_by_role("button", name="Ask Vouch")).to_be_focused()
    # The page takes focus again: on a phone the top bar's Menu button (#222).
    page.get_by_role("banner").get_by_role("button", name="Menu", exact=True).focus()
    expect(
        page.get_by_role("banner").get_by_role("button", name="Menu", exact=True)
    ).to_be_focused()
    page.get_by_role("button", name="Ask Vouch").click()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Ask Vouch")).to_be_focused()


@pytest.mark.parametrize("width", [400, 1400])
@pytest.mark.parametrize("how", ["double click", "double tap"])
def test_a_double_click_on_close_closes_the_chat_once_and_presses_nothing_behind(
    browser, dossier_url, width, how
):
    # #333: the first click closes the chat, and the second would land on what was under Close:
    # the top bar's Menu at 400px (focused and opened), the page at 1400px (focus lost). It
    # presses nothing, as #527's guard does for the in-app ask, and focus stays on Ask Vouch.
    context = browser.new_context(
        has_touch=how == "double tap", viewport={"width": width, "height": 800}
    )
    page = context.new_page()
    try:
        page.goto(dossier_url)
        page.get_by_role("button", name="Ask Vouch").click()
        sheet = page.locator(SHEET)
        sheet.evaluate("el => Promise.all(el.getAnimations().map((a) => a.finished))")
        close = page.get_by_role("button", name="Close the chat")
        if how == "double click":
            close.dblclick()
        else:
            b = close.bounding_box()
            assert b is not None
            x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
            page.touchscreen.tap(x, y)
            page.touchscreen.tap(x, y)
        expect(sheet).to_have_count(0)
        menu = page.get_by_role("banner").get_by_role(
            "button",
            name="Menu",
            exact=True,
            include_hidden=True,  # hidden at 1400px
        )
        expect(menu).to_have_attribute("aria-expanded", "false")
        expect(menu).not_to_be_focused()
        expect(page.get_by_role("button", name="Ask Vouch")).to_be_focused()
    finally:
        context.close()
