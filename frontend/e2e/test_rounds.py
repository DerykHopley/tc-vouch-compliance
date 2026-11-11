"""Review rounds (#402, spec #350 stories 7 and 8), on a Vouch of their own (rounds_url): closing a
round changes the hero and the comparison, and accepting a control the counts, which other
servers' checks read.

The lead closes the first round from the hero through the in-app ask (#441: no browser dialog
opens; the ask takes focus on Cancel, Escape closes it sending nothing), starts "Q4 2026",
accepts 4.1.2 (its review seeded through the API, as test_report does: the review's own steps
are test_review's), and the overview's comparison names 4.1.2 as changed with the counts'
change; its link opens the working paper; the report's audit trail shows the close and the
start. Then, at 1280px and 400px, light and dark, both states of the round line (open, and none
open with the name field) and the comparison fit: nothing scrolls sideways and every control is
at least 44px. The layout check runs from whatever state the server is in."""

import json
import re

import pytest
from browser_dialogs import dialogs_of, no_dialog
from playwright.sync_api import APIRequestContext, Page, expect
from reviewing import reviewed
from touch_targets import small_targets

CLOSE_FIRST = (
    'Close the review round "First round"? Its results are frozen, and the overview compares '
    "later work with them. The close is recorded in the audit trail."
)
# InlineConfirm's DOUBLE_CLICK_MS (#527): for that long after a pointer confirm, the next click
# anywhere presses nothing, even once the ask has left the page.
DOUBLE_CLICK_MS = 500


def open_dossier(page: Page, url: str) -> None:
    with page.expect_response("**/api/overview"):
        page.goto(url)


def ask(page: Page, dialogs: list[str]) -> str:
    """Close round on the hero: no browser dialog; the in-app ask opens, focus on Cancel; its
    question."""
    page.locator(".hero .round").get_by_role("button", name="Close round").click()
    no_dialog(dialogs)
    question = page.locator(".hero .inline-confirm [role=alert]")
    expect(question).to_be_visible()
    expect(page.get_by_role("button", name="Cancel")).to_be_focused()
    return question.inner_text()


def close_round(page: Page, dialogs: list[str]) -> str:
    """Close round, then Close round in the ask, waiting for the overview read after it: what
    was asked. Focus then moves to the next round's name field."""
    asked = ask(page, dialogs)
    with (
        page.expect_response("**/api/rounds/close") as closed,
        page.expect_response("**/api/overview"),
    ):
        page.locator(".hero .inline-confirm").get_by_role("button", name="Close round").click()
    assert closed.value.ok, closed.value.status
    expect(page.get_by_role("textbox", name="Name of the next round")).to_be_focused()
    page.wait_for_timeout(DOUBLE_CLICK_MS)  # a person names the round after a double-click's time
    return asked


def start_round(page: Page, name: str) -> None:
    """Name the next round and Start round, waiting for the overview read after it."""
    page.get_by_role("textbox", name="Name of the next round").fill(name)
    with (
        page.expect_response(re.compile(r".*/api/rounds$")) as started,
        page.expect_response("**/api/overview"),
    ):
        page.get_by_role("button", name="Start round").click()
    assert started.value.ok, started.value.status


def accepted(request: APIRequestContext, url: str, control_id: str) -> str:
    """The control assessed on the fake model and accepted through the three steps: its
    accepted verdict."""
    response = request.post(f"{url}/api/assessments", data={"control_id": control_id})
    assert response.ok, response.status
    data = re.search(r"event: result\r?\ndata: (.*)", response.text())
    assert data, response.text()
    thread_id = json.loads(data.group(1))["assessment"]["thread_id"]
    return reviewed(request, url, thread_id)["verdict"]


VERDICT_WORDS = {
    "evidenced": "Evidenced",
    "partly-evidenced": "Partly evidenced",
    "not-evidenced": "Not evidenced",
    "not-applicable": "Not applicable",
}


def test_the_lead_closes_the_first_round_starts_the_next_and_sees_what_changed(
    page: Page, rounds_url
):
    dialogs = dialogs_of(page)
    open_dossier(page, rounds_url)
    line = page.locator(".hero .round")
    expect(line).to_contain_text("Review round: First round, open since the start")
    expect(page.get_by_role("region", name="Compared with the last round")).to_contain_text(
        "No closed review round yet."
    )

    # Escape closes the ask, sends nothing, and puts focus back on Close round.
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.url) if "/api/rounds" in r.url else None)
    assert ask(page, dialogs) == CLOSE_FIRST
    page.keyboard.press("Escape")
    expect(page.locator(".inline-confirm")).to_have_count(0)
    expect(line.get_by_role("button", name="Close round")).to_be_focused()
    assert sent == []

    assert close_round(page, dialogs) == CLOSE_FIRST
    expect(line).to_contain_text("No review round open.")
    compared = page.get_by_role("region", name="Compared with First round")
    expect(compared).to_contain_text("Nothing has changed since First round closed.")

    start_round(page, "Q4 2026")
    expect(line).to_contain_text("Review round: Q4 2026, open since")

    verdict = accepted(page.request, rounds_url, "4.1.2")
    open_dossier(page, rounds_url)
    expect(compared).to_contain_text("Accepted: 1 now, 0 when First round closed.")
    expect(compared).to_contain_text(f"{VERDICT_WORDS[verdict]} 1 (+1)")
    changed = compared.get_by_role("link")
    expect(changed).to_have_count(1)
    expect(changed).to_have_text(f"4.1.2: Not accepted → {VERDICT_WORDS[verdict]}")
    changed.click()
    expect(page).to_have_url(f"{rounds_url}/controls/4.1.2")

    with page.expect_response("**/api/report"):
        page.goto(f"{rounds_url}/report")
    trail = page.get_by_role("table", name="Audit trail")
    expect(trail.get_by_text("Review round started: Q4 2026")).to_be_visible()
    expect(trail.get_by_text("Review round closed: First round")).to_be_visible()
    no_dialog(dialogs)


