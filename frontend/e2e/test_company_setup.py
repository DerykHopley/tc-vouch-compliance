"""Company setup (#403, #407, #408, #409, #447, M8 #351), its seven steps: a developer opens it
from the developer page and walks it, going back and forward without losing an answer, then creates
the company and lands on it, its Goal set and a profile pass running; a compliance lead never
reaches it.

On the profiles server, as the developer page's checks are (local mode: its one user is a
developer), except the checks that create a company: they run on servers of their own, as the
company stays in that process and is made current there (#480). Story 5's runs on
first_pass_url, as every create now runs a profile pass and setup_url's creates would spend the
passes' daily limit (3).
"""

import re

import pytest
from playwright.sync_api import Page, Response, expect
from test_developer_page import as_compliance_lead
from top_bar import bar_company, from_user_menu
from touch_targets import small_targets

LEVELS = "/api/developer/protection-levels"
CONTROLS = "/api/developer/controls"
LIBRARY = "/api/developer/sample-library"
PROFILES = "/api/developer/profiles"
PROFILE_PASS = "/api/profile-pass"
# The wizard ignores a press of its own within 400 ms of the last move (two quick clicks, a
# double-tap); a person's separate presses come further apart, so each helper move waits this long
# first. A double-click's second press is kept from acting whenever it comes.
SETTLE_MS = 450


def press(page: Page, button: str) -> None:
    """Next or Back as a person's separate press: after the last move has settled."""
    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name=button).click()


def open_setup(page: Page, profiles_url: str) -> None:
    with page.expect_response("**/api/developer/settings"):
        page.goto(profiles_url)
    from_user_menu(page, "Set up a company")  # the user menu's, since #495


def to_step_3(page: Page, company: str = "Musterfirma Nord GmbH") -> None:
    page.get_by_role("textbox", name="Company name").fill(company)
    page.get_by_role("combobox", name="Country").select_option("Germany")
    press(page, "Next")
    page.get_by_role("radio", name="German").check()
    with page.expect_response(lambda r: LEVELS in r.url) as answer:
        press(page, "Next")
    assert answer.value.status == 200


def create_company(page: Page) -> Response:
    """Presses Create company on step 7; the create's answer, once the profile pass the new company
    opens with (#447) has answered too, so nothing after it races that pass."""
    with (
        page.expect_response(
            lambda r: r.url.endswith(PROFILES) and r.request.method == "POST"
        ) as created,
        page.expect_response(lambda r: r.url.endswith(PROFILE_PASS)) as passed,
    ):
        press(page, "Create company")
    assert created.value.status == 201
    assert passed.value.ok, passed.value.status
    return created.value


def to_step_4(page: Page, company: str = "Musterfirma Nord GmbH") -> None:
    to_step_3(page, company)
    page.get_by_role("radio", name=re.compile(r"^Normal")).check()
    press(page, "Next")
    expect(page.get_by_role("heading", name="Goal")).to_be_focused()


def to_step_5(page: Page, company: str = "Musterfirma Nord GmbH") -> None:
    """Steps 1 to 4 answered (Internal improvement kept), then Next to step 5."""
    to_step_4(page, company)
    with page.expect_response(lambda r: CONTROLS in r.url) as answer:
        press(page, "Next")
    assert answer.value.status == 200
    expect(page.get_by_role("checkbox")).to_have_count(12)


def to_step_6(page: Page, company: str = "Musterfirma Nord GmbH") -> None:
    to_step_5(page, company)
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


def test_a_developer_opens_company_setup_from_the_user_menu(page: Page, profiles_url):
    open_setup(page, profiles_url)

    expect(page).to_have_url(re.compile(r"/developer/setup$"))
    expect(page.get_by_role("heading", name="Company setup")).to_be_visible()
    expect(page.get_by_text("Step 1 of 7")).to_be_visible()
    # no tab is the setup's own: the developer's pages are in the user menu (#495)
    expect(page.locator("nav[aria-label=Main] [aria-current=page]")).to_have_count(0)


