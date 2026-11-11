"""Starting a profile afresh for a repeat demo (#285, spec #321 story 1), on a Vouch of its own
(afresh_url).

Alpenwerk: 4.1.2 is assessed and accepted through the API on the demo fake; the lead then starts
it afresh from the user menu, OKs the browser's confirm, and sees every control not assessed, the
report with every control open, and the earlier accept still in the report's audit trail.

Nordlicht (#605): the fresh start restores the demo state, so Company › Rounds compares with
"Q2 2026", three controls better."""

import json
import re

from current_company import current_company
from dossier_cards import card
from playwright.sync_api import Dialog, Page, expect
from reviewing import reviewed

CONTROL = "4.1.2"
COMPANY = "Alpenwerk Präzision GmbH"
NORDLICHT = "Nordlicht Komponenten GmbH"


def accepted(page: Page, afresh_url: str) -> None:
    """Assesses 4.1.2 under Nordlicht on the server's fake model and accepts it."""
    response = page.request.post(f"{afresh_url}/api/assessments", data={"control_id": CONTROL})
    assert response.ok, response.status
    data = re.search(r"event: result\r?\ndata: (.*)", response.text())
    assert data, response.text()
    thread_id = json.loads(data.group(1))["assessment"]["thread_id"]
    reviewed(page.request, afresh_url, thread_id)


def started_afresh(page: Page, company: str) -> None:
    """Start `company` afresh from the user menu, OK'ing the confirm, up to the app opened afresh."""
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Your menu").click()
    with (
        page.expect_response("**/api/progress/fresh-start", timeout=60_000) as fresh,
        page.expect_response("**/api/overview"),
    ):
        page.get_by_role("menuitem", name=f"Start {company} afresh…").click()
    assert fresh.value.ok


def test_starting_nordlicht_afresh_restores_the_demo_round(page: Page, afresh_url):
    page.set_viewport_size({"width": 1280, "height": 900})
    with page.expect_response("**/api/overview"):
        page.goto(afresh_url)
    started_afresh(page, NORDLICHT)

    with page.expect_response(re.compile(r".*/api/rounds$")):
        page.get_by_role("navigation", name="Main").get_by_role("link", name="Company").click()
    marked = page.get_by_role("region", name="Rounds", exact=True).get_by_role("listitem")
    marked = marked.filter(has_text="Compared by default")
    expect(marked).to_contain_text("Q2 2026")
    with page.expect_response(re.compile(r".*/api/rounds/\d+/comparison$")):
        marked.get_by_role("link").click()
    changes = page.get_by_role("region", name="Summary").get_by_role("list", name="Changes")
    expect(changes).to_contain_text("Better 3")
    expect(changes).to_contain_text("Worse 0")


def test_starting_alpenwerk_afresh_shows_every_control_open_and_keeps_the_trail(
    page: Page, afresh_url
):
    with current_company(page.request, afresh_url, "alpenwerk"):
        every_control_open_after_a_fresh_start(page, afresh_url)


def every_control_open_after_a_fresh_start(page: Page, afresh_url: str) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    accepted(page, afresh_url)
    with page.expect_response("**/api/overview"):
        page.goto(afresh_url)
    expect(card(page, CONTROL)).to_contain_text("Accepted")

    asked: list[str] = []

    def ok(dialog: Dialog) -> None:
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", ok)
    page.get_by_role("button", name="Your menu").click()
    with (
        page.expect_response("**/api/progress/fresh-start") as fresh,
        page.expect_response("**/api/overview"),
    ):
        page.get_by_role("menuitem", name=f"Start {COMPANY} afresh…").click()
    assert fresh.value.ok
    question = (
        f"Start {COMPANY} afresh? Your progress here starts from nothing; the audit trail keeps"
        " every entry."
    )
    assert asked == [question]

    expect(page.get_by_text("Nothing is assessed yet.")).to_be_visible()
    expect(card(page, CONTROL)).not_to_contain_text("Accepted")
    cards = page.get_by_role("article", name=re.compile(r"^Control "))
    expect(cards).to_have_count(12)
    expect(cards.locator(".tag")).to_have_count(0)  # no card has a review state: all not assessed

    with page.expect_response("**/api/report"):
        page.get_by_role("navigation", name="Main").get_by_role("link", name="Report").click()
    report = page.locator("article.report-doc")
    rows = report.locator("table.report-controls tbody tr")
    expect(rows).to_have_count(12)
    # Each open, in its status word (#463): with nothing reviewed, no Verdict column at all. The
    # report is in Alpenwerk's answer language, German.
    expect(rows.locator("[data-label=Status]")).to_have_text(["Offen"] * 12)
    expect(report.locator("table.report-controls th", has_text="Bewertung")).to_have_count(0)
    expect(rows.locator(".chip")).to_have_count(0)  # no reviewed verdict on any control
    expect(report.get_by_role("region", name="Bereitschaft").locator(".report-lead")).to_have_text(
        "0 von 12 Kontrollen akzeptiert"
    )
    trail = report.get_by_role("table", name="Prüfpfad").locator("tbody tr")
    expect(trail.filter(has_text="Akzeptiert").filter(has_text=CONTROL)).to_have_count(1)
