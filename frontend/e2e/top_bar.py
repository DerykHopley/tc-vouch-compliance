"""The top bar's controls (#222, the owner's option D), for the browser checks: the breadcrumb back
to the Overview, and the Overview tab. The profile dropdown is gone (#480): a check under another
company makes it current (current_company.py)."""

from playwright.sync_api import Locator, Page


def back_to_overview(page: Page) -> None:
    """The working paper's breadcrumb, "Overview › 5.1.1": its Overview link."""
    page.get_by_role("navigation", name="Breadcrumb").get_by_role("link", name="Overview").click()


def overview_tab(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Main").get_by_role("link", name="Overview")
