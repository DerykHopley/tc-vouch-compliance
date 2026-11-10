"""The lead's review on the working paper (#42), in three steps (#372): override a requirement with
a reason and see the roll-up change, confirm the findings, decide the remediation items, accept or
reject with a note, assess a rejected control again with the note, and a step another tab already
took shown as such.

On the working paper's server (paper_url). Each check assesses 4.1.2 afresh (the demo script:
M1 partly evidenced, M2 evidenced, so the control is partly evidenced) and reviews that newest one,
read from storage as the paper opens it, so each starts from a proposed assessment."""

import json
import re

from playwright.sync_api import APIRequestContext, Dialog, Locator, Page, expect
from reviewing import confirmed
from top_bar import back_to_overview
from touch_targets import small_targets

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


def override(paper: Locator, requirement_id: str, verdict: str, reason: str) -> Locator:
    """Opens the requirement's override and fills it in; the editor, not yet saved."""
    paper.get_by_role("button", name=f"Change {requirement_id}", exact=True).click()
    editor = paper.get_by_role("form", name=f"Override {requirement_id}")
    editor.get_by_label("Verdict").select_option(label=verdict)
    editor.get_by_label("Reason (required)").fill(reason)
    return editor


def test_overriding_m1_to_evidenced_with_a_reason_changes_the_roll_up_on_screen(
    page: Page, paper_url
):
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)
    expect(head_verdict(paper)).to_have_text("Partly evidenced")

    override(paper, "M1", "Evidenced", REASON).get_by_role("button", name="Save").click()

    expect(row(paper, "M1").locator(".chip")).to_have_text("Evidenced")
    expect(row(paper, "M1")).to_contain_text("Overridden from Partly evidenced")
    expect(row(paper, "M1")).to_contain_text(REASON)
    expect(head_verdict(paper)).to_have_text("Evidenced")


def test_an_override_without_a_reason_cannot_be_saved(page: Page, paper_url):
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)

    editor = override(paper, "M1", "Evidenced", "")
    save = editor.get_by_role("button", name="Save")
    expect(save).to_be_disabled()
    expect(editor.get_by_text("Give a reason to save the override.")).to_be_visible()
    editor.get_by_label("Reason (required)").fill("   ")
    expect(save).to_be_disabled()
    expect(head_verdict(paper)).to_have_text("Partly evidenced")


def accepted_tile(page: Page) -> Locator:
    return page.locator(".tile").filter(has_text=re.compile(r"accepted of \d+")).locator("b")


def current_step(bar: Locator) -> Locator:
    return bar.get_by_role("list", name="Review steps").locator('li[aria-current="step"]')


