"""Open questions (#414, spec #350 stories 11 and 12), on a Vouch of their own (questions_url): its
control is accepted, questioned and assessed again, which would change the counts and the cards
other servers' checks read.

Each run starts the profile afresh under Audit readiness (through the API). On the working paper
of 4.1.2 (the demo script: M1 partly evidenced) the lead assesses, confirms the findings, approves
M1's item and accepts; logs a question for "IT lead", due in 7 days, and sees it listed; records
its answer and sees "Answered on …"; sees the answered-question sentence on the paper and
"Question answered: assess again" on the card; assesses again and accepts, after which both are
gone; and the report's trail shows "Open question logged" and "Question answered". At 1280px and
400px, light and dark, nothing scrolls sideways and every control is at least 44px."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import APIRequestContext, Locator, Page, expect
from touch_targets import small_targets

CONTROL = "4.1.2"
QUESTION = "Who reviews the access rights of the build servers each quarter?"
ANSWER = "The IT lead, in the first week of each quarter."
ANSWERED = (
    f"An open question was answered. Assess {CONTROL} again so its verdict takes the answer into "
    "account."
)
ON_THE_CARD = "Question answered: assess again"


def readiness_afresh(request: APIRequestContext, url: str) -> None:
    """The profile started afresh, its goal Audit readiness."""
    started = request.post(f"{url}/api/progress/fresh-start")
    assert started.ok, started.status
    goal = request.put(f"{url}/api/goal", data={"goal": "audit-readiness"})
    assert goal.ok, goal.status


def fits(page: Page, where: str) -> None:
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    ), f"{where} scrolls sideways"
    assert small_targets(page) == [], where


def assess_and_accept(page: Page, paper: Locator) -> None:
    """Assess (again) on the paper, confirm the findings, approve M1's item and accept."""
    with page.expect_response("**/api/assessments"):
        paper.locator(".assess").get_by_role(
            "button", name=re.compile(rf"^Assess( again)? {CONTROL}$")
        ).click()
    bar = paper.get_by_role("region", name="Review")
    with page.expect_response(re.compile(r"/findings$")) as confirmed:
        bar.get_by_role("button", name="Confirm findings").click()
    assert confirmed.value.ok, confirmed.value.status
    with page.expect_response(re.compile(r"/remediation/M1$")):
        paper.get_by_role("button", name="Approve M1").click()
    with page.expect_response(re.compile(r"/review$")) as accepted:
        bar.get_by_role("button", name="Accept verdict").click()
    assert accepted.value.ok, accepted.value.status
    expect(bar).to_contain_text("Accepted")


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_stories_11_and_12_log_answer_and_reassess(page: Page, questions_url, width, scheme):
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    readiness_afresh(page.request, questions_url)
    with page.expect_response("**/api/overview"):
        page.goto(f"{questions_url}/controls/{CONTROL}")
    paper = page.get_by_role("region", name="Working paper")
    assess_and_accept(page, paper)

    # Story 11: a question logged, then answered.
    questions = paper.get_by_role("region", name="Open questions")
    expect(questions).to_contain_text("No open questions on this control.")
    questions.get_by_role("button", name="Log a question").click()
    form = questions.get_by_role("form", name=f"Log a question on {CONTROL}")
    form.get_by_role("button", name="Log question").click()
    expect(form.get_by_role("alert")).to_have_text("Say what is asked first.")
    fits(page, f"the log form, {width}px {scheme}")
    due = datetime.now(UTC).date() + timedelta(days=7)
    form.get_by_label("Question").fill(QUESTION)
    form.get_by_label("For whom").fill("IT lead")
    form.get_by_label("Due date").fill(due.isoformat())
    with (
        page.expect_response(re.compile(r"/questions$")) as logged,
        page.expect_response("**/api/overview"),
    ):
        form.get_by_role("button", name="Log question").click()
    assert logged.value.ok, logged.value.status
    listed = questions.get_by_role("listitem")
    expect(listed).to_have_count(1)
    expect(listed).to_contain_text(QUESTION)
    expect(listed).to_contain_text(f"For IT lead · Due {due.day} {due:%B} {due.year}")
    fits(page, f"a listed question, {width}px {scheme}")

    listed.get_by_role("button", name="Record answer").click()
    listed.get_by_role("textbox", name="Answer", exact=True).fill(ANSWER)
    fits(page, f"the answer form, {width}px {scheme}")
    with (
        page.expect_response(re.compile(r"/answer$")) as answered,
        page.expect_response("**/api/overview"),
    ):
        listed.get_by_role("button", name="Save answer").click()
    assert answered.value.ok, answered.value.status
    expect(listed).to_contain_text(re.compile(rf"Answered on \d+ \w+ \d{{4}}: {re.escape(ANSWER)}"))
    expect(listed.get_by_role("button", name="Record answer")).to_have_count(0)

    # Story 12: a re-assessment suggested on the paper and the card.
    expect(paper.get_by_text(ANSWERED)).to_be_visible()
    fits(page, f"an answered question, {width}px {scheme}")
    with page.expect_response("**/api/overview"):
        page.get_by_role("link", name="Overview", exact=True).first.click()
    card = page.get_by_role("article", name=f"Control {CONTROL}")
    expect(card).to_contain_text(ON_THE_CARD)
    fits(page, f"the Dossier with a suggestion, {width}px {scheme}")

    # Assessed again and accepted: the suggestion is gone; the question stays answered.
    card.get_by_role("link", name=f"Open {CONTROL}").click()
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_text(ANSWERED)).to_be_visible()
    assess_and_accept(page, paper)
    expect(paper.get_by_text(ANSWERED)).to_have_count(0)
    expect(paper.get_by_role("region", name="Open questions")).to_contain_text(ANSWER)
    with page.expect_response("**/api/overview"):
        page.get_by_role("link", name="Overview", exact=True).first.click()
    card = page.get_by_role("article", name=f"Control {CONTROL}")
    expect(card.locator(".tag")).to_have_text("Accepted")
    expect(card).not_to_contain_text(ON_THE_CARD)

    # The report's audit trail shows the question and its answer.
    with page.expect_response("**/api/report"):
        page.goto(f"{questions_url}/report")
    trail = page.get_by_role("table", name="Audit trail")
    expect(trail.get_by_role("cell", name="Open question logged", exact=True).first).to_be_visible()
    expect(trail.get_by_role("cell", name="Question answered", exact=True).first).to_be_visible()
    expect(trail).to_contain_text(f"{QUESTION} (for IT lead, due {due.isoformat()})")
