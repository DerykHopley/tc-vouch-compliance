"""The top bar (#222; the owner's option A "Letterhead" since #495): the current company named
beside the brand, underlined page tabs Overview · Report · Company, a user menu for the theme, the
developer's tools and the fresh start (and sign out, in clerk mode), a breadcrumb on the working
paper, and one Menu button on a phone; no profile dropdown since #480 (test_profiles.py checks it
is gone). Local mode, on the fake model: its one user is a developer."""

import re

import pytest
from playwright.sync_api import Page, expect
from top_bar import (
    back_to_overview,
    bar_company,
    bar_tag,
    company_tab,
    from_user_menu,
    overview_tab,
)
from touch_targets import small_targets

ASSESSED = "4.1.2"
NORDLICHT = "Nordlicht Komponenten GmbH"
FRESH_START = f"Start {NORDLICHT} afresh…"  # #285, last since #495


def opened(page: Page, url: str, width: int = 1280) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    with page.expect_response("**/api/overview"):
        page.goto(url)


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1280, 375])
def test_story_1_a_lead_sees_one_company_named_in_the_top_bar_and_no_switcher(
    page: Page, profiles_url, width, scheme
):
    """#448 story 1, end to end: one company named in the top bar, the tabs Overview · Report ·
    Company, and no switcher; on a phone the name shows without opening Menu, and the page never
    scrolls sideways."""
    page.emulate_media(color_scheme=scheme)
    opened(page, profiles_url, width)
    bar = page.get_by_role("banner")
    expect(bar_company(page)).to_have_count(1)
    expect(bar_company(page)).to_have_text(NORDLICHT)
    expect(bar_company(page)).to_be_visible()
    expect(bar_tag(page)).to_have_text("DE · EN")
    expect(bar.get_by_text(NORDLICHT)).to_have_count(1)  # named once, never as a control
    expect(bar.locator("button, a").filter(has_text=NORDLICHT)).to_have_count(0)
    expect(bar.get_by_role("menuitemradio")).to_have_count(0)
    expect(page.locator(".hero .pill")).to_have_count(0)  # the hero names it no more
    assert not scrolls_sideways(page)
    if width < 700:
        bar.get_by_role("button", name="Menu", exact=True).click()
    tabs = page.get_by_role("navigation", name="Main").get_by_role("link")
    expect(tabs).to_have_text(["Overview", "Report", "Company"])
    assert not scrolls_sideways(page)


def test_the_tabs_mark_the_current_page(page: Page, profiles_url):
    opened(page, profiles_url)
    tabs = page.get_by_role("navigation", name="Main").get_by_role("link")
    # Report after Overview (#56), Company after Report (#495); the developer's pages in the user menu
    expect(tabs).to_have_text(["Overview", "Report", "Company"])
    expect(overview_tab(page)).to_have_attribute("aria-current", "page")

    page.get_by_role("link", name="Report").click()
    expect(page).to_have_url(re.compile(r"/report$"))
    expect(page.get_by_role("link", name="Report")).to_have_attribute("aria-current", "page")
    expect(overview_tab(page)).not_to_have_attribute("aria-current", "page")

    with page.expect_response("**/api/documents"):
        company_tab(page).click()
    expect(page).to_have_url(re.compile(r"/company$"))
    expect(company_tab(page)).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("article", name="Documents")).to_be_visible()

    from_user_menu(page, "Developer page")
    expect(page).to_have_url(re.compile(r"/developer$"))
    expect(page.locator("nav[aria-label=Main] [aria-current=page]")).to_have_count(0)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_user_menu_opens_by_click_or_keyboard_closes_with_esc_and_flips_the_theme(
    page: Page, profiles_url, scheme
):
    page.emulate_media(color_scheme=scheme)
    opened(page, profiles_url)
    user = page.get_by_role("banner").get_by_role("button", name="Your menu")
    expect(user).to_have_text("LU")  # local mode's one user, "Local user"

    user.click()
    menu = page.get_by_role("menu", name="Your menu")
    theme = "Light theme" if scheme == "dark" else "Dark theme"
    expect(menu.get_by_role("menuitem")).to_have_text(
        [theme, "Developer page", "Set up a company", FRESH_START]
    )
    expect(menu.get_by_role("separator")).to_have_count(2)
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)

    user.focus()
    page.keyboard.press("Enter")
    items = page.get_by_role("menuitem")
    expect(items.nth(0)).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(items.nth(1)).to_be_focused()
    page.keyboard.press("ArrowUp")
    expect(items.nth(0)).to_be_focused()
    expect(items.nth(0)).to_have_text(theme)  # the theme first, since #495
    page.keyboard.press("Enter")  # the theme flips, and the menu closes back on its button
    flipped = "light" if scheme == "dark" else "dark"
    expect(page.locator("html")).to_have_attribute("data-theme", flipped)
    expect(menu).to_have_count(0)
    expect(user).to_be_focused()


