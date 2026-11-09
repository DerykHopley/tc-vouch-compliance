"""Company setup (#403, #407, M8 #351), steps 1 to 3 and 5: a developer opens it from the
developer page and walks it, going back and forward without losing an answer; a compliance lead
never reaches it.

On the profiles server, as the developer page's checks are (local mode: its one user is a
developer). Nothing is stored: no step sends a profile yet.
"""

import re

from playwright.sync_api import Page, expect
from test_developer_page import as_compliance_lead

LEVELS = "/api/developer/protection-levels"
CONTROLS = "/api/developer/controls"


def open_setup(page: Page, profiles_url: str) -> None:
    page.goto(f"{profiles_url}/developer")
    page.get_by_role("button", name="Set up a company").click()


def to_step_3(page: Page) -> None:
    page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
    page.get_by_role("combobox", name="Country").select_option("Germany")
    page.get_by_role("button", name="Next").click()
    page.get_by_role("radio", name="German").check()
    with page.expect_response(lambda r: LEVELS in r.url) as answer:
        page.get_by_role("button", name="Next").click()
    assert answer.value.status == 200


def to_step_5(page: Page) -> None:
    to_step_3(page)
    page.get_by_role("radio", name=re.compile(r"^Normal")).check()
    with page.expect_response(lambda r: CONTROLS in r.url) as answer:
        page.get_by_role("button", name="Next").click()
    assert answer.value.status == 200
    expect(page.get_by_role("checkbox")).to_have_count(12)


def control(page: Page, control_id: str):
    return page.get_by_role("checkbox", name=re.compile(rf"^{re.escape(control_id)} "))


def reason(page: Page, control_id: str):
    return page.get_by_role("textbox", name=f"Why {control_id} doesn't apply")


def test_a_developer_opens_company_setup_from_the_developer_page(page: Page, profiles_url):
    open_setup(page, profiles_url)

    expect(page).to_have_url(re.compile(r"/developer/setup$"))
    expect(page.get_by_role("heading", name="Company setup")).to_be_visible()
    expect(page.get_by_text("Step 1 of 7")).to_be_visible()
    developer = page.get_by_role("navigation", name="Main").get_by_role("link", name="Developer")
    expect(developer).to_have_attribute("aria-current", "page")


def test_a_compliance_lead_at_the_setup_address_gets_the_dossier(page: Page, profiles_url):
    as_compliance_lead(page)

    with page.expect_response(lambda r: "/api/developer/" in r.url) as answer:
        page.goto(f"{profiles_url}/developer/setup")
    assert answer.value.status == 403

    expect(page.get_by_role("heading", level=1)).to_contain_text("Prepared for audit")
    expect(page.get_by_role("region", name="Company setup")).to_have_count(0)


def test_a_developer_walks_steps_1_to_3_and_back_keeping_every_answer(page: Page, profiles_url):
    """Story 1: name and country, German, the protection levels with the route's numbers."""
    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
    open_setup(page, profiles_url)
    to_step_3(page)

    levels = page.get_by_role("group", name="Protection level")
    expect(levels).to_contain_text("Must and should requirements: 85 in 12 controls.")
    expect(levels).to_contain_text(
        re.compile(r"Adds 14 high-protection requirements, .*: 99 in all\.")
    )
    expect(levels).to_contain_text(
        re.compile(r"Adds 10 very-high-protection requirements, .*: 109 in all\.")
    )
    page.get_by_role("radio", name=re.compile(r"^High")).check()

    page.get_by_role("button", name="Back").click()
    page.get_by_role("button", name="Back").click()
    expect(page.get_by_role("textbox", name="Company name")).to_have_value("Musterfirma Nord GmbH")
    expect(page.get_by_role("combobox", name="Country")).to_have_value("Germany")
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("radio", name="German")).to_be_checked()
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("radio", name=re.compile(r"^High"))).to_be_checked()
    assert not any("/api/developer/profiles" in url for url in posted)


