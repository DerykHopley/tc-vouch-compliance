"""Every page's controls meet the touch-target floor at phone width (docs/design.md, Accessibility
floor). The pages on the profiles server, which reaches the developer page; the chat on its own."""

import re

import pytest
from playwright.sync_api import Page, expect
from test_guided_chat import assess_in_the_chat
from test_remediation import ITEM, accepted_with_an_item
from test_review import assessed, open_paper, row
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


def test_the_readiness_report_s_controls_are_at_least_44px(page: Page, report_url):
    # #56: the report page on its own server, with whatever the report's checks have reviewed.
    at_phone_width(page, f"{report_url}/report")
    expect(page.locator("article.report-doc")).to_be_visible()
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


def test_a_requirement_card_s_toggle_override_and_remediation_controls_are_at_least_44px(
    page: Page, paper_url
):
    # #211: on a phone each requirement is a card; its controls keep the floor there too.
    page.set_viewport_size({"width": 400, "height": 900})
    assessed(page.request, paper_url)  # proposed: each card offers Change
    paper = open_paper(page, paper_url)
    row(paper, "M1").get_by_role("button", name="Change M1").click()
    expect(page.get_by_role("form", name="Override M1")).to_be_visible()
    assert small_targets(page) == []

    accepted_with_an_item(page.request, paper_url)  # accepted, with S1's item to approve or discard
    paper = open_paper(page, paper_url)
    expect(row(paper, ITEM).get_by_role("button", name=f"Discard {ITEM}")).to_be_visible()
    assert small_targets(page) == []


def test_the_top_bar_menu_and_its_menus_are_at_least_44px(page: Page, profiles_url):
    # #222: on a phone the tabs, the profile dropdown and the user menu sit behind one Menu button.
    page.set_viewport_size({"width": 400, "height": 900})
    with page.expect_response("**/api/overview"):
        page.goto(profiles_url)
    bar = page.get_by_role("banner")
    bar.get_by_role("button", name="Menu", exact=True).click()
    bar.get_by_role("button", name=re.compile(r"^Profile: ")).click()
    expect(bar.get_by_role("menuitemradio").first).to_be_visible()
    assert small_targets(page) == []


def test_the_chat_s_new_conversation_control_is_at_least_44px(page: Page, chat_url):
    # #251: "New conversation" in the chat's header, at 400px (a full-screen sheet there).
    page.set_viewport_size({"width": 400, "height": 900})
    with page.expect_response("**/api/overview"):
        page.goto(chat_url + "/")
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    expect(page.get_by_role("button", name="New conversation")).to_be_visible()
    assert small_targets(page) == []