def test_story_2_override_confirm_plan_fixes_and_accept_counts_on_the_overview(
    page: Page, paper_url
):
    with page.expect_response("**/api/overview"):
        page.goto(paper_url)
    before = int(accepted_tile(page).inner_text())
    thread_id = assessed(page.request, paper_url)["assessment"]["thread_id"]
    paper = open_paper(page, paper_url)
    bar = paper.get_by_role("region", name="Review")
    expect(current_step(bar)).to_have_text("Confirm findings")

    # Confirm findings: S1 overridden to not evidenced, so it gets an item beside M1's.
    override(paper, "S1", "Not evidenced", "No per-system list.").get_by_role(
        "button", name="Save"
    ).click()
    expect(bar).to_contain_text("1 override, recorded with the findings.")
    with page.expect_response(re.compile(r"/findings$")) as answer:
        bar.get_by_role("button", name="Confirm findings").click()
    assert answer.value.ok, answer.value.status

    # Plan fixes: the rows can't be changed now, Accept waits for the items.
    expect(current_step(bar)).to_have_text("Plan fixes")
    expect(paper.get_by_role("button", name=re.compile(r"^Change "))).to_have_count(0)
    expect(row(paper, "S1")).to_contain_text("Overridden from Evidenced")
    expect(bar.get_by_role("button", name="Accept verdict")).to_be_disabled()
    expect(bar).to_contain_text("0 of 2 remediation items decided.")
    with page.expect_response(re.compile(r"/remediation/M1$")):
        row(paper, "M1").get_by_role("button", name="Approve M1").click()
    expect(bar).to_contain_text("1 of 2 remediation items decided.")
    with page.expect_response(re.compile(r"/remediation/S1$")):
        row(paper, "S1").get_by_role("button", name="Discard S1").click()

    # Accept.
    expect(current_step(bar)).to_have_text("Accept")
    bar.get_by_label("Your note (kept in the audit trail)").fill("Checked against the records.")
    with page.expect_response(re.compile(r"/review$")) as answer:
        bar.get_by_role("button", name="Accept verdict").click()
    assert answer.value.ok, answer.value.status
    expect(bar.get_by_text("Accepted", exact=True)).to_be_visible()
    # Who and when, kept with the review: local mode's reviewer name (#154).
    expect(bar).to_contain_text(re.compile(r"by Local user on \d+ \w+ \d{4}, \d\d:\d\d"))
    expect(bar.get_by_role("button", name="Accept verdict")).to_have_count(0)

    entries = page.request.get(f"{paper_url}/api/audit-trail?limit=500").json()["entries"]
    mine = [(e["action"], e["requirement_id"]) for e in entries if e["thread_id"] == thread_id]
    assert mine[::-1] == [
        ("override", "S1"),
        ("confirm", None),
        ("approve", "M1"),
        ("discard", "S1"),
        ("accept", None),
    ]
    assert entries[0]["note"] == "Checked against the records."

    with page.expect_response("**/api/overview"):
        back_to_overview(page)
    expect(accepted_tile(page)).to_have_text(str(before + 1))


def test_reject_then_assess_again_carries_the_note_into_the_new_run(page: Page, paper_url):
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
    # The server carries the rejection note itself, however the run was started.
    assert sent.value.post_data_json == {"control_id": CONTROL}

    # The fake model counts the tokens it is sent: the note in its prompts makes the new run's more.
    usage = paper.get_by_role("region", name="Usage")
    expect(usage).to_be_visible()
    tokens_in = int(re.match(r"^([\d,]+) tokens in", usage.inner_text()).group(1).replace(",", ""))
    assert tokens_in > first["usage"]["input_tokens"], (tokens_in, first["usage"])
    expect(paper.get_by_text("Proposed", exact=True)).to_be_visible()


def test_findings_another_page_confirmed_first_show_moved_on_with_refresh(page: Page, paper_url):
    assessed(page.request, paper_url)
    other = page.context.new_page()
    mine = open_paper(page, paper_url)
    theirs = open_paper(other, paper_url)

    with other.expect_response(re.compile(r"/findings$")):
        theirs.get_by_role("region", name="Review").get_by_role(
            "button", name="Confirm findings"
        ).click()

    bar = mine.get_by_role("region", name="Review")
    with page.expect_response(re.compile(r"/findings$")) as answer:
        bar.get_by_role("button", name="Confirm findings").click()
    assert answer.value.status == 409
    expect(bar.get_by_role("alert")).to_contain_text("review has moved on")
    expect(current_step(bar)).to_have_text("Confirm findings")  # not shown as taken

    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):
        bar.get_by_role("button", name="Refresh").click()
    expect(current_step(bar)).to_have_text("Plan fixes")


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


M1_EVIDENCED = {"requirement_id": "M1", "verdict": "evidenced", "reason": REASON}
MAIN = {
    "confirm-findings": "Confirm findings",
    "plan-fixes": "Accept verdict",
    "accept": "Accept verdict",
}
TABBED = {  # in order from the note; at Plan fixes Accept verdict waits, disabled, out of the tab order
    "confirm-findings": ["Confirm findings", "Reject verdict"],
    "plan-fixes": ["Reject verdict"],
    "accept": ["Accept verdict", "Reject verdict"],
}