def test_next_and_back_move_focus_to_the_step_heading(page: Page, profiles_url):
    open_setup(page, profiles_url)
    page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
    page.get_by_role("combobox", name="Country").select_option("Germany")

    page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("heading", name="Language")).to_be_focused()
    with page.expect_response(lambda r: LEVELS in r.url):
        page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("heading", name="Protection level")).to_be_focused()
    page.get_by_role("button", name="Back").click()
    expect(page.get_by_role("heading", name="Language")).to_be_focused()


def test_next_with_a_missing_answer_focuses_the_first_invalid_field(page: Page, profiles_url):
    open_setup(page, profiles_url)
    name = page.get_by_role("textbox", name="Company name")
    country = page.get_by_role("combobox", name="Country")

    page.get_by_role("button", name="Next").click()
    expect(name).to_be_focused()
    expect(country).to_have_attribute("aria-invalid", "true")

    name.fill("Musterfirma Nord GmbH")
    page.get_by_role("button", name="Next").click()
    expect(country).to_be_focused()
    expect(page.get_by_role("heading", name="Company", exact=True)).to_be_visible()


def test_enter_in_the_name_field_moves_on_as_next_does(page: Page, profiles_url):
    open_setup(page, profiles_url)
    page.get_by_role("combobox", name="Country").select_option("Germany")
    name = page.get_by_role("textbox", name="Company name")
    name.fill("Musterfirma Nord GmbH")

    name.press("Enter")

    expect(page.get_by_role("heading", name="Language")).to_be_focused()


def test_a_developer_marks_two_controls_not_applicable_and_keeps_them_going_back(
    page: Page, profiles_url
):
    """Story 2: step 3's Next skips the Goal to step 5; two controls ticked with reasons stay
    ticked, their reasons as typed, after Back to step 3 and Next again."""
    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
    open_setup(page, profiles_url)
    to_step_5(page)

    expect(page.get_by_text("Step 5 of 7")).to_be_visible()
    expect(page.get_by_role("heading", name="Not applicable")).to_be_focused()
    control(page, "1.6.1").check()
    reason(page, "1.6.1").fill("We build no prototype vehicles.")
    control(page, "5.1.1").check()
    reason(page, "5.1.1").fill("No cryptography in our own products ")

    page.get_by_role("button", name="Back").click()
    expect(page.get_by_text("Step 3 of 7")).to_be_visible()
    page.get_by_role("button", name="Next").click()

    expect(control(page, "1.6.1")).to_be_checked()
    expect(reason(page, "1.6.1")).to_have_value("We build no prototype vehicles.")
    expect(control(page, "5.1.1")).to_be_checked()
    expect(reason(page, "5.1.1")).to_have_value("No cryptography in our own products ")
    expect(control(page, "4.1.2")).not_to_be_checked()
    assert not any("/api/developer/profiles" in url for url in posted)


def test_a_double_click_on_next_moves_exactly_one_step(page: Page, profiles_url):
    """From #403's verifier: a double-click on step 1's Next landed on step 3."""
    open_setup(page, profiles_url)
    page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
    page.get_by_role("combobox", name="Country").select_option("Germany")

    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()

    with page.expect_response(lambda r: LEVELS in r.url):
        page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Step 3 of 7")).to_be_visible()

    page.get_by_role("radio", name=re.compile(r"^Normal")).check()
    with page.expect_response(lambda r: CONTROLS in r.url):
        page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Step 5 of 7")).to_be_visible()

    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Step 5 of 7")).to_be_visible()
    expect(page.get_by_role("heading", name="Not applicable")).to_be_visible()


def test_step_5_is_walked_by_keyboard(page: Page, profiles_url):
    """Each checkbox, its reason field and the buttons are reached and left with Tab."""
    open_setup(page, profiles_url)
    to_step_5(page)

    first = control(page, "1.1.1")
    first.focus()
    page.keyboard.press("Space")
    expect(first).to_be_checked()
    page.keyboard.press("Tab")
    expect(reason(page, "1.1.1")).to_be_focused()
    page.keyboard.type("Not here")
    page.keyboard.press("Tab")
    expect(page.get_by_role("checkbox").nth(1)).to_be_focused()
    page.get_by_role("checkbox").last.focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Back")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Next")).to_be_focused()