def fits(page: Page, where: str) -> None:
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    ), f"the Dossier scrolls sideways, {where}"
    assert small_targets(page) == [], where


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_round_line_and_the_comparison_fit_in_both_states(
    page: Page, rounds_url, width, scheme
):
    dialogs = dialogs_of(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    open_dossier(page, rounds_url)
    line = page.locator(".hero .round")
    expect(line).to_be_visible()
    fits(page, f"{width}px {scheme}, as it was")
    name = f"Check {width} {scheme}"
    if page.get_by_role("button", name="Close round").count():
        ask(page, dialogs)
        fits(page, f"{width}px {scheme}, the ask open")
        page.get_by_role("button", name="Cancel").click()
        close_round(page, dialogs)
        expect(line).to_contain_text("No review round open.")
        fits(page, f"{width}px {scheme}, none open")
        start_round(page, name)
    else:
        start_round(page, name)
    expect(line).to_contain_text(f"Review round: {name}, open since")
    fits(page, f"{width}px {scheme}, open")


@pytest.mark.parametrize("width", [1280, 400])
def test_the_lead_picks_a_closed_round_on_the_company_page(page: Page, rounds_url, width):
    """#572: the Company page's Rounds section, newest first, the default marked; choosing a
    round goes to its comparison's address. Runs after the tests above, so a round is closed;
    with none (run alone), one is closed through the API first."""
    listed = page.request.get(f"{rounds_url}/api/rounds").json()
    if listed["default"] is None:
        assert page.request.post(f"{rounds_url}/api/rounds/close").ok
        listed = page.request.get(f"{rounds_url}/api/rounds").json()
    chosen = listed["default"]
    page.set_viewport_size({"width": width, "height": 900})
    with page.expect_response(re.compile(r".*/api/rounds$")):
        page.goto(f"{rounds_url}/company")
    section = page.get_by_role("region", name="Rounds", exact=True)
    names = section.locator(".rounds-name")
    expect(names).to_have_count(len(listed["rounds"]))
    assert names.all_inner_texts() == [r["name"] for r in reversed(listed["rounds"])]
    marked = section.get_by_role("listitem").filter(has_text="Compared by default")
    expect(marked).to_have_count(1)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    ), f"the Company page scrolls sideways at {width}px"
    assert small_targets(page) == [], width
    marked.get_by_role("link").click()
    expect(page).to_have_url(f"{rounds_url}/company/rounds/{chosen}")


def compared_after_a_change(request: APIRequestContext, url: str, name: str) -> tuple[int, str]:
    """A round closed (the open one, if any) and the next started, then a control not accepted
    when it closed accepted: the closed round's number, and the control now changed (#573)."""
    listed = request.get(f"{url}/api/rounds").json()["rounds"]
    if listed[-1]["closed_at"] is None:
        assert request.post(f"{url}/api/rounds/close").ok
    assert request.post(f"{url}/api/rounds", data={"name": name}).ok
    number = request.get(f"{url}/api/rounds").json()["default"]
    comparison = request.get(f"{url}/api/rounds/{number}/comparison").json()
    control_id = next(c["control_id"] for c in comparison["controls"] if c["after"] is None)
    accepted(request, url, control_id)
    return number, control_id


def test_the_lead_opens_a_control_from_the_round_comparison_and_comes_back_to_the_same_place(
    page: Page, rounds_url
):
    """#573, the user story: the round comparison's summary and chapters; a control opened from
    it, and the breadcrumb back to it, with the folds and the scroll as they were. Then at 400px
    each control is a card, nothing scrolls sideways and every control is at least 44px."""
    number, control_id = compared_after_a_change(page.request, rounds_url, "Comparison check")
    page.set_viewport_size({"width": 1280, "height": 600})
    with page.expect_response(f"**/api/rounds/{number}/comparison"):
        page.goto(f"{rounds_url}/company/rounds/{number}")
    tab = page.get_by_role("navigation", name="Main").get_by_role("link", name="Company")
    expect(tab).to_have_attribute("aria-current", "page")
    summary = page.get_by_role("region", name="Summary")
    expect(summary.get_by_role("list", name="Changes")).to_contain_text("New 1")
    folds = page.locator(".compare-chapter details")
    expect(folds.first).to_be_visible()
    folds.first.locator("summary").click()
    expect(folds.first).to_have_attribute("open", "")
    row = page.get_by_role("row", name=re.compile(rf"^{re.escape(control_id)} "))
    link = row.get_by_role("link")
    link.scroll_into_view_if_needed()
    scrolled = page.evaluate("window.scrollY")
    assert scrolled > 0, "the check needs the comparison scrolled"
    link.click()
    expect(page).to_have_url(f"{rounds_url}/controls/{control_id}")

    crumbs = page.get_by_role("navigation", name="Breadcrumb")
    with page.expect_response(f"**/api/rounds/{number}/comparison"):
        crumbs.get_by_role("link", name=re.compile("^Compared with ")).click()
    expect(page).to_have_url(f"{rounds_url}/company/rounds/{number}")
    expect(folds.first).to_have_attribute("open", "")
    expect(folds.nth(1)).not_to_have_attribute("open", "")
    page.wait_for_function(f"Math.abs(window.scrollY - {scrolled}) < 2")

    page.set_viewport_size({"width": 400, "height": 900})
    card = page.locator(".compare-controls tbody tr").first
    assert card.evaluate("r => getComputedStyle(r).display") == "block"
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    ), "the round comparison scrolls sideways at 400px"
    assert small_targets(page) == []
