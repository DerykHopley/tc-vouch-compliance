"""The improvement plan (#410, spec #350 stories 9 and 10), on a Vouch of its own (plan_url): its
goal is Internal improvement and its control ends Closed, which other servers' checks would read.

Each run starts the profile afresh and sets the goal (through the API: the goal's own check is
test_goal's). On the working paper of 4.1.2 (the demo script: M1 partly evidenced, the rest
evidenced) the lead confirms the findings, approves M1's item and accepts the control; sets the
item's priority to Low; marks it done with a note and sees the re-assess sentence; assesses the
control again and, as the demo script finds the same gap, overrides M1 to evidenced at Confirm
findings (so the re-assessment has no gap) and accepts it. The paper's head and the card then read
Closed, and the report's trail shows the priority change and the done mark. At 1280px and 400px,
light and dark, nothing scrolls sideways and every control is at least 44px."""

import re

import pytest
from playwright.sync_api import APIRequestContext, Locator, Page, expect
from reviewing import decide
from touch_targets import small_targets

CONTROL = "4.1.2"
NOTE = "The exposure record is now kept for every system, reviewed each quarter."
REASSESS = (
    f"Every approved remediation item is done. Assess {CONTROL} again: the control is Closed once "
    "that assessment is accepted with no gaps."
)


def improving_afresh(request: APIRequestContext, url: str) -> None:
    """The profile started afresh, its goal Internal improvement."""
    started = request.post(f"{url}/api/progress/fresh-start")
    assert started.ok, started.status
    goal = request.put(f"{url}/api/goal", data={"goal": "internal-improvement"})
    assert goal.ok, goal.status


def fits(page: Page, where: str) -> None:
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    ), f"{where} scrolls sideways"
    assert small_targets(page) == [], where


def row(paper: Locator, requirement_id: str) -> Locator:
    return paper.locator("tbody tr").filter(
        has=paper.page.locator("th[scope=row] .mono").get_by_text(requirement_id, exact=True)
    )


def head_tag(paper: Locator) -> Locator:
    return paper.locator(".paper-head .paper-meta").filter(has_text="Verdict").locator(".tag")


def assess_on_the_paper(page: Page, paper: Locator) -> None:
    """Assess (again), waiting for the run to finish at Confirm findings."""
    with page.expect_response("**/api/assessments"):
        paper.locator(".assess").get_by_role(
            "button", name=re.compile(rf"^Assess( again)? {CONTROL}$")
        ).click()
    expect(paper.get_by_role("button", name="Confirm findings")).to_be_visible()


def confirm_and_accept(page: Page, paper: Locator, *, approve: bool) -> None:
    bar = paper.get_by_role("region", name="Review")
    with page.expect_response(re.compile(r"/findings$")) as confirmed:
        bar.get_by_role("button", name="Confirm findings").click()
    assert confirmed.value.ok, confirmed.value.status
    if approve:
        item = paper.get_by_role("region", name="Remediation item M1")  # its own row (#464)
        expect(item).to_contain_text("Priority: Medium")  # the default for partly evidenced
        with page.expect_response(re.compile(r"/remediation/M1$")):
            item.get_by_role("button", name="Approve M1").click()
    with page.expect_response("**/api/overview"):  # read again in the improvement goal
        accepted = decide(page, bar, "Accept verdict")
    assert accepted.ok, accepted.status


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_stories_9_and_10_priority_done_reassess_and_closed(page: Page, plan_url, width, scheme):
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    improving_afresh(page.request, plan_url)
    with page.expect_response("**/api/overview"):
        page.goto(f"{plan_url}/controls/{CONTROL}")
    paper = page.get_by_role("region", name="Working paper")

    # Story 9: the control accepted with a gap, its item approved; its priority set to Low.
    assess_on_the_paper(page, paper)
    confirm_and_accept(page, paper, approve=True)
    item = paper.get_by_role("region", name="Remediation item M1")  # its own row (#464)
    priority = item.get_by_label("Priority")
    expect(priority).to_have_value("medium")
    fits(page, f"an approved item, {width}px {scheme}")
    with page.expect_response(re.compile(r"/remediation/M1/priority$")) as changed:
        priority.select_option("low")
    assert changed.value.ok, changed.value.status
    expect(item).to_contain_text("Priority: Low")
    expect(priority).to_have_value("low")

    # Story 10: marked done with a note; the paper suggests assessing again.
    item.get_by_role("button", name="Mark M1 done").click()
    expect(item.get_by_label("What was done")).to_be_visible()
    fits(page, f"the done form, {width}px {scheme}")
    item.get_by_role("button", name="Mark done", exact=True).click()
    expect(item.get_by_role("alert")).to_have_text("Say what was done first.")
    item.get_by_label("What was done").fill(NOTE)
    with (
        page.expect_response(re.compile(r"/remediation/M1/done$")) as marked,
        page.expect_response("**/api/overview"),
    ):
        item.get_by_role("button", name="Mark done", exact=True).click()
    assert marked.value.ok, marked.value.status
    expect(item.locator(".tag")).to_have_text("Done")
    expect(item).to_contain_text(re.compile(rf"Done on \d+ \w+ \d{{4}}: {re.escape(NOTE)}"))
    expect(item.get_by_label("Priority")).to_have_count(0)
    expect(paper.get_by_text(REASSESS)).to_be_visible()
    fits(page, f"a done item, {width}px {scheme}")

    # The Dossier's card suggests it too.
    page.get_by_role("link", name="Overview", exact=True).first.click()
    card = page.get_by_role("article", name=f"Control {CONTROL}")
    expect(card).to_contain_text("Fixes done: assess again")
    fits(page, f"the Dossier with a suggestion, {width}px {scheme}")

    # Assessed again with no gap and accepted: Closed on the paper and the card.
    card.get_by_role("link", name=f"Open {CONTROL}").click()  # its run is this page's: no read
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_text(REASSESS)).to_be_visible()
    assess_on_the_paper(page, paper)
    row(paper, "M1").get_by_role("button", name="Change M1").click()
    editor = paper.get_by_role("form", name="Override M1")
    editor.get_by_label("Verdict").select_option(label="Evidenced")
    editor.get_by_label("Reason (required)").fill("The exposure record covers every system.")
    editor.get_by_role("button", name="Save").click()
    confirm_and_accept(page, paper, approve=False)
    expect(head_tag(paper)).to_have_text("Closed")
    fits(page, f"a Closed paper, {width}px {scheme}")

    with page.expect_response("**/api/overview"):
        page.get_by_role("link", name="Overview", exact=True).first.click()
    card = page.get_by_role("article", name=f"Control {CONTROL}")
    expect(card.locator(".tag")).to_have_text("Closed")
    expect(card).not_to_contain_text("Fixes done")
    fits(page, f"the Dossier with a Closed card, {width}px {scheme}")

    # The report's audit trail shows the priority change and the done mark.
    with page.expect_response("**/api/report"):
        page.goto(f"{plan_url}/report")
    trail = page.get_by_role("table", name="Audit trail")
    expect(trail.get_by_role("cell", name="Marked done", exact=True).first).to_be_visible()
    expect(trail.get_by_role("cell", name="Priority changed", exact=True).first).to_be_visible()
    expect(trail).to_contain_text("Priority: low (was medium).")
