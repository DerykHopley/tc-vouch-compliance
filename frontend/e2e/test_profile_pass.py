"""The profile pass from the Dossier (#390): stories 4 and 5 of #350, on a server of their own
(PASS_URL), Nordlicht on the bundled pass script (the scripted fake model, no paid call).

The tests run in this file's order on one server: the first pass assesses all 12 controls, the
second (after one is accepted) skips that one, and the fit check reads its result; the third
(after 4.1.2's findings are confirmed) skips that review in progress too, and its paper's Assess
again warns first (#437). The pass's own daily limit is 3 a day, so the file runs three."""

import re

import reviewing
from playwright.sync_api import Page, Route, expect
from touch_targets import small_targets

CONTROLS = 12
PROFILE_PASS = "**/api/profile-pass"


def open_dossier(page: Page, pass_url: str) -> None:
    with page.expect_response("**/api/overview"):
        page.goto(pass_url)


def run_pass(page: Page) -> None:
    """Presses Run a profile pass with its request held, so the running state is checked while
    the pass runs however fast the fake answers (#693), then releases it and waits for its answer."""
    held: list[Route] = []
    page.route(PROFILE_PASS, lambda route: held.append(route))
    page.get_by_role("button", name="Run a profile pass").click()
    held_once(page, held)
    running = page.get_by_role("button", name="Running the profile pass…")
    expect(running).to_be_disabled()
    expect(running).to_have_attribute("aria-busy", "true")
    expect(page.get_by_role("main").get_by_role("status")).to_have_text(
        "Vouch is assessing every open control in one run. This can take a few minutes."
    )
    with page.expect_response(PROFILE_PASS) as answered:
        held[0].continue_()
    page.unroute(PROFILE_PASS)
    assert answered.value.ok, answered.value.status


def held_once(page: Page, held: list[Route]) -> None:
    """Waits until the route holding the pass has caught its one request (up to 5 s), as
    test_company_setup's held creates do; the route handler runs while Playwright waits."""
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1, len(held)


def panel(page: Page):
    return page.get_by_role("region", name="Profile pass")


def card(page: Page, control_id: str):
    return page.get_by_role("article", name=f"Control {control_id}")


def test_story_4_a_pass_from_the_dossier_proposes_every_control(page: Page, pass_url):
    open_dossier(page, pass_url)
    expect(card(page, "1.1.1")).to_contain_text("Not assessed")

    run_pass(page)

    expect(panel(page)).to_contain_text(f"{CONTROLS} assessments waiting for your review.")
    expect(panel(page).get_by_role("listitem")).to_have_count(CONTROLS)
    for article in card(page, "").all():
        expect(article).to_contain_text("Proposed")
    expect(page.get_by_role("article", name="Control ")).to_have_count(CONTROLS)


def test_story_5_a_second_pass_skips_the_accepted_and_one_control_is_assessed_again(
    page: Page, pass_url
):
    listed = page.request.get(f"{pass_url}/api/assessments").json()["assessments"]
    first = next(a for a in listed if a["control_id"] == "1.1.1")
    reviewing.reviewed(page.request, pass_url, first["thread_id"], "accept")
    open_dossier(page, pass_url)
    expect(card(page, "1.1.1")).to_contain_text("Accepted")

    run_pass(page)

    expect(panel(page)).to_contain_text(f"{CONTROLS - 1} assessments waiting for your review.")
    expect(panel(page)).to_contain_text("1 accepted control skipped: 1.1.1.")
    fits(page)
    again = panel(page).get_by_role("button", name="again").first
    control_id = again.inner_text().removeprefix("Assess ").removesuffix(" again")
    # The row's control waits for review, so the row opens its paper and starts nothing (#437);
    # at Confirm findings the paper's Assess again starts the run in one click.
    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        again.click()
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("list", name="Agent steps")).to_have_count(0)
    with page.expect_response("**/api/assessments"):
        paper.get_by_role("button", name=f"Assess again {control_id}").click()
    steps = paper.get_by_role("list", name="Agent steps").get_by_role("listitem")
    expect(steps.first).to_contain_text(f"Loaded control {control_id}: ")
    expect(steps.last).to_contain_text("Waiting for the compliance lead's review.")


def test_a_third_pass_skips_a_review_in_progress_and_assess_again_warns_first(page: Page, pass_url):
    # #437: 4.1.2's findings confirmed (its newest assessment, from the passes or the per-control
    # run before), so its review waits at Plan fixes or Accept.
    listed = page.request.get(f"{pass_url}/api/assessments").json()["assessments"]
    newest = next(a for a in listed if a["control_id"] == "4.1.2")  # newest first
    reviewing.confirmed(page.request, pass_url, newest["thread_id"])
    open_dossier(page, pass_url)

    run_pass(page)

    expect(panel(page)).to_contain_text("1 accepted control skipped: 1.1.1.")
    expect(panel(page)).to_contain_text(
        "1 control in review skipped: 4.1.2. Finish their review, or assess one again from its paper."
    )
    expect(panel(page).get_by_role("listitem", name="Control 4.1.2")).to_have_count(0)

    with page.expect_response(f"**/api/assessments/{newest['thread_id']}"):
        card(page, "4.1.2").get_by_role("link", name="Open 4.1.2").click()
    paper = page.get_by_role("region", name="Working paper")
    current = paper.get_by_role("list", name="Review steps").locator("[aria-current=step]")
    expect(current).to_have_text(re.compile("^(Plan fixes|Accept)$"))
    assess_again = paper.get_by_role("button", name="Assess again 4.1.2")
    assess_again.focus()
    page.keyboard.press("Enter")  # the keyboard, so focus is checked where jsdom can't
    warning = paper.get_by_role("alert")
    expect(warning).to_have_text(
        "Assessing 4.1.2 again sets its review aside: the findings you confirmed and the "
        "remediation items you decided won't carry over to the new assessment. Nothing is "
        "deleted; they stay in the audit trail."
    )
    keep = paper.get_by_role("button", name="Keep the review")
    expect(keep).to_be_focused()
    expect(paper.get_by_role("button", name="Assess 4.1.2 again anyway")).to_be_visible()
    page.set_viewport_size({"width": 400, "height": 900})
    assert small_targets(page) == []

    page.keyboard.press("Enter")
    expect(warning).to_have_count(0)
    expect(assess_again).to_be_focused()
    expect(current).to_have_text(re.compile("^(Plan fixes|Accept)$"))  # no run started
    expect(paper.get_by_role("list", name="Agent steps")).to_have_count(0)


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def fits(page: Page) -> None:
    """The hero with the pass button and the result panel fit at 400px and 1280px, light and
    dark: nothing scrolls sideways, every control at least 44px; the pass button is reachable
    by keyboard, Tab from the next step. The theme is set as the user menu sets it, on the page
    as it stands, so the pass's result stays shown."""
    for width in (400, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        for theme in ("light", "dark"):
            page.evaluate("theme => { document.documentElement.dataset.theme = theme }", theme)
            expect(panel(page)).to_be_visible()
            assert not scrolls_sideways(page), f"the Dossier scrolls sideways at {width}px, {theme}"
            assert small_targets(page) == [], (width, theme)
    hero = page.locator(".hero")
    # The next step: the hero's first button in its row (Change goal, on the goal line above it, comes first, #369).
    hero.locator(".next").get_by_role("button").first.focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Run a profile pass")).to_be_focused()
