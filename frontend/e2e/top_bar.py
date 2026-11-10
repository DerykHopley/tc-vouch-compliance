"""The top bar's controls (#222; the owner's option A "Letterhead" since #495), for the browser
checks: the breadcrumb back to the Overview, the Overview and Company tabs, the company the bar
names, and the user menu's items (the developer page and company setup are there, not in tabs).
The profile dropdown is gone (#480): a check under another company makes it current
(current_company.py)."""

from playwright.sync_api import Locator, Page, expect


def back_to_overview(page: Page) -> None:
    """The working paper's breadcrumb, "Overview › 5.1.1": its Overview link."""
    page.get_by_role("navigation", name="Breadcrumb").get_by_role("link", name="Overview").click()


def overview_tab(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Main").get_by_role("link", name="Overview")


def company_tab(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Main").get_by_role("link", name="Company")


def bar_company(page: Page) -> Locator:
    """The current company's name in the top bar (#495)."""
    return page.get_by_role("banner").locator(".top-name")


def bar_tag(page: Page) -> Locator:
    """The current company's country and language tag beside its name, "DE · EN" (#495)."""
    return page.get_by_role("banner").locator(".top-tag")


def from_user_menu(page: Page, item: str) -> None:
    """Opens the user menu and chooses `item` ("Developer page", "Set up a company"); on a phone,
    from behind the Menu button first."""
    bar = page.get_by_role("banner")
    toggle = bar.get_by_role("button", name="Menu", exact=True)
    user = bar.get_by_role("button", name="Your menu")
    expect(toggle.or_(user).first).to_be_visible()  # the app is up: one of them shows, by width
    if toggle.is_visible() and toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    user.click()
    page.get_by_role("menuitem", name=item).click()