def test_a_developer_opens_company_setup_from_the_developer_page(page: Page, profiles_url):
    page.goto(f"{profiles_url}/developer")
    page.get_by_role("button", name="Set up a company").click()
    expect(page).to_have_url(re.compile(r"/developer/setup$"))
    expect(page.get_by_text("Step 1 of 7")).to_be_visible()


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
    """Story 2: two controls ticked with reasons stay ticked, their reasons as typed, after Back to
    step 4 and Next again."""
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
    expect(page.get_by_text("Step 4 of 7")).to_be_visible()
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
    expect(page.get_by_role("heading", name="Language")).to_be_focused()


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
    expect(backup.locator(".flag")).to_have_text(
        "Stale: approved 2024-02-12 with a yearly review, due 2025-02-12"
    )
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
    press landed on the form and took focus to the page's body. The problem now shows below them
    (step 3); on step 6, focusing the first checkbox scrolls the page, and the second press, like
    every double-click's, is kept from taking focus."""
    open_setup(page, profiles_url)
    to_step_3(page)

    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_text("Choose a protection level.")).to_be_visible()
    expect(page.get_by_role("radio", name=re.compile(r"^Normal"))).to_be_focused()

    page.get_by_role("radio", name=re.compile(r"^Normal")).check()
    press(page, "Next")  # step 4: the Goal
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


def test_step_6_is_walked_by_keyboard(page: Page, profiles_url):
    """Heading, then each folder's Select all and Deselect all (#475) and its checkboxes (policies,
    then records), then Back and Next, with Tab."""
    open_setup(page, profiles_url)
    to_step_6(page)

    expect(page.get_by_role("heading", name="Evidence")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Select all policies", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Deselect all policies", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    first = page.get_by_role("table", name="Policies").get_by_role("checkbox").first
    expect(first).to_be_focused()
    page.keyboard.press("Space")
    expect(first).to_be_checked()
    page.keyboard.press("Tab")
    expect(page.get_by_role("checkbox").nth(1)).to_be_focused()
    page.get_by_role("table", name="Policies").get_by_role("checkbox").last.focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Select all records", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Deselect all records", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("table", name="Records").get_by_role("checkbox").first).to_be_focused()
    page.get_by_role("checkbox").last.focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Back")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Next")).to_be_focused()


def test_select_all_and_deselect_all_work_by_keyboard_one_folder_at_a_time(
    page: Page, profiles_url
):
    """#475: Enter on Select all policies ticks every policy, Space on Deselect all unticks them;
    the records stay as they were, and the ready-to-cite line follows."""
    open_setup(page, profiles_url)
    to_step_6(page)
    policies = page.get_by_role("table", name="Policies").get_by_role("checkbox")
    records = page.get_by_role("table", name="Records").get_by_role("checkbox")
    document(page, "Security training record 2026").check()
    ticked_records = [records.nth(i).is_checked() for i in range(records.count())]

    page.get_by_role("button", name="Select all policies", exact=True).focus()
    page.keyboard.press("Enter")
    for i in range(policies.count()):
        expect(policies.nth(i)).to_be_checked()
    assert [records.nth(i).is_checked() for i in range(records.count())] == ticked_records
    expect(page.get_by_role("status")).to_contain_text(
        f"Ready to cite: {policies.count() + 1} documents"
    )

    page.get_by_role("button", name="Deselect all policies", exact=True).focus()
    page.keyboard.press("Space")
    for i in range(policies.count()):
        expect(policies.nth(i)).not_to_be_checked()
    expect(document(page, "Security training record 2026")).to_be_checked()
    expect(page.get_by_role("status")).to_have_text(re.compile(r"^Ready to cite: 1 document \("))


def test_next_while_the_library_loads_keeps_focus_on_the_heading_when_it_lands(
    page: Page, profiles_url
):
    """No checkbox yet: Next focuses the step's heading, which stays when the library arrives
    (a focused loading note would go, dropping focus to the page's body)."""
    held = []
    page.route(f"**{LIBRARY}", lambda route: held.append(route))
    open_setup(page, profiles_url)
    to_step_5(page)
    press(page, "Next")
    expect(page.get_by_text("Loading the sample library…")).to_be_visible()
    assert len(held) == 1

    press(page, "Next")
    expect(page.get_by_text("Choose at least one document.")).to_be_visible()
    heading = page.get_by_role("heading", name="Evidence")
    expect(heading).to_be_focused()
    with page.expect_response(lambda r: LIBRARY in r.url):
        held[0].continue_()
    expect(page.get_by_role("checkbox")).to_have_count(17)
    expect(heading).to_be_focused()


