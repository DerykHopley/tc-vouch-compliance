"""A control's working paper (#41): requirement by requirement for an assessed control, the
requirements and Assess for one not yet assessed, not-found for an unknown control, the verdict
kept over a reload, and the index stacked or beside the paper by width.

On its own server (paper_url): local mode has one user and 60 requests a minute, and the main
server's checks already spend most of that."""

import re

import pytest
from playwright.sync_api import APIRequestContext, Locator, Page, expect

ASSESSED = "4.1.2"  # the demo script's control: five requirements, each with a quote
UNASSESSED = "5.1.2"  # assessed by one check only; Nordlicht marks its S3 not applicable


def assessed(request: APIRequestContext, paper_url: str, control_id: str) -> None:
    """Assesses a control on the server's fake model, up to the review."""
    response = request.post(f"{paper_url}/api/assessments", data={"control_id": control_id})
    assert response.ok, response.status
    assert "event: result" in response.text()


def open_paper(page: Page, paper_url: str, control_id: str, *, assessed: bool) -> Locator:
    """The control's paper, once what it lists is read: the overview (the requirements before
    an assessment), and for an assessed control its newest stored assessment. A control the
    overview says is not assessed reads none."""
    if assessed:
        with (
            page.expect_response("**/api/overview"),
            page.expect_response(re.compile(r"/api/assessments/[^/]+$")),
        ):
            page.goto(f"{paper_url}/controls/{control_id}")
    else:
        with page.expect_response("**/api/overview"):
            page.goto(f"{paper_url}/controls/{control_id}")
    return page.get_by_role("region", name="Working paper")


def row(paper: Locator, requirement_id: str) -> Locator:
    return paper.locator("tr").filter(
        has=paper.page.locator("th[scope=row] .mono").get_by_text(requirement_id, exact=True)
    )


def box(locator: Locator) -> dict:
    """The element's bounding box, which it must have."""
    found = locator.bounding_box()
    assert found, locator
    return found


def test_an_assessed_controls_rows_show_verdict_kind_stale_flag_and_quotes(page: Page, paper_url):
    assessed(page.request, paper_url, ASSESSED)

    paper = open_paper(page, paper_url, ASSESSED, assessed=True)

    expect(paper.get_by_text("Protection level: normal")).to_be_visible()
    expect(paper.get_by_text("Answer language: English")).to_be_visible()
    rows = paper.get_by_role("table", name="Requirements").locator("tbody tr")
    expect(rows).to_have_count(5)
    for requirement_id in ("M1", "M2", "S1", "S2", "S3"):
        shown = row(paper, requirement_id)
        expect(shown.locator(".chip")).to_have_text(re.compile(r"^(Partly e|E)videnced$"))
        expect(shown.locator(".kind").first).to_have_text(re.compile(r"^(documented|implemented)$"))
        expect(shown.get_by_text(re.compile(r"^(Current|Stale)$"))).to_be_visible()
        expect(shown.locator("blockquote").first).to_be_visible()
    expect(row(paper, "M1").locator("blockquote")).to_have_text(
        "We decide how people sign in to a system by looking at what could go wrong with it."
    )
    expect(row(paper, "M1").get_by_text("Must", exact=True)).to_be_visible()
    # M1 partly, M2 evidenced: the control is partly evidenced, and the paper says why (#212).
    expect(paper.locator(".paper-head .roll-up")).to_have_text(
        "Partly evidenced: 1 of 2 must requirements is fully evidenced."
    )


def test_an_unassessed_control_lists_its_requirements_and_assess_streams_steps(
    page: Page, paper_url
):
    paper = open_paper(page, paper_url, UNASSESSED, assessed=False)

    table = paper.get_by_role("table", name="Requirements")
    expect(row(paper, "M1")).to_be_visible()
    expect(row(paper, "S3")).to_contain_text("Not applicable: Nordlicht allows no remote access")
    expect(table.locator(".chip")).to_have_count(0)  # no verdicts before an assessment

    paper.get_by_role("button", name=f"Assess {UNASSESSED}", exact=True).click()

    steps = paper.get_by_role("list", name="Agent steps").get_by_role("listitem")
    expect(steps.first).to_contain_text(f"Loaded control {UNASSESSED}: ")
    expect(paper.get_by_role("region", name="Verdict and draft answer")).to_be_visible()
    expect(row(paper, "S3").locator(".chip")).to_have_text("Not applicable")
    expect(row(paper, "S3")).to_contain_text("Nordlicht allows no remote access")
    index = page.get_by_role("navigation", name="Controls")
    expect(
        index.get_by_role("link", name=re.compile(rf"^{re.escape(UNASSESSED)}"))
    ).to_contain_text("Proposed")


