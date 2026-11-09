"""Company setup (#403, #407, #408, M8 #351), steps 1 to 3, 5 and 6: a developer opens it from the
developer page and walks it, going back and forward without losing an answer; a compliance lead
never reaches it.

On the profiles server, as the developer page's checks are (local mode: its one user is a
developer). Nothing is stored: no step sends a profile yet.
"""

import re

import pytest
from playwright.sync_api import Page, expect
from test_developer_page import as_compliance_lead

LEVELS = "/api/developer/protection-levels"
CONTROLS = "/api/developer/controls"
LIBRARY = "/api/developer/sample-library"
# The wizard ignores a move within 400 ms of the last (a double-click's second press); a person's
# separate presses come further apart, so each helper move waits this long first.
SETTLE_MS = 450


def press(page: Page, button: str) -> None:
    """Next or Back as a person's separate press: after the last move has settled."""
    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name=button).click()


def open_setup(page: Page, profiles_url: str) -> None:
    page.goto(f"{profiles_url}/developer")
    page.get_by_role("button", name="Set up a company").click()


def to_step_3(page: Page) -> None:
    page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
    page.get_by_role("combobox", name="Country").select_option("Germany")
    press(page, "Next")
    page.get_by_role("radio", name="German").check()
    with page.expect_response(lambda r: LEVELS in r.url) as answer:
        press(page, "Next")
    assert answer.value.status == 200


def to_step_5(page: Page) -> None:
    to_step_3(page)
    page.get_by_role("radio", name=re.compile(r"^Normal")).check()
    with page.expect_response(lambda r: CONTROLS in r.url) as answer:
        press(page, "Next")
    assert answer.value.status == 200
    expect(page.get_by_role("checkbox")).to_have_count(12)


def to_step_6(page: Page) -> None:
    to_step_5(page)
    with page.expect_response(lambda r: LIBRARY in r.url) as answer:
        press(page, "Next")
    assert answer.value.status == 200
    expect(page.get_by_role("checkbox")).to_have_count(17)


def document(page: Page, title: str):
    return page.get_by_role("checkbox", name=f"Choose {title}", exact=True)


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

    press(page, "Back")
    press(page, "Back")
    expect(page.get_by_role("textbox", name="Company name")).to_have_value("Musterfirma Nord GmbH")
    expect(page.get_by_role("combobox", name="Country")).to_have_value("Germany")
    press(page, "Next")
    expect(page.get_by_role("radio", name="German")).to_be_checked()
    press(page, "Next")
    expect(page.get_by_role("radio", name=re.compile(r"^High"))).to_be_checked()
    assert not any("/api/developer/profiles" in url for url in posted)


def test_next_and_back_move_focus_to_the_step_heading(page: Page, profiles_url):
    open_setup(page, profiles_url)
    page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
    page.get_by_role("combobox", name="Country").select_option("Germany")

    page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("heading", name="Language")).to_be_focused()
    with page.expect_response(lambda r: LEVELS in r.url):
        press(page, "Next")
    expect(page.get_by_role("heading", name="Protection level")).to_be_focused()
    press(page, "Back")
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

    press(page, "Back")
    expect(page.get_by_text("Step 3 of 7")).to_be_visible()
    press(page, "Next")

    expect(control(page, "1.6.1")).to_be_checked()
    expect(reason(page, "1.6.1")).to_have_value("We build no prototype vehicles.")
    expect(control(page, "5.1.1")).to_be_checked()
    expect(reason(page, "5.1.1")).to_have_value("No cryptography in our own products ")
    expect(control(page, "4.1.2")).not_to_be_checked()
    assert not any("/api/developer/profiles" in url for url in posted)


def fill_step_1(page: Page) -> None:
    page.get_by_role("textbox", name="Company name").fill("Musterfirma Nord GmbH")
    page.get_by_role("combobox", name="Country").select_option("Germany")


def test_a_double_click_on_next_or_back_moves_exactly_one_step(page: Page, profiles_url):
    """From #403's verifier: a double-click on step 1's Next landed on step 3."""
    open_setup(page, profiles_url)
    fill_step_1(page)

    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()

    page.wait_for_timeout(SETTLE_MS)
    with page.expect_response(lambda r: LEVELS in r.url):
        page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Step 3 of 7")).to_be_visible()
    expect(page.get_by_text("Choose a protection level.")).to_have_count(0)

    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Back").dblclick()
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()
    page.wait_for_timeout(SETTLE_MS)
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()


def test_two_quick_clicks_on_next_or_back_move_exactly_one_step(page: Page, profiles_url):
    """Two separate clicks (each a click of its own, not a double-click) close together."""
    open_setup(page, profiles_url)
    fill_step_1(page)
    next_button = page.get_by_role("button", name="Next")

    next_button.click()
    next_button.click()
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()

    page.wait_for_timeout(SETTLE_MS)
    with page.expect_response(lambda r: LEVELS in r.url):
        next_button.click()
        next_button.click()
    expect(page.get_by_text("Step 3 of 7")).to_be_visible()
    expect(page.get_by_text("Choose a protection level.")).to_have_count(0)

    page.wait_for_timeout(SETTLE_MS)
    back_button = page.get_by_role("button", name="Back")
    back_button.click()
    back_button.click()
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()
    page.wait_for_timeout(SETTLE_MS)
    expect(page.get_by_text("Step 2 of 7")).to_be_visible()


