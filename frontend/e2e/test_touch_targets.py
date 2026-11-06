"""Every page's controls meet the touch-target floor at phone width (docs/design.md, Accessibility
floor). The pages on the profiles server, which reaches the developer page; the chat on its own."""

import pytest
from playwright.sync_api import Page, expect
from test_guided_chat import assess_in_the_chat
from touch_targets import small_targets

PAGES = ["/", "/controls/4.1.2", "/developer"]


def at_phone_width(page: Page, url: str) -> None:
    page.set_viewport_size({"width": 400, "height": 900})
    page.goto(url)
    expect(page.get_by_role("main")).to_be_visible()
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("path", PAGES)
def test_every_control_is_at_least_44px_at_phone_width(page: Page, profiles_url, path):
    at_phone_width(page, f"{profiles_url}{path}")
    assert small_targets(page) == []


def test_the_chat_and_its_action_pills_are_at_least_44px(page: Page, chat_url):
    # #45 failed verify twice on these pills: the chat's own server, as test_guided_chat uses.
    assess_in_the_chat(page, chat_url, 400)
    assert small_targets(page) == []


def test_the_developer_page_at_375px_with_the_daily_limit_field_in_view(page: Page, profiles_url):
    # #198 moved the page's foot clear of the Ask Vouch button at phone width.
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{profiles_url}/developer")
    page.get_by_role("textbox", name="Assessments per day").scroll_into_view_if_needed()
    page.wait_for_load_state("networkidle")
    assert small_targets(page) == []


def test_the_working_paper_links_after_a_cross_control_answer_are_at_least_44px(
    page: Page, chat_url
):
    # #217: a pill per control the answer names, at 400px.
    assess_in_the_chat(page, chat_url, 400)
    page.get_by_role("textbox", name="Message to Vouch").fill(
        "Which controls have documented evidence only?"
    )
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_role("link", name="Working paper 5.1.1")).to_be_visible()
    assert small_targets(page) == []
