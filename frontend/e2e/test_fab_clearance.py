"""The Ask Vouch button never covers a field, a button or a line of text, at any width (#462).

On the Overview, a working paper, the Report and the Developer page, at 1440, 1024 and 390: each
field, button and link brought into view (tabbed or scrolled to) stops clear of the button, and
scrolled to the bottom the page's last content ends above it, with nothing under it. The pages that
need an assessment loop over the widths, so each spends one from its server's daily limit; the
others are a case per width.

Not checked: the page as it opens. A fixed button over a scrolling page sits over whatever reaches
the viewport's bottom right as the page scrolls, the first screen included; keeping that clear
needs a layout choice the ticket leaves out, which is the owner's (#462).

Each page on the server its own checks use: the working paper on the paper's server with a
remediation item (its due-date field, which the button covered at 1440)."""

import re

import pytest
from fab_clearance import (
    clearance_at_the_bottom,
    under_the_button,
    under_the_button_when_brought_into_view,
)
from playwright.sync_api import Page, expect
from test_profiles import assessed as assessed_in
from test_remediation import confirmed_with_an_item

WIDTHS = [(1440, 900), (1024, 768), (390, 844)]


def opened(page: Page, size: tuple[int, int], url: str, answer: str | re.Pattern[str]) -> None:
    page.set_viewport_size({"width": size[0], "height": size[1]})
    with page.expect_response(answer):
        page.goto(url)
    expect(page.get_by_role("main")).to_be_visible()
    page.wait_for_load_state("networkidle")


def covers_nothing(page: Page) -> None:
    width = page.viewport_size["width"] if page.viewport_size else None
    assert under_the_button_when_brought_into_view(page) == [], width
    assert clearance_at_the_bottom(page) >= 0, width
    assert under_the_button(page) == [], width


def test_on_the_overview(page: Page, profiles_url):
    assessed_in(page.request, profiles_url, "nordlicht", "4.1.2")
    for size in WIDTHS:
        opened(page, size, profiles_url, "**/api/overview")
        covers_nothing(page)


def test_on_a_working_paper_with_a_remediation_item(page: Page, paper_url):
    confirmed_with_an_item(page.request, paper_url)
    for size in WIDTHS:
        opened(page, size, f"{paper_url}/controls/4.1.2", re.compile(r"/api/assessments/[^/]+$"))
        expect(page.get_by_label("Due date").first).to_be_visible()
        covers_nothing(page)


@pytest.mark.parametrize("size", WIDTHS, ids=lambda size: str(size[0]))
def test_on_the_report(page: Page, report_url, size):
    opened(page, size, f"{report_url}/report", "**/api/report")
    covers_nothing(page)


@pytest.mark.parametrize("size", WIDTHS, ids=lambda size: str(size[0]))
def test_on_the_developer_page(page: Page, profiles_url, size):
    opened(page, size, f"{profiles_url}/developer", "**/api/developer/settings")
    covers_nothing(page)