def test_a_fast_double_tap_on_next_or_back_moves_exactly_one_step(browser, profiles_url):
    """On a touch screen, two taps close together."""
    context = browser.new_context(has_touch=True, viewport={"width": 400, "height": 800})
    page = context.new_page()
    try:
        open_setup(page, profiles_url)
        fill_step_1(page)
        next_button = page.get_by_role("button", name="Next")

        next_button.tap()
        next_button.tap()
        expect(page.get_by_text("Step 2 of 7")).to_be_visible()

        page.wait_for_timeout(SETTLE_MS)
        with page.expect_response(lambda r: LEVELS in r.url):
            next_button.tap()
        expect(page.get_by_text("Step 3 of 7")).to_be_visible()
        page.wait_for_timeout(SETTLE_MS)
        back_button = page.get_by_role("button", name="Back")
        back_button.tap()
        back_button.tap()
        expect(page.get_by_text("Step 2 of 7")).to_be_visible()
        page.wait_for_timeout(SETTLE_MS)
        expect(page.get_by_text("Step 2 of 7")).to_be_visible()
    finally:
        context.close()


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


def test_a_developer_chooses_evidence_and_sees_it_ready_to_cite(page: Page, profiles_url):
    """Stories 3 and 4: two policies (one of them the stale backup policy) and one record, each
    row with what Vouch read from it; the count follows; Back and Next keep all three ticked."""
    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
    open_setup(page, profiles_url)
    to_step_6(page)

    expect(page.get_by_text("Step 6 of 7")).to_be_visible()
    expect(page.get_by_role("heading", name="Evidence")).to_be_focused()
    policies = page.get_by_role("table", name="Policies")
    backup = policies.get_by_role("row").filter(has_text="Backup policy")
    expect(backup).to_contain_text("backup-policy")
    expect(backup.locator(".kind.kind-documented")).to_have_text("documented")
    expect(backup).to_contain_text("1.0")
    expect(backup).to_contain_text("2024-02-12")
    expect(backup.locator(".flag")).to_have_text("Stale")
    access = policies.get_by_role("row").filter(has_text="Access rights policy")
    expect(access).to_contain_text("3.2")
    expect(access).to_contain_text("2026-07-20")
    expect(access).to_contain_text("Current")
    records = page.get_by_role("table", name="Records")
    training = records.get_by_role("row").filter(has_text="Security training record 2026")
    expect(training.locator(".kind.kind-implemented")).to_have_text("implemented")
    expect(training).to_contain_text("2026-10-05")

    status = page.get_by_role("status")
    expect(status).to_have_text("No documents chosen yet.")
    document(page, "Backup policy").check()
    document(page, "Access rights policy").check()
    document(page, "Security training record 2026").check()
    expect(status).to_have_text(
        "Ready to cite: 3 documents (2 documented, 1 implemented, 1 stale)."
    )

    press(page, "Back")
    expect(page.get_by_text("Step 5 of 7")).to_be_visible()
    press(page, "Next")
    for title in ("Backup policy", "Access rights policy", "Security training record 2026"):
        expect(document(page, title)).to_be_checked()
    expect(document(page, "Logging standard")).not_to_be_checked()
    expect(status).to_have_text(
        "Ready to cite: 3 documents (2 documented, 1 implemented, 1 stale)."
    )
    assert not any("/api/developer/profiles" in url for url in posted)


def test_a_double_click_on_next_that_doesnt_move_keeps_focus_on_the_first_invalid_field(
    page: Page, profiles_url
):
    """From #407's verifier: the problem shown above the buttons moved Next down, so the second
    press landed on the form and took focus to the page's body."""
    open_setup(page, profiles_url)
    to_step_3(page)

    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Choose a protection level.")).to_be_visible()
    expect(page.get_by_role("radio", name=re.compile(r"^Normal"))).to_be_focused()

    page.get_by_role("radio", name=re.compile(r"^Normal")).check()
    with page.expect_response(lambda r: CONTROLS in r.url):
        press(page, "Next")
    expect(page.get_by_role("checkbox")).to_have_count(12)
    with page.expect_response(lambda r: LIBRARY in r.url):
        press(page, "Next")
    expect(page.get_by_role("checkbox")).to_have_count(17)

    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Choose at least one document.")).to_be_visible()
    expect(page.get_by_role("checkbox").first).to_be_focused()


@pytest.mark.parametrize("width", [400, 900, 1400])
def test_step_6_scrolls_sideways_only_inside_its_tables(page: Page, profiles_url, width):
    """design.md, Layout: wide tables scroll in their own container, never the page."""
    page.set_viewport_size({"width": width, "height": 900})
    open_setup(page, profiles_url)
    to_step_6(page)

    page_overflow = page.evaluate(
        "document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
    )
    assert page_overflow <= 0
    if width == 400:
        wrap = page.get_by_role("table", name="Policies").locator("xpath=..")
        assert wrap.evaluate("w => w.scrollWidth > w.clientWidth")