def test_in_forced_colours_a_document_checkbox_keeps_its_44px_target(page: Page, profiles_url):
    """Forced colours draw the system's 18px box; its label still takes a press anywhere in 44px."""
    page.emulate_media(forced_colors="active")
    page.set_viewport_size({"width": 400, "height": 900})
    open_setup(page, profiles_url)
    to_step_6(page)
    box = document(page, "Backup policy")

    size = box.evaluate(
        """b => { const s = getComputedStyle(b), r = b.getBoundingClientRect()
        return [r.width + parseFloat(s.marginLeft) + parseFloat(s.marginRight),
                r.height + parseFloat(s.marginTop) + parseFloat(s.marginBottom)] }"""
    )
    assert size[0] >= 44 and size[1] >= 44
    label = box.locator("xpath=ancestor::label[1]")
    target = label.bounding_box()
    assert target is not None and target["width"] >= 44 and target["height"] >= 44
    page.mouse.click(target["x"] + 2, target["y"] + 2)  # the target's corner, outside the box
    expect(box).to_be_checked()


def to_step_7(page: Page, company: str = "Musterfirma Nord GmbH") -> None:
    """Steps 1 to 6 answered (no control marked, the backup policy chosen), then Next to step 7."""
    to_step_6(page, company)
    document(page, "Backup policy").check()
    press(page, "Next")
    expect(page.get_by_role("heading", name="Finish")).to_be_focused()


def current_of(page: Page, url: str) -> str:
    """The server's current company (#480), as GET /api/profiles says."""
    listed = page.request.get(f"{url}/api/profiles")
    assert listed.ok, listed.status
    return listed.json()["current"]


