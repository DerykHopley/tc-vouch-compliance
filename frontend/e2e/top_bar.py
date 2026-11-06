"""The top bar's controls (#222, the owner's option D), for the browser checks: the profile
dropdown, the breadcrumb back to the Overview, and the user menu."""

from playwright.sync_api import Locator, Page


def profile_button(page: Page) -> Locator:
    """The profile dropdown's button: its name says "Profile: " and the profile in full."""
    return page.get_by_role("banner").get_by_role("button", name="Profile: ")


def open_profile_menu(page: Page) -> Locator:
    """Opens the profile dropdown: its choices, each a profile in full."""
    profile_button(page).click()
    return page.get_by_role("banner").get_by_role("menuitemradio")


def switch_profile(page: Page, label: str) -> None:
    """Chooses the profile named in full (company · country · language) in the dropdown."""
    open_profile_menu(page)
    page.get_by_role("banner").get_by_role("menuitemradio", name=label, exact=True).click()


def back_to_overview(page: Page) -> None:
    """The working paper's breadcrumb, "Overview › 5.1.1": its Overview link."""
    page.get_by_role("navigation", name="Breadcrumb").get_by_role("link", name="Overview").click()


def overview_tab(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Main").get_by_role("link", name="Overview")
