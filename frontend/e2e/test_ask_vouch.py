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
TABS = 6
REACH = 40  # Shift+Tabs from inside the bubble back to the top bar at 1400px (19 on the Dossier)


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
        if "Dossier" in reached:
            break
    assert "Dossier" in reached, reached
    assert any(name and name.endswith("theme") for name in reached), reached


def test_closing_the_sheet_gives_the_page_back(page: Page, dossier_url):
    open_sheet(page, dossier_url, 400)
    page.get_by_role("button", name="Close the chat").click()
    expect(page.get_by_role("button", name="Ask Vouch")).to_be_focused()
    page.get_by_role("link", name="Dossier").focus()
    expect(page.get_by_role("link", name="Dossier")).to_be_focused()
    page.get_by_role("button", name="Ask Vouch").click()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Ask Vouch")).to_be_focused()
