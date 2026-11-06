"""The Dossier overview from the API (#40): readiness from accepted verdicts only, the 12 control
cards with their state, the empty state, the API failing, and the page at 400px in both themes."""

import json

from dossier_cards import card, click_on_card
from playwright.sync_api import APIRequestContext, Page, expect

CONTROLS = 12


def assessed(request: APIRequestContext, dossier_url: str, control_id: str) -> str:
    """Assesses a control on the server's fake model, up to the review; its thread id."""
    response = request.post(f"{dossier_url}/api/assessments", data={"control_id": control_id})
    assert response.ok, response.status
    lines = [line.strip() for line in response.text().splitlines()]
    data = lines[lines.index("event: result") + 1].removeprefix("data:").strip()
    return json.loads(data)["assessment"]["thread_id"]


def reviewed(request: APIRequestContext, dossier_url: str, thread_id: str, decision: str) -> None:
    response = request.post(
        f"{dossier_url}/api/assessments/{thread_id}/review", data={"decision": decision}
    )
    assert response.ok, response.status


def open_dossier(page: Page, dossier_url: str) -> None:
    with page.expect_response("**/api/overview"):
        page.goto(dossier_url)


def tile(page: Page, label: str):
    """The figure on the readiness band's tile with this label."""
    band = page.get_by_role("region", name="Readiness")
    return band.locator(".tile").filter(has=page.get_by_text(label, exact=True)).locator("b")


def test_the_band_counts_accepted_verdicts_only_and_each_card_shows_its_state(
    page: Page, dossier_url
):
    # Seeded on the server's fake model: one control accepted, one rejected, one proposed.
    reviewed(page.request, dossier_url, assessed(page.request, dossier_url, "4.1.2"), "accept")
    reviewed(page.request, dossier_url, assessed(page.request, dossier_url, "1.1.1"), "reject")
    assessed(page.request, dossier_url, "5.2.9")

    open_dossier(page, dossier_url)

    expect(tile(page, f"accepted of {CONTROLS}")).to_have_text("1")
    expect(tile(page, "partly evidenced")).to_have_text("1")  # the proposed and rejected aren't counted
    expect(tile(page, "evidenced")).to_have_text("0")
    expect(tile(page, "not evidenced")).to_have_text("0")
    expect(tile(page, "open")).to_have_text(str(CONTROLS - 1))
    expect(page.get_by_role("article", name="Control ")).to_have_count(CONTROLS)
    expect(card(page, "4.1.2")).to_contain_text("Accepted")
    expect(card(page, "4.1.2")).to_contain_text("Partly evidenced")
    expect(card(page, "1.1.1")).to_contain_text("Rejected")
    expect(card(page, "5.2.9")).to_contain_text("Proposed")
    expect(card(page, "5.2.5")).to_contain_text("Not assessed")
    hero = page.get_by_role("heading", level=1).locator("..")
    expect(hero).to_contain_text("1 of 12 controls accepted.")
    expect(hero.get_by_role("button", name="Review 5.2.9")).to_be_visible()
    quotes = page.get_by_role("region", name="What the evidence says")
    expect(quotes.get_by_role("article").first).to_be_visible()


def test_a_card_opens_its_working_paper(page: Page, dossier_url):
    open_dossier(page, dossier_url)

    card(page, "5.2.4").get_by_role("link", name="Open 5.2.4").click()

    expect(page).to_have_url(f"{dossier_url}/controls/5.2.4")
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("button", name="Assess 5.2.4")).to_be_visible()


def test_a_click_anywhere_on_a_card_opens_its_working_paper(page: Page, dossier_url):
    """#231: the card's one link covers the whole card: its question, ISO label and verdict chip."""
    for control_id, part in (("5.2.4", ".control-title"), ("4.1.3", ".card-meta .mono"), ("2.1.3", ".chip")):
        open_dossier(page, dossier_url)
        click_on_card(page, control_id, part)
        expect(page).to_have_url(f"{dossier_url}/controls/{control_id}")
        expect(page.get_by_role("region", name="Working paper")).to_be_visible()


def test_a_ctrl_click_on_a_card_opens_its_paper_in_a_new_tab_and_the_dossier_stays(
    page: Page, dossier_url
):
    """#231: the stretched link leaves a modified click to the browser."""
    open_dossier(page, dossier_url)
    with page.context.expect_page() as opened:  # the browser's new tab
        click_on_card(page, "5.2.4", modifiers=["Control"])
    expect(opened.value).to_have_url(f"{dossier_url}/controls/5.2.4")
    expect(page).to_have_url(f"{dossier_url}/")
    expect(page.get_by_role("region", name="Working paper")).to_have_count(0)
    opened.value.close()


def test_with_nothing_assessed_the_empty_state_names_the_first_step(
    page: Page, dossier_url, failing_api_url
):
    # The failing API never finishes an assessment, so its profile has nothing assessed.
    page.route(
        "**/api/overview",
        lambda route: route.fulfill(response=route.fetch(url=f"{failing_api_url}/api/overview")),
    )

    open_dossier(page, dossier_url)

    hero = page.get_by_role("heading", level=1).locator("..")
    expect(hero).to_contain_text("Nothing is assessed yet. Start with 1.1.1")
    expect(hero.get_by_role("button", name="Assess 1.1.1")).to_be_visible()
    expect(page.get_by_role("article", name="Control ")).to_have_count(CONTROLS)
    expect(page.get_by_text("Not assessed", exact=True)).to_have_count(CONTROLS)
    quotes = page.get_by_role("region", name="What the evidence says")
    expect(quotes).to_contain_text("No evidence on file yet")
    expect(quotes.get_by_role("article")).to_have_count(0)


def test_the_api_failing_shows_a_formal_message_and_retry_works(page: Page, dossier_url):
    page.route("**/api/overview", lambda route: route.fulfill(status=503, json={"detail": "down"}))

    open_dossier(page, dossier_url)

    expect(page.get_by_role("alert")).to_have_text(
        "The overview could not be loaded just now. Please try again."
    )
    page.unroute("**/api/overview")
    with page.expect_response("**/api/overview"):
        page.get_by_role("button", name="Retry").click()
    expect(page.get_by_role("article", name="Control ")).to_have_count(CONTROLS)
    expect(page.get_by_role("alert")).to_have_count(0)


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_at_400px_nothing_scrolls_sideways_in_light_or_dark(page: Page, dossier_url):
    page.set_viewport_size({"width": 400, "height": 900})
    for scheme, paper in (("light", "rgb(232, 231, 227)"), ("dark", "rgb(13, 13, 14)")):
        page.emulate_media(color_scheme=scheme)
        open_dossier(page, dossier_url)
        expect(page.get_by_role("article", name="Control ")).to_have_count(CONTROLS)
        expect(page.locator("body")).to_have_css("background-color", paper)  # --paper, tokens.css
        assert not scrolls_sideways(page), f"the Dossier scrolls sideways at 400px in {scheme}"
