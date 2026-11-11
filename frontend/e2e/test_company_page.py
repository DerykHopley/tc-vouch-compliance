"""The Company page (#505, spec #448 story 4): under Nordlicht the lead sees the company's name,
its five facts, the read-only note, the scope, the not-applicable entry with its reason, the
documents table and the open review round; under Alpenwerk (made current, Nordlicht put back after)
the country overlay in the facts and the scope. The layout: the Documents left of the panels and
wider from 700px, the panels first (the Goal leading) on a phone, the full width with side gutters,
nothing scrolling sideways and every control at least 44px, light and dark, the Goal's ask open
too. Local mode, the scripted fake model."""

import pytest
from current_company import make_current
from playwright.sync_api import Page, expect
from touch_targets import small_targets

COMPANY = "**/api/company"
DOCUMENTS = "**/api/documents"

# Boxes read once the fonts have loaded and layout has settled (two frames), as #512 found a late
# font moving them between reads.
BOXES = """async (selectors) => {
  await document.fonts.ready
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
  return selectors.map((s) => {
    const r = document.querySelector(s).getBoundingClientRect()
    return { left: r.left, top: r.top, right: r.right, width: r.width }
  })
}"""


def open_company(page: Page, url: str) -> None:
    """/company, once its facts, the overview and the documents have been read."""
    with (
        page.expect_response(COMPANY) as facts,
        page.expect_response("**/api/overview"),
        page.expect_response(DOCUMENTS),
    ):
        page.goto(f"{url}/company")
    assert facts.value.ok, facts.value.status
    expect(page.locator("article.documents-doc")).to_be_visible()


def panel(page: Page, name: str):
    return page.get_by_role("region", name=name, exact=True)


def boxes(page: Page, *selectors: str) -> list[dict[str, float]]:
    return page.evaluate(BOXES, list(selectors))


def test_story_4_the_lead_sees_the_company_its_scope_and_documents(
    page: Page, vouch_url, nordlicht_later
):
    page.set_viewport_size({"width": 1280, "height": 900})
    open_company(page, vouch_url)

    expect(page.get_by_role("heading", level=1)).to_have_text("Nordlicht Komponenten GmbH")
    facts = page.locator("dl.company-facts")
    expect(facts.locator("dt")).to_have_text(
        ["Country", "Answer language", "Base framework", "Protection level", "Overlays"]
    )
    expect(facts.locator("dd")).to_have_text(
        ["Germany", "English", "VDA ISA 2027.1", "Normal", "None"]
    )
    expect(page.get_by_text("This company is defined in files:")).to_be_visible()
    expect(panel(page, "Scope")).to_contain_text(
        "Normal protection brings in 85 requirements of VDA ISA 2027.1, in 12 controls."
    )
    expect(panel(page, "Scope")).to_contain_text("No country overlay applies.")
    not_applicable = panel(page, "Not applicable")
    expect(not_applicable.get_by_text("5.1.2 S3", exact=True)).to_be_visible()
    expect(not_applicable).to_contain_text("Nordlicht allows no remote access")
    expect(page.get_by_role("table", name="Policies")).to_be_visible()
    rounds = panel(page, "Review rounds")
    expect(rounds).to_contain_text("Review round:")
    expect(rounds.get_by_role("button", name="Close round")).to_be_visible()

    nordlicht_later(vouch_url)
    make_current(page.request, vouch_url, "alpenwerk")
    open_company(page, vouch_url)
    expect(page.get_by_role("heading", level=1)).to_have_text("Alpenwerk Präzision GmbH")
    overlay = facts.locator("dd").nth(4).get_by_text("AT NIS2 (illustrativ)")
    expect(overlay).to_have_attribute("lang", "de")
    expect(panel(page, "Scope")).to_contain_text("AT NIS2 (illustrativ) adds 1 requirement.")


def test_the_documents_sit_left_and_wider_on_a_desktop_and_after_the_panels_on_a_phone(
    page: Page, vouch_url
):
    page.set_viewport_size({"width": 1280, "height": 900})
    open_company(page, vouch_url)
    documents, goal = boxes(page, ".company-documents", ".company-panel")
    assert documents["right"] <= goal["left"], (documents, goal)
    assert documents["width"] > goal["width"], (documents, goal)

    page.set_viewport_size({"width": 400, "height": 900})
    documents, goal = boxes(page, ".company-documents", ".company-panel")
    assert goal["top"] < documents["top"], (documents, goal)
    expect(page.locator(".company-panel").first.get_by_role("heading")).to_have_text("Goal")


def test_at_1440px_the_page_uses_more_than_1000px(page: Page, vouch_url):
    page.set_viewport_size({"width": 1440, "height": 900})
    open_company(page, vouch_url)
    [grid] = boxes(page, ".company-grid")
    assert grid["width"] > 1000, grid


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_nothing_scrolls_sideways_and_every_control_is_44px(page: Page, vouch_url, width, scheme):
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    open_company(page, vouch_url)

    def fits(where: str) -> None:
        boxes(page, "main")  # the fonts loaded and layout settled
        assert not page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        ), f"the Company page scrolls sideways, {where}"
        assert small_targets(page) == [], where

    fits(scheme)
    panel(page, "Goal").get_by_role("button", name="Change goal").click()
    expect(panel(page, "Goal").locator(".inline-confirm")).to_be_visible()
    fits(f"{scheme}, the Goal's ask open")
    page.get_by_role("button", name="Cancel").click()
