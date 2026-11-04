"""A compliance lead assesses a control from its card and watches the steps stream in (#12)."""

import re

from playwright.sync_api import Page, expect

CONTROL = "4.1.2"


def start_assessment(page: Page, vouch_url: str) -> None:
    page.goto(vouch_url)
    card = page.get_by_role("article").filter(has_text=CONTROL)
    card.get_by_role("button", name=f"Assess {CONTROL}").click()


def test_assess_shows_the_steps_one_by_one_then_the_verdict_and_the_answer(page: Page, vouch_url):
    seen: list[int] = []  # the number of steps on screen each time it changes
    page.expose_function("stepCount", lambda n: seen.append(n))
    page.add_init_script(
        """new MutationObserver(() => {
             const n = document.querySelectorAll('[aria-label="Agent steps"] li').length
             if (n) window.stepCount(n)
           }).observe(document, {childList: true, subtree: true})"""
    )

    start_assessment(page, vouch_url)

    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("heading", name=CONTROL)).to_be_visible()
    steps = paper.get_by_role("list", name="Agent steps").get_by_role("listitem")
    result = paper.get_by_role("region", name="Verdict and draft answer")
    expect(result.get_by_text("Partly evidenced", exact=True)).to_be_visible()
    expect(result).to_contain_text("hardware security keys")
    expect(result.get_by_role("region", name="Usage")).to_have_text(  # #35: the fake's tokens, priced
        re.compile(r"^[\d,]+ tokens in · [\d,]+ tokens out · \$[\d.]+ \(estimated\)$")
    )
    for text in (
        "No country overlay adds a requirement to this control",
        "authentication",
        "Judged 4 of 5 requirements evidenced.",
        "Checked each quote against its document: kept 6 citations.",
    ):
        expect(steps.filter(has_text=text).first).to_be_visible()
    expect(steps.first).to_contain_text(f"Loaded control {CONTROL}: ")
    expect(steps.first).to_contain_text("5 requirements apply")
    expect(steps.last).to_contain_text("Waiting for the compliance lead's review.")
    shown = steps.count()
    assert seen[0] == 1, f"the first render already held {seen[0]} steps"
    assert sorted(set(seen)) == list(range(1, shown + 1)), seen


def test_api_down_shows_the_message_and_retry_works_once_it_is_back(page: Page, vouch_url):
    page.route("**/api/assessments", lambda route: route.abort("connectionrefused"))

    start_assessment(page, vouch_url)

    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("alert")).to_have_text(
        "Vouch could not reach its service. Try again shortly."
    )
    page.unroute("**/api/assessments")
    paper.get_by_role("button", name=f"Retry {CONTROL}", exact=True).click()
    expect(paper.get_by_role("region", name="Verdict and draft answer").get_by_text("Partly evidenced", exact=True)).to_be_visible()
    expect(paper.get_by_role("alert")).to_have_count(0)


def test_an_error_event_shows_its_message_in_place_of_the_result(
    page: Page, vouch_url, failing_api_url
):
    page.route(
        "**/api/assessments",
        lambda route: route.fulfill(response=route.fetch(url=f"{failing_api_url}/api/assessments")),
    )

    start_assessment(page, vouch_url)

    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("alert")).to_have_text(
        "The assessment could not be completed. Please try again later."
    )
    expect(paper.get_by_role("region", name="Verdict and draft answer")).to_have_count(0)


def test_the_button_is_busy_while_running_and_a_second_click_starts_nothing(page: Page, vouch_url):
    held = []  # the assessment's request, held open until the test lets it go
    page.route("**/api/assessments", lambda route: held.append(route))
    posts = []
    page.on("request", lambda r: posts.append(r) if r.url.endswith("/api/assessments") else None)
    page.goto(vouch_url)
    button = page.get_by_role("button", name=f"Assess {CONTROL}")

    button.evaluate("b => { b.click(); b.click() }")  # two clicks before React re-renders
    paper = page.get_by_role("region", name="Working paper")
    busy = paper.get_by_role("button", name=f"Assessing {CONTROL}", exact=True)
    expect(busy).to_be_disabled()
    expect(busy).to_have_attribute("aria-busy", "true")
    busy.click(force=True)  # a disabled button: the click must start nothing
    page.wait_for_timeout(300)  # allowed: give a wrongly started request time to show

    assert len(posts) == 1
    held[0].continue_()
    expect(paper.get_by_role("region", name="Verdict and draft answer")).to_be_visible()
    assert len(posts) == 1


def test_with_reduced_motion_steps_appear_without_animation(page: Page, vouch_url, browser):
    def step_animation(p: Page) -> str:
        start_assessment(p, vouch_url)
        step = p.get_by_role("list", name="Agent steps").get_by_role("listitem").first
        expect(step).to_be_visible()
        return step.evaluate("li => getComputedStyle(li).animationName")

    assert step_animation(page) != "none"  # the motion exists, so turning it off is a real check

    reduced = browser.new_context(reduced_motion="reduce")
    try:
        assert step_animation(reduced.new_page()) == "none"
    finally:
        reduced.close()


def test_the_working_paper_survives_a_refresh_and_opens_from_its_address(page: Page, vouch_url):
    start_assessment(page, vouch_url)
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("region", name="Verdict and draft answer")).to_be_visible()
    expect(page).to_have_url(f"{vouch_url}/controls/{CONTROL}")

    page.reload()
    expect(paper.get_by_role("heading", name=CONTROL)).to_be_visible()

    page.goto(f"{vouch_url}/controls/{CONTROL}")
    expect(paper.get_by_role("button", name=f"Assess {CONTROL}", exact=True)).to_be_enabled()