def test_a_developer_sets_up_a_company_and_lands_on_it(page: Page, setup_url, nordlicht_later):
    """Stories 1 to 4 end to end: every answer summed up on step 7 (the name and a reason typed
    with spaces around them, shown and sent trimmed), Create company, then the new company made
    current (#480) and on the Dossier, focus on its heading, its marked controls' reasons kept in
    its profile."""
    nordlicht_later(setup_url)
    page.set_viewport_size({"width": 1280, "height": 900})
    open_setup(page, setup_url)
    to_step_5(page, "  Musterfirma Nord GmbH ")
    control(page, "1.6.1").check()
    reason(page, "1.6.1").fill("We build no prototype vehicles.")
    control(page, "5.1.1").check()
    reason(page, "5.1.1").fill("  No cryptography in our own products  ")
    with page.expect_response(lambda r: LIBRARY in r.url):
        press(page, "Next")
    document(page, "Backup policy").check()
    document(page, "Access rights policy").check()
    document(page, "Security training record 2026").check()
    press(page, "Next")

    expect(page.get_by_text("Step 7 of 7")).to_be_visible()
    summary = page.locator("dl.setup-summary")
    # textContent, not to_have_text, which would forgive the spaces
    assert summary.get_by_role("definition").first.text_content() == "Musterfirma Nord GmbH"
    expect(summary).to_contain_text("Germany")
    expect(summary).to_contain_text("German")
    expect(summary).to_contain_text("Normal")
    expect(summary).to_contain_text("Internal improvement")
    expect(summary.get_by_role("listitem")).to_have_count(2)
    assert summary.get_by_role("listitem").all_text_contents() == [
        "1.6.1: We build no prototype vehicles.",
        "5.1.1: No cryptography in our own products",
    ]
    expect(summary).to_contain_text(
        "Ready to cite: 3 documents (2 documented, 1 implemented, 1 stale)."
    )
    expect(page.get_by_role("table", name="Policies").get_by_role("row")).to_have_count(3)
    expect(page.get_by_role("table", name="Records").get_by_role("row")).to_have_count(2)
    expect(page.get_by_role("checkbox")).to_have_count(0)

    created = create_company(page)
    sent = created.request.post_data_json
    assert sent["company"] == "Musterfirma Nord GmbH"
    assert sent["not_applicable"] == [
        {"control": "1.6.1", "reason": "We build no prototype vehicles."},
        {"control": "5.1.1", "reason": "No cryptography in our own products"},
    ]
    profile = created.json()
    assert profile["company"] == "Musterfirma Nord GmbH"

    expect(page).to_have_url(f"{setup_url}/")
    expect(page.get_by_role("heading", level=1)).to_be_focused()
    expect(bar_company(page)).to_have_text("Musterfirma Nord GmbH")
    expect(page.get_by_role("article", name=re.compile(r"^Control "))).to_have_count(12)
    # Its Goal is set and its profile pass has run (#447): story 5's check follows the pass through.
    expect(page.get_by_text("Goal: Internal improvement")).to_be_visible()
    expect(page.get_by_role("region", name="Profile pass")).to_be_visible()
    assert current_of(page, setup_url) == profile["id"]  # every user's company now (#480)

    # The create replaced the wizard's history entry (#476): Back goes to where the developer was
    # before setup (the Dossier, opened first), not to an empty wizard that could make a second.
    page.go_back()
    expect(page).to_have_url(f"{setup_url}/")
    expect(page.get_by_text(re.compile(r"^Step \d of 7$"))).to_have_count(0)
    expect(page.get_by_role("article", name=re.compile(r"^Control "))).to_have_count(12)
    expect(bar_company(page)).to_have_text("Musterfirma Nord GmbH")

    overview = page.request.get(
        f"{setup_url}/api/overview", headers={"X-Vouch-Profile": profile["id"]}
    )
    assert overview.ok, overview.status
    requirements = overview.json()["requirements"]
    for control_id, typed in (
        ("1.6.1", "We build no prototype vehicles."),
        ("5.1.1", "No cryptography in our own products"),
    ):
        assert requirements[control_id]
        assert {r["not_applicable"] for r in requirements[control_id]} == {typed}
    assert all(r["not_applicable"] is None for r in requirements["1.1.1"])

    with page.expect_response(lambda r: r.url.endswith("/api/overview")) as reread:
        page.reload()
    assert reread.value.request.header_value("X-Vouch-Profile") == profile["id"]
    expect(bar_company(page)).to_have_text("Musterfirma Nord GmbH")


def test_a_double_click_on_step_6s_next_lands_on_step_7_and_creates_nothing(
    page: Page, profiles_url
):
    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
    open_setup(page, profiles_url)
    to_step_6(page)
    document(page, "Backup policy").check()

    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Next").dblclick()

    expect(page.get_by_text("Step 7 of 7")).to_be_visible()
    expect(page.get_by_role("heading", name="Finish")).to_be_focused()
    create = page.get_by_role("button", name="Create company")
    page.wait_for_load_state("networkidle")  # a create, had one gone out, has answered by now
    expect(create).to_have_text("Create company")
    expect(create).not_to_have_attribute("aria-busy", "true")
    assert not any(PROFILES in url for url in posted)


def test_a_double_click_on_a_next_that_moves_leaves_focus_on_the_new_heading(
    page: Page, profiles_url
):
    """From #408's verifier: on 3 to 5, 5 to 6 and 6 to 7, the second press left focus on BODY; 3 to
    4 and 4 to 5 since #447 built step 4."""
    open_setup(page, profiles_url)
    to_step_3(page)
    page.get_by_role("radio", name=re.compile(r"^Normal")).check()

    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_role("heading", name="Goal")).to_be_focused()
    expect(page.get_by_role("radio", name=re.compile(r"^Internal improvement"))).to_be_checked()

    page.wait_for_timeout(SETTLE_MS)
    with page.expect_response(lambda r: CONTROLS in r.url):
        page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_role("heading", name="Not applicable")).to_be_focused()
    expect(page.get_by_role("checkbox")).to_have_count(12)
    expect(page.get_by_role("heading", name="Not applicable")).to_be_focused()

    page.wait_for_timeout(SETTLE_MS)
    with page.expect_response(lambda r: LIBRARY in r.url):
        page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_role("checkbox")).to_have_count(17)
    expect(page.get_by_role("heading", name="Evidence")).to_be_focused()

    document(page, "Backup policy").check()
    page.wait_for_timeout(SETTLE_MS)
    page.get_by_role("button", name="Next").dblclick()
    expect(page.get_by_role("heading", name="Finish")).to_be_focused()


