"""Company setup (#403, M8 #351), steps 1 to 3: a developer opens it from the developer page and
walks it, going back and forward without losing an answer; a compliance lead never reaches it.

On the profiles server, as the developer page's checks are (local mode: its one user is a
developer). Nothing is stored: steps 1 to 3 send no profile.
"""

import re

from playwright.sync_api import Page, expect
from test_developer_page import as_compliance_lead

LEVELS = "/api/developer/protection-levels"


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
    expect(page.get_by_role("button", name="Next")).to_have_count(0)
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