def check_the_bar(page: Page, paper_url: str, step: str) -> None:
    """The bar at `step` on 4.1.2's newest assessment: it fits at 400px and 1280px in light and
    dark with no control under 44px, and its buttons are reached by Tab from the note."""
    for width in (400, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            paper = open_paper(page, paper_url)
            bar = paper.get_by_role("region", name="Review")
            if step == "confirm-findings":
                override(paper, "M1", "Evidenced", REASON)  # the bar with an override open
            expect(bar.get_by_role("button", name=MAIN[step])).to_be_visible()
            where = f"{step} at {width}px in {scheme}"
            assert not scrolls_sideways(page), f"the review scrolls sideways: {where}"
            assert small_targets(page) == [], where
            if step == "accept":
                expect(bar).to_contain_text("Plan fixes: skipped, nothing to fix")
    # The sign-off colour on the Accept button: champagne in dark (design.md, Tokens: --sign).
    if step == "accept":
        expect(bar.get_by_role("button", name="Accept verdict")).to_have_css(
            "background-color", "rgb(227, 203, 159)"
        )
    if step == "confirm-findings":
        page.get_by_role("form", name="Override M1").get_by_role("button", name="Cancel").click()
    bar.get_by_label("Your note (kept in the audit trail)").focus()
    for name in TABBED[step]:
        page.keyboard.press("Tab")
        expect(bar.get_by_role("button", name=name)).to_be_focused()


def test_the_review_bar_fits_and_is_reached_by_keyboard_at_each_step(page: Page, paper_url):
    # Three assessments in all: the paper's server counts them against the daily limit.
    assessed(page.request, paper_url)
    check_the_bar(page, paper_url, "confirm-findings")
    for overrides, step in (([], "plan-fixes"), ([M1_EVIDENCED], "accept")):
        thread_id = assessed(page.request, paper_url)["assessment"]["thread_id"]
        confirmed(page.request, paper_url, thread_id, overrides)
        check_the_bar(page, paper_url, step)


def test_at_400px_enter_on_change_focuses_the_override_and_cancel_returns_it(page: Page, paper_url):
    # #226: focus used to land on the page body; a keyboard user had to Tab back from the top.
    page.set_viewport_size({"width": 400, "height": 900})
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)
    change = row(paper, "M1").get_by_role("button", name="Change M1")
    change.focus()
    page.keyboard.press("Enter")

    form = page.get_by_role("form", name="Override M1")
    expect(form.get_by_label("Verdict")).to_be_focused()

    form.get_by_role("button", name="Cancel").focus()
    page.keyboard.press("Enter")
    expect(change).to_be_focused()

    page.keyboard.press("Enter")  # opened again, and saved: focus comes back to Change too
    form.get_by_label("Verdict").select_option("evidenced")
    form.get_by_label("Reason (required)").fill(REASON)
    form.get_by_role("button", name="Save").focus()
    page.keyboard.press("Enter")
    expect(change).to_be_focused()

    row(paper, "M1").get_by_role("button", name="Undo the override of M1").focus()
    page.keyboard.press("Enter")  # Undo goes once pressed: focus moves to Change, not the page
    expect(change).to_be_focused()


def test_leaving_with_an_unsaved_override_asks_first_and_cancel_keeps_it(page: Page, paper_url):
    # #182: an override saved but not yet recorded used to be dropped without a word.
    page.set_viewport_size({"width": 1280, "height": 900})
    assessed(page.request, paper_url)
    paper = open_paper(page, paper_url)
    override(paper, "M1", "Evidenced", REASON).get_by_role("button", name="Save").click()
    expect(row(paper, "M1")).to_contain_text("Overridden from Partly evidenced")
    other = page.get_by_role("navigation", name="Controls").get_by_role(
        "link", name=re.compile(r"^4\.1\.3")
    )
    asked: list[str] = []

    def answer(accept: bool):
        def handle(dialog: Dialog) -> None:
            asked.append(dialog.message)
            dialog.accept() if accept else dialog.dismiss()

        return handle

    page.once("dialog", answer(False))
    other.click()
    expect(row(paper, "M1")).to_contain_text("Overridden from Partly evidenced")
    assert asked == ["You have 1 override not yet recorded. Leave and drop it?"]
    assert page.url.endswith(f"/controls/{CONTROL}")

    page.once("dialog", answer(True))
    other.click()
    expect(page).to_have_url(re.compile(r"/controls/4\.1\.3$"))
    expect(page.get_by_role("heading", name=re.compile(r"^4\.1\.3 "))).to_be_visible()
    assert len(asked) == 2