def test_a_control_marked_not_applicable_in_setup_reads_so_on_the_dossier(
    page: Page, setup_url, nordlicht_later
):
    """#454: 5.2.9 marked not applicable on step 5. Its card has the Not applicable chip, no review
    tag and the reason; readiness counts the 11 others; opening it reads no stored assessment
    (each read counts to the rate limit), while opening an assessed control does. Its working
    paper has the chip and the reason, and no Assess button (#454 review)."""
    nordlicht_later(setup_url)
    page.set_viewport_size({"width": 1280, "height": 900})
    open_setup(page, setup_url)
    to_step_5(page, "Ausnahmefirma GmbH")
    control(page, "5.2.9").check()
    reason(page, "5.2.9").fill("We run no web applications of our own.")
    with page.expect_response(lambda r: LIBRARY in r.url):
        press(page, "Next")
    document(page, "Backup policy").check()
    press(page, "Next")
    create_company(page)
    expect(bar_company(page)).to_have_text("Ausnahmefirma GmbH")

    card = page.get_by_role("article", name="Control 5.2.9")
    expect(card.locator(".chip")).to_have_class("chip v-not-applicable")
    expect(card.locator(".chip")).to_have_text("Nicht anwendbar")  # the company answers in German
    expect(card.locator(".tag")).to_have_count(0)
    expect(card.locator(".field-note")).to_have_text("We run no web applications of our own.")
    expect(page.get_by_role("article", name=re.compile(r"^Control "))).to_have_count(12)
    expect(page.locator(".hero")).to_contain_text("of 11 controls accepted.")
    expect(page.get_by_role("heading", name="12 controls")).to_be_visible()
    # The new card state meets the touch-target floor at phone width (the check lives here, not in
    # test_touch_targets.py, so it costs no fourth create on setup_url: the passes' daily limit).
    page.set_viewport_size({"width": 400, "height": 900})
    card.scroll_into_view_if_needed()
    assert small_targets(page) == []
    page.set_viewport_size({"width": 1280, "height": 900})

    reads: list[str] = []
    page.on(
        "request", lambda r: reads.append(r.url) if r.url.endswith("/api/assessments") else None
    )
    card.get_by_role("link", name="Open 5.2.9").click()
    expect(page).to_have_url(re.compile(r"/controls/5\.2\.9$"))
    page.wait_for_load_state("networkidle")
    assert reads == []
    # Its working paper says so too, and offers no assessment: nothing could review its result.
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.locator(".paper-meta .chip")).to_have_text("Nicht anwendbar")
    expect(paper.locator(".paper-head .field-note")).to_have_text(
        "We run no web applications of our own."
    )
    expect(paper.get_by_text("Not assessed yet")).to_have_count(0)
    expect(paper.get_by_role("button", name=re.compile(r"^Assess"))).to_have_count(0)
    assessed = page.get_by_role("navigation", name="Controls").get_by_role(
        "link", name=re.compile(r"^1\.1\.1")
    )
    with page.expect_request(lambda r: r.url.endswith("/api/assessments")):
        assessed.click()  # proposed by the profile pass: its paper reads it


RING = "el => getComputedStyle(el).outlineStyle"


