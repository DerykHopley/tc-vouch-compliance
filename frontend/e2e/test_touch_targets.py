"""Every page's controls meet the touch-target floor at phone width (docs/design.md, Accessibility
floor). The pages on the profiles server, which reaches the developer page; the chat on its own."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, expect
from reviewing import reviewed
from test_company_setup import (
    LEVELS,
    control,
    document,
    open_setup,
    press,
    to_step_3,
    to_step_4,
    to_step_5,
    to_step_6,
    to_step_7,
)
from test_guided_chat import assess_in_the_chat
from test_plan import improving_afresh
from test_profiles import assessed as assessed_in
from test_questions import readiness_afresh
from test_remediation import ITEM, confirmed_with_an_item
from test_review import assessed, open_paper, row
from touch_targets import small_targets

PAGES = ["/", "/controls/4.1.2", "/developer", "/documents"]


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


def test_the_review_round_s_name_field_and_start_round_are_at_least_44px(page: Page, rounds_url):
    # #402: the overview above measures the open round's Close round; here, none open. On the
    # rounds' own server, whose checks read whichever state it is in.
    closed = page.request.post(f"{rounds_url}/api/rounds/close")
    assert closed.status in (200, 409), closed.status  # 409: none was open
    at_phone_width(page, rounds_url)
    expect(page.get_by_role("button", name="Start round")).to_be_visible()
    assert small_targets(page) == []


def test_the_improvement_plan_s_priority_and_mark_done_are_at_least_44px(page: Page, plan_url):
    # #410: an approved item of an accepted control, its Priority select, Mark done and the done
    # form open, in the Internal improvement goal; on the plan's own server.
    improving_afresh(page.request, plan_url)
    thread_id = assessed(page.request, plan_url)["assessment"]["thread_id"]
    reviewed(page.request, plan_url, thread_id, items="approve")
    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        at_phone_width(page, f"{plan_url}/controls/4.1.2")
    item = page.get_by_role("region", name="Remediation item M1")
    expect(item.get_by_label("Priority")).to_be_visible()
    assert small_targets(page) == []
    item.get_by_role("button", name="Mark M1 done").click()
    expect(item.get_by_label("What was done")).to_be_visible()
    assert small_targets(page) == []


def test_the_open_questions_forms_are_at_least_44px(page: Page, questions_url):
    # #414: a listed question's Record answer, then the answer form and the log form open, on a
    # control not assessed; on the questions' own server.
    readiness_afresh(page.request, questions_url)
    due = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
    asked = page.request.post(
        f"{questions_url}/api/controls/1.1.1/questions",
        data={"question": "Who owns the policy?", "asked_of": "CISO", "due_date": due},
    )
    assert asked.ok, asked.status
    at_phone_width(page, f"{questions_url}/controls/1.1.1")
    questions = page.get_by_role("region", name="Open questions")
    questions.get_by_role("button", name="Record answer").click()
    expect(questions.get_by_role("textbox", name="Answer", exact=True)).to_be_visible()
    questions.get_by_role("button", name="Log a question").click()
    expect(questions.get_by_role("form", name="Log a question on 1.1.1")).to_be_visible()
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


def test_the_developer_page_s_remove_buttons_are_at_least_44px(page: Page, profiles_url):
    """#418: a company made in setup listed with its Remove button; the settings answered with
    one added, so no company is made on the profiles server."""
    made = {
        "id": "musterfirma-sud-gmbh",
        "company": "Musterfirma Süd GmbH",
        "created_at": "2026-11-09T10:15:00+00:00",
    }

    def with_a_company(route) -> None:
        answer = route.fetch()
        route.fulfill(response=answer, json=answer.json() | {"companies": [made]})

    page.route("**/api/developer/settings", with_a_company)
    at_phone_width(page, f"{profiles_url}/developer")
    expect(page.get_by_role("button", name="Remove Musterfirma Süd GmbH")).to_be_visible()
    assert small_targets(page) == []


@pytest.mark.parametrize(
    "step", ["company", "levels", "levels failed", "goal", "not applicable", "evidence", "finish"]
)
def test_every_company_setup_control_is_at_least_44px_at_phone_width(
    page: Page, profiles_url, step
):
    # #403: steps 1 and 3, and step 3 when the scope can't be read (its Try again); #407: step 5,
    # a control ticked with its reason field open; #408: step 6, a document ticked; #409: step 7.
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
    if step == "goal":  # #447
        to_step_4(page)
    if step == "not applicable":
        to_step_5(page)
        control(page, "1.6.1").check()
    if step == "evidence":
        to_step_6(page)
        document(page, "Backup policy").check()
    if step == "finish":
        to_step_7(page)
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

    confirmed_with_an_item(page.request, paper_url)  # Plan fixes: S1's item to approve or discard
    paper = open_paper(page, paper_url)
    expect(row(paper, ITEM).get_by_role("button", name=f"Discard {ITEM}")).to_be_visible()
    assert small_targets(page) == []

    # #437: Assess again on a review in progress warns first, with two pills in its place.
    paper.get_by_role("button", name="Assess again 4.1.2").click()
    expect(paper.get_by_role("button", name="Keep the review")).to_be_visible()
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
