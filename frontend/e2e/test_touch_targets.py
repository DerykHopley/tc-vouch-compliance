"""Every page's controls meet the touch-target floor at phone width (docs/design.md, Accessibility
floor). The pages on the profiles server, which reaches the developer page; the chat on its own."""

import re

import pytest
from playwright.sync_api import Page, expect
from test_company_setup import LEVELS, control, open_setup, press, to_step_3, to_step_5
from test_guided_chat import assess_in_the_chat
from test_profiles import assessed as assessed_in
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
    if path == "/":
        # #311: each evidence quote card is a link, so the overview is measured with quotes on it.
        assessed_in(page.request, profiles_url, "nordlicht", "4.1.2")
    at_phone_width(page, f"{profiles_url}{path}")
    if path == "/":
        quotes = page.get_by_role("region", name="What the evidence says")
        expect(quotes.get_by_role("link").first).to_be_visible()
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


@pytest.mark.parametrize("step", ["company", "levels", "levels failed", "not applicable"])
def test_every_company_setup_control_is_at_least_44px_at_phone_width(
    page: Page, profiles_url, step
):
    # #403: steps 1 and 3, and step 3 when the scope can't be read (its Try again); #407: step 5,
    # a control ticked with its reason field open.
    page.set_viewport_size({"width": 400, "height": 900})
    if step == "levels failed":
        page.route(f"**{LEVELS}", lambda route: route.fulfill(status=500, body="{}"))
    open_setup(page, profiles_url)
    if step == "levels":
        to_step_3(page)
        expect(page.get_by_role("group", name="Protection level")).to_contain_text("85 in 12")
    if step == "levels failed":
        page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
        page.get_by_role("combobox", name="Country").select_option("Germany")
        press(page, "Next")
        with page.expect_response(lambda r: LEVELS in r.url):
            press(page, "Next")
        expect(page.get_by_role("button", name="Try again")).to_be_visible()
    if step == "not applicable":
        to_step_5(page)
        control(page, "1.6.1").check()
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
