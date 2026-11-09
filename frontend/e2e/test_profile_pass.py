"""The profile pass from the Dossier (#390): stories 4 and 5 of #350, on a server of their own
(PASS_URL), Nordlicht on the bundled pass script (the scripted fake model, no paid call).

The tests run in this file's order on one server: the first pass assesses all 12 controls, the
second (after one is accepted) skips that one, and the fit check reads its result. The pass's own
daily limit is 3 a day, so the file runs two."""

import reviewing
from playwright.sync_api import Page, expect
from touch_targets import small_targets

CONTROLS = 12


def open_dossier(page: Page, pass_url: str) -> None:
    with page.expect_response("**/api/overview"):
        page.goto(pass_url)


def run_pass(page: Page) -> None:
    """Presses Run a profile pass, sees it running, and waits for its answer and the overview read
    after it."""
    button = page.get_by_role("button", name="Run a profile pass")
    with page.expect_response("**/api/profile-pass") as answered:
        button.click()
        running = page.get_by_role("button", name="Running the profile pass…")
        expect(running).to_be_disabled()
        expect(running).to_have_attribute("aria-busy", "true")
        expect(page.get_by_role("status")).to_have_text(
            "Vouch is assessing every open control in one run. This can take a few minutes."
        )
    assert answered.value.ok, answered.value.status


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
    with page.expect_response("**/api/assessments"):
        again.click()
    paper = page.get_by_role("region", name="Working paper")
    steps = paper.get_by_role("list", name="Agent steps").get_by_role("listitem")
    expect(steps.first).to_contain_text(f"Loaded control {control_id}: ")
    expect(steps.last).to_contain_text("Waiting for the compliance lead's review.")


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
    hero.get_by_role("button").first.focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Run a profile pass")).to_be_focused()