def test_the_working_paper_breadcrumb_goes_back_to_the_overview(page: Page, paper_url):
    response = page.request.post(f"{paper_url}/api/assessments", data={"control_id": ASSESSED})
    assert response.ok, response.status
    page.set_viewport_size({"width": 1280, "height": 900})
    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        page.goto(f"{paper_url}/controls/{ASSESSED}")
    crumbs = page.get_by_role("navigation", name="Breadcrumb")
    expect(crumbs).to_have_text(re.compile(rf"Overview\s*›\s*{re.escape(ASSESSED)}"))
    expect(crumbs.locator("[aria-current=page]")).to_have_text(ASSESSED)

    back_to_overview(page)
    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.get_by_role("heading", level=1)).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_on_a_phone_one_menu_button_holds_the_tabs_and_user_menu(page: Page, profiles_url, scheme):
    page.emulate_media(color_scheme=scheme)
    opened(page, profiles_url, 400)
    bar = page.get_by_role("banner")
    toggle = bar.get_by_role("button", name="Menu", exact=True)
    expect(toggle).to_be_visible()
    report_tab = page.get_by_role("link", name="Report", exact=True)
    expect(overview_tab(page)).to_be_hidden()
    expect(report_tab).to_be_hidden()  # in the menu too (#56)
    expect(bar.get_by_role("button", name="Your menu")).to_be_hidden()
    assert not scrolls_sideways(page)

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(overview_tab(page)).to_be_visible()
    expect(report_tab).to_be_visible()
    expect(bar.get_by_role("button", name="Your menu")).to_be_visible()
    assert not scrolls_sideways(page)
    assert small_targets(page) == []

    bar.get_by_role("button", name="Your menu").click()
    # the theme, the developer's two (#495) and the fresh start (#285)
    expect(page.get_by_role("menuitem")).to_have_count(4)
    assert small_targets(page) == []

    page.get_by_role("menuitem", name="Developer page").click()  # as a tab does, closes the panel
    expect(page).to_have_url(re.compile(r"/developer$"))
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(
        toggle
    ).to_be_focused()  # the item it came from is hidden: focus on a control still shown
    expect(bar_company(page)).to_be_visible()

    toggle.click()
    report_tab.click()  # a tab closes the panel
    expect(toggle).to_have_attribute("aria-expanded", "false")


def test_the_user_menu_moves_with_the_arrows_and_tab_closes_it(page: Page, profiles_url):
    # Keyboard and focus get a browser test (agent-rules.md): jsdom only models them. Once the
    # profile menu's check; the user menu is the same Menu since the dropdown went (#480).
    opened(page, profiles_url)
    button = page.get_by_role("banner").get_by_role("button", name="Your menu")
    button.focus()
    page.keyboard.press("Enter")
    choices = page.get_by_role("banner").get_by_role("menuitem")
    # the theme, Developer page, Set up a company, the fresh start; the separators are skipped (#495)
    expect(choices).to_have_count(4)
    expect(choices.nth(0)).to_be_focused()
    for i in (1, 2, 3):
        page.keyboard.press("ArrowDown")
        expect(choices.nth(i)).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(choices.nth(0)).to_be_focused()  # round again
    page.keyboard.press("ArrowUp")
    expect(choices.nth(3)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("banner").get_by_role("menu")).to_have_count(0)


def test_esc_closes_a_top_bar_menu_but_leaves_an_open_chat_open(page: Page, profiles_url):
    opened(page, profiles_url)
    page.get_by_role("button", name="Ask Vouch").click()
    chat = page.locator("aside[aria-label='Chat with Vouch']")
    expect(chat).to_be_visible()
    page.get_by_role("banner").get_by_role("button", name="Your menu").click()
    page.keyboard.press("Escape")
    expect(page.get_by_role("banner").get_by_role("menu")).to_have_count(0)
    expect(chat).to_be_visible()


def test_on_a_phone_a_profile_kept_by_an_earlier_version_changes_nothing(page: Page, profiles_url):
    """#201's remembered profile is gone (#480): a value left under its key opens the current
    company, with no note, the Menu closed."""
    page.add_init_script("localStorage.setItem('vouch.profile', 'gone')")
    with page.expect_response(lambda r: r.url.endswith("/api/overview")) as read:
        opened(page, profiles_url, 400)
    assert read.value.request.header_value("X-Vouch-Profile") == "nordlicht"
    expect(page.get_by_role("banner").get_by_role("status")).to_have_count(0)
    expect(
        page.get_by_role("banner").get_by_role("button", name="Menu", exact=True)
    ).to_have_attribute("aria-expanded", "false")
    assert not scrolls_sideways(page)