def test_an_unknown_controls_link_says_not_found_with_a_way_back(page: Page, paper_url):
    with page.expect_response("**/api/overview"):
        page.goto(f"{paper_url}/controls/9.9.9")

    not_found = page.get_by_role("region", name="Control not found")
    expect(not_found.get_by_role("heading")).to_have_text("No control 9.9.9")
    with page.expect_response("**/api/overview"):
        not_found.get_by_role("link", name="← Overview").click()
    expect(page).to_have_url(f"{paper_url}/")
    expect(page.get_by_role("article", name="Control ")).to_have_count(12)


def test_a_finished_assessment_is_still_shown_after_a_reload(page: Page, paper_url):
    paper = open_paper(page, paper_url, "1.1.1", assessed=False)
    paper.get_by_role("button", name="Assess 1.1.1", exact=True).click()
    result = paper.get_by_role("region", name="Verdict and draft answer")
    expect(result).to_be_visible()
    verdict = result.locator(".chip").inner_text()
    answer = result.locator(".answer").inner_text()
    assert answer

    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):  # read from the database
        page.reload()

    expect(result.locator(".chip")).to_have_text(verdict)
    expect(result.locator(".answer")).to_have_text(answer)
    expect(paper.get_by_text("Proposed", exact=True)).to_be_visible()
    expect(row(paper, "M1").locator(".chip")).to_be_visible()


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_the_index_is_beside_the_paper_wide_stacks_above_it_below_1000px_and_400px_fits(
    page: Page, paper_url
):
    assessed(page.request, paper_url, ASSESSED)  # the wide table of an assessed control
    page.emulate_media(color_scheme="light")
    for width, beside in ((1400, True), (900, False), (400, False)):
        page.set_viewport_size({"width": width, "height": 900})
        paper = open_paper(page, paper_url, ASSESSED, assessed=True)
        expect(row(paper, "M1")).to_be_visible()
        index = page.get_by_role("navigation", name="Controls").bounding_box()
        shown = paper.bounding_box()
        assert index and shown
        if beside:
            assert index["x"] + index["width"] <= shown["x"], f"at {width}px the index isn't beside"
        else:
            assert index["y"] + index["height"] <= shown["y"], f"at {width}px the index isn't above"
        assert not scrolls_sideways(page), f"the working paper scrolls sideways at {width}px"

    page.emulate_media(color_scheme="dark")  # still 400px
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)
    expect(row(paper, "M1")).to_be_visible()
    expect(page.locator("body")).to_have_css("background-color", "rgb(13, 13, 14)")  # dark --paper
    assert not scrolls_sideways(page), "the working paper scrolls sideways at 400px in dark"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_on_a_phone_each_requirement_is_a_card_in_order(page: Page, paper_url, scheme):
    """#211 (the owner's option A): at 400px each row is a card: id, column and verdict on one
    line; the text; the evidence kinds and stale flag; the reason and quotes."""
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 400, "height": 900})
    page.emulate_media(color_scheme=scheme)
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)

    assert not scrolls_sideways(page)
    for requirement_id in ("M1", "M2", "S1", "S2", "S3"):
        card = row(paper, requirement_id)
        head = box(card.locator("th[scope=row]"))
        verdict = box(card.locator(".verdict-cell .chip"))
        text = box(card.locator(".rq-text"))
        evidence = box(card.locator(".evidence"))
        reason = box(card.locator(".reason"))
        # One line: the id and the chip share a band, the chip to the right.
        assert (
            verdict["y"] < head["y"] + head["height"]
            and head["y"] < verdict["y"] + verdict["height"]
        )
        assert verdict["x"] > head["x"]
        assert head["y"] + head["height"] <= text["y"] + 1
        assert text["y"] + text["height"] <= evidence["y"] + 1
        assert evidence["y"] + evidence["height"] <= reason["y"] + 1
        assert box(card)["width"] <= 400


def test_on_a_phone_show_full_requirement_works_from_the_keyboard(page: Page, paper_url):
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 400, "height": 900})
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)
    card = row(paper, "M1")
    toggle = card.locator(".rq-toggle")
    expect(toggle).to_have_text("Show full requirement")
    text = card.locator(".rq-text")
    clamped = box(text)["height"]

    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(toggle).to_have_text("Show less")
    assert box(text)["height"] > clamped  # M1's text runs past 2 lines at this width
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    assert box(text)["height"] == clamped


def test_on_a_desktop_the_requirements_stay_a_table_with_no_toggle(page: Page, paper_url):
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 1280, "height": 900})
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)

    expect(paper.get_by_role("columnheader", name="Requirement", exact=True)).to_be_visible()
    expect(paper.get_by_role("button", name="Show full requirement")).to_have_count(0)