def test_step_7_is_walked_by_keyboard_and_a_failed_create_keeps_focus_on_create_company(
    page: Page, profiles_url
):
    """Back and Create company by Tab, each with a visible focus ring; a 503 (routed: nothing is
    kept) says so, and focus is back on Create company to press again."""
    page.route(
        f"**{PROFILES}",
        lambda route: route.fulfill(status=503, json={"detail": "down", "unavailable": "database"}),
    )
    open_setup(page, profiles_url)
    to_step_7(page)

    back = page.get_by_role("button", name="Back")
    create = page.get_by_role("button", name="Create company")
    page.keyboard.press("Tab")  # from the heading: the tables hold nothing to focus
    expect(back).to_be_focused()
    assert back.evaluate(RING) != "none"
    page.keyboard.press("Tab")
    expect(create).to_be_focused()
    assert create.evaluate(RING) != "none"

    page.wait_for_timeout(SETTLE_MS)  # a press of its own, not the move's landing again
    with page.expect_response(lambda r: PROFILES in r.url):
        page.keyboard.press("Enter")
    expect(page.get_by_role("alert")).to_have_text(
        "The database is unavailable just now. Nothing was saved; try again in a moment."
    )
    expect(create).to_be_focused()
    expect(create).to_be_enabled()
    expect(page.locator("dl.setup-summary")).to_contain_text("Musterfirma Nord GmbH")


@pytest.mark.parametrize("width", [400, 900, 1400])
def test_step_7_scrolls_sideways_only_inside_its_tables(page: Page, profiles_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    open_setup(page, profiles_url)
    to_step_7(page)

    page_overflow = page.evaluate(
        "document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
    )
    assert page_overflow <= 0


CHOSEN = {
    "Information security policy": "information-security-policy",
    "Backup policy": "backup-policy",
    "Security training record 2026": "training-record",
}


def test_story_5_a_new_company_opens_with_its_goal_set_and_every_control_drafted(
    page: Page, first_pass_url, nordlicht_later
):
    """Story 5 end to end (#447): setup with Internal improvement left chosen on step 4 and a few
    library documents, Create company, then the new company's Overview with its Goal set and its
    profile pass's result: every card waiting for review, and an opened working paper citing only
    chosen documents."""
    nordlicht_later(first_pass_url)
    page.set_viewport_size({"width": 1280, "height": 900})
    open_setup(page, first_pass_url)
    to_step_4(page, "Erstpass GmbH")
    expect(page.get_by_text("Step 4 of 7")).to_be_visible()
    expect(page.get_by_role("radio", name=re.compile(r"^Internal improvement"))).to_be_checked()
    expect(page.get_by_text("You can change the Goal later on the overview.")).to_be_visible()
    with page.expect_response(lambda r: CONTROLS in r.url):
        press(page, "Next")
    with page.expect_response(lambda r: LIBRARY in r.url):
        press(page, "Next")
    for title in CHOSEN:
        document(page, title).check()
    press(page, "Next")
    summary = page.locator("dl.setup-summary")
    expect(summary).to_contain_text("Internal improvement")
    expect(
        page.get_by_text(re.compile(r"Then Vouch sets its Goal and runs a profile pass"))
    ).to_be_visible()

    with page.expect_response(
        lambda r: r.url.endswith("/api/goal") and r.request.method == "PUT"
    ) as goal:
        created = create_company(page)
    profile = created.json()["id"]
    assert goal.value.ok, goal.value.status
    assert goal.value.request.post_data_json == {"goal": "internal-improvement"}
    assert goal.value.request.headers["x-vouch-profile"] == profile

    expect(page).to_have_url(f"{first_pass_url}/")
    expect(bar_company(page)).to_have_text("Erstpass GmbH")
    assert current_of(page, first_pass_url) == profile  # every user's company now (#480)
    expect(page.get_by_text("Goal: Internal improvement")).to_be_visible()
    panel = page.get_by_role("region", name="Profile pass")
    expect(panel).to_contain_text("12 assessments waiting for your review.")
    cards = page.get_by_role("article", name=re.compile(r"^Control "))
    expect(cards).to_have_count(12)
    for card in cards.all():
        expect(card).to_contain_text("Proposed")

    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        panel.get_by_role("button", name="Open 1.1.1").click()
    paper = page.get_by_role("region", name="Working paper")
    cited = paper.locator("[data-doc-id]")
    expect(cited.first).to_be_visible()
    for doc in cited.evaluate_all("spans => spans.map(s => s.dataset.docId)"):
        assert doc in CHOSEN.values(), doc
