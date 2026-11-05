"""The lead's review on the working paper (#42): override a requirement with a reason and see the
roll-up change, accept or reject with a note, assess a rejected control again with the note, and
a review another tab already made shown as such.

On the working paper's server (paper_url). Each check assesses 4.1.2 afresh (the demo script:
M1 partly evidenced, M2 evidenced, so the control is partly evidenced) and reviews that newest one,
read from storage as the paper opens it, so each starts from a proposed assessment."""

import json
import re

from playwright.sync_api import APIRequestContext, Locator, Page, expect

CONTROL = "4.1.2"
REASON = "The 2025 MFA rollout record shows the risk-based decision."


def assessed(request: APIRequestContext, paper_url: str) -> dict:
    """Assesses the control on the server's fake model, up to the review: the `result` event's data."""
    response = request.post(f"{paper_url}/api/assessments", data={"control_id": CONTROL})
    assert response.ok, response.status
    data = re.search(r"event: result\r?\ndata: (.*)", response.text())
    assert data, response.text()
    return json.loads(data.group(1))


def open_paper(page: Page, paper_url: str) -> Locator:
    """The control's paper, once its newest stored assessment is read."""
    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        page.goto(f"{paper_url}/controls/{CONTROL}")
    return page.get_by_role("region", name="Working paper")


def row(paper: Locator, requirement_id: str) -> Locator:
    return paper.locator("tbody tr").filter(
        has=paper.page.locator("th[scope=row] .mono").get_by_text(requirement_id, exact=True)
    )


def head_verdict(paper: Locator) -> Locator:
    return paper.locator(".paper-head .paper-meta").filter(has_text="Verdict").locator(".chip")


def change(paper: Locator, requirement_id: str, verdict: str, reason: str) -> Locator:
    """Opens the requirement's override and fills it in; the editor, not yet saved."""
    paper.get_by_role("button", name=f"Change {requirement_id}", exact=True).click()
    editor = paper.get_by_role("form", name=f"Change {requirement_id}")
    editor.get_by_label("Verdict").select_option(label=verdict)
    editor.get_by_label("Reason (required)").fill(reason)
    return editor


def test_overriding_m1_to_evidenced_with_a_reason_changes_the_roll_up_on_screen(
    page: Page, paper_url
):
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)
    expect(head_verdict(paper)).to_have_text("Partly evidenced")

    change(paper, "M1", "Evidenced", REASON).get_by_role("button", name="Save").click()

    expect(row(paper, "M1").locator(".chip")).to_have_text("Evidenced")
    expect(row(paper, "M1")).to_contain_text("Changed from Partly evidenced")
    expect(row(paper, "M1")).to_contain_text(REASON)
    expect(head_verdict(paper)).to_have_text("Evidenced")


def test_an_override_without_a_reason_cannot_be_saved(page: Page, paper_url):
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)

    editor = change(paper, "M1", "Evidenced", "")
    save = editor.get_by_role("button", name="Save")
    expect(save).to_be_disabled()
    expect(editor.get_by_text("Give a reason to save the change.")).to_be_visible()
    editor.get_by_label("Reason (required)").fill("   ")
    expect(save).to_be_disabled()
    expect(head_verdict(paper)).to_have_text("Partly evidenced")


def accepted_tile(page: Page) -> Locator:
    return page.locator(".tile").filter(has_text=re.compile(r"accepted of \d+")).locator("b")


def test_accept_records_the_decision_and_the_overviews_counts_change(page: Page, paper_url):
    with page.expect_response("**/api/overview"):
        page.goto(paper_url)
    before = int(accepted_tile(page).inner_text())
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)

    bar = paper.get_by_role("region", name="Review")
    bar.get_by_label("Your note (kept in the audit trail)").fill("Checked against the records.")
    with page.expect_response(re.compile(r"/review$")) as answer:
        bar.get_by_role("button", name="Accept verdict").click()
    assert answer.value.ok, answer.value.status
    expect(bar.get_by_text("Accepted", exact=True)).to_be_visible()
    # Who and when, from the audit trail: local mode's reviewer name (#154).
    expect(bar).to_contain_text(re.compile(r"by Local user on \d+ \w+ \d{4}, \d\d:\d\d"))
    expect(bar.get_by_role("button", name="Accept verdict")).to_have_count(0)

    trail = page.request.get(f"{paper_url}/api/audit-trail?limit=1").json()["entries"]
    assert (trail[0]["action"], trail[0]["note"]) == ("accept", "Checked against the records.")

    with page.expect_response("**/api/overview"):
        page.get_by_role("link", name="← Overview").click()
    expect(accepted_tile(page)).to_have_text(str(before + 1))


def test_reject_then_assess_again_sends_the_note_into_the_new_run(page: Page, paper_url):
    first = assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)
    note = "The exposure assessment is in the records folder: " + "look at it closely. " * 20

    bar = paper.get_by_role("region", name="Review")
    bar.get_by_label("Your note (kept in the audit trail)").fill(note)
    with page.expect_response(re.compile(r"/review$")):
        bar.get_by_role("button", name="Reject verdict").click()
    expect(bar.get_by_text("Rejected", exact=True)).to_be_visible()
    expect(bar).to_contain_text(note.strip())

    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/api/assessments")
    ) as sent:
        bar.get_by_role("button", name=f"Assess again {CONTROL}", exact=True).click()
    assert sent.value.post_data_json == {"control_id": CONTROL, "note": note}

    # The fake model counts the tokens it is sent: the note in its prompts makes the new run's more.
    usage = paper.get_by_role("region", name="Usage")
    expect(usage).to_be_visible()
    tokens_in = int(re.match(r"^([\d,]+) tokens in", usage.inner_text()).group(1).replace(",", ""))
    assert tokens_in > first["usage"]["input_tokens"], (tokens_in, first["usage"])
    expect(paper.get_by_text("Proposed", exact=True)).to_be_visible()


def test_a_review_another_page_made_first_shows_already_reviewed_with_refresh(
    page: Page, paper_url
):
    assessed(page.request, paper_url)
    other = page.context.new_page()
    mine = open_paper(page, paper_url)
    theirs = open_paper(other, paper_url)

    with other.expect_response(re.compile(r"/review$")):
        theirs.get_by_role("region", name="Review").get_by_role(
            "button", name="Accept verdict"
        ).click()

    bar = mine.get_by_role("region", name="Review")
    with page.expect_response(re.compile(r"/review$")) as answer:
        bar.get_by_role("button", name="Reject verdict").click()
    assert answer.value.status == 409
    expect(bar.get_by_role("alert")).to_contain_text("already been reviewed")
    expect(mine.get_by_text("Rejected", exact=True)).to_have_count(0)  # not shown as made

    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        bar.get_by_role("button", name="Refresh").click()
    expect(bar.get_by_text("Accepted", exact=True)).to_be_visible()


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_the_review_bar_and_override_fit_at_400px_in_light_and_dark(page: Page, paper_url):
    assessed(page.request, paper_url)
    page.set_viewport_size({"width": 400, "height": 900})
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        paper = open_paper(page, paper_url)
        change(paper, "M1", "Evidenced", REASON)
        bar = paper.get_by_role("region", name="Review")
        expect(bar.get_by_role("button", name="Accept verdict")).to_be_visible()
        assert not scrolls_sideways(page), f"the review scrolls sideways at 400px in {scheme}"
    # The sign-off colour on the Accept button: champagne in dark (design.md, Tokens: --sign).
    expect(bar.get_by_role("button", name="Accept verdict")).to_have_css(
        "background-color", "rgb(227, 203, 159)"
    )
