"""Alpenwerk's working paper shows German verdict words and answer, the interface English (#48).

Alpenwerk is chosen in the top bar's profile menu (#47). The assessment's stream is seeded, an
Alpenwerk assessment of 4.1.2 as the API sends it, since the server's fake model answers in
English, which the output check refuses for a German profile (#51).

Every verdict chip of the assessment is in German (#200): the run's, a requirement row's, and its
control's card in the Dossier. The seeded run isn't stored on the server, so the overview the
Dossier reads back is given 4.1.2's card as the run left it."""

import json
import re

from playwright.sync_api import Page, Route, expect

CONTROL = "4.1.2"
GERMAN_ANSWER = (
    "Die Authentifizierungsrichtlinie legt die Anmeldeverfahren nach dem Schutzbedarf fest; "
    "die Risikobewertung der Anmeldeverfahren ist nicht vollständig."
)
ALPENWERK_ASSESSMENT = {
    "control_id": CONTROL,
    "profile_id": "alpenwerk",
    "verdict": "partly-evidenced",
    "answer": GERMAN_ANSWER,
    "answer_language": "de",
    "requirements": [
        {
            "id": "M1",
            "column": "must",
            "text": "Die Anmeldeverfahren sind nach dem Schutzbedarf festgelegt.",
            "verdict": "not-evidenced",
            "reason": "Keine Risikobewertung der Anmeldeverfahren.",
            "citations": [],
        }
    ],
    "steps": [],
}
STEPS = [
    {"node": "load_control", "text": f"Loaded control {CONTROL}: Inwieweit wird der Zugang …"},
    {"node": "check_answer", "text": "Checked the answer: it claims no audit result."},
]


def seeded_stream(route: Route) -> None:
    events = [("step", step) for step in STEPS]
    events.append(("result", {"assessment": ALPENWERK_ASSESSMENT}))
    body = "".join(f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for e, d in events)
    route.fulfill(status=200, content_type="text/event-stream", body=body)


def overview_with_the_run(route: Route) -> None:
    """The overview as the server answers it, with Alpenwerk's 4.1.2 card as the seeded run left it."""
    overview = route.fetch().json()
    if overview["company"].startswith("Alpenwerk"):
        for card in overview["controls"]:
            if card["id"] == CONTROL:
                card.update(verdict="partly-evidenced", status="waiting-for-review")
    route.fulfill(json=overview)


def test_alpenwerk_verdict_chips_are_german_in_the_working_paper_and_the_dossier_in_an_english_interface(
    page: Page, vouch_url
):
    page.route("**/api/assessments", seeded_stream)
    with page.expect_response("**/api/overview"):
        page.goto(vouch_url)
    with page.expect_response("**/api/overview"):
        page.get_by_role("banner").get_by_role("combobox", name="Profile").select_option(
            label="Alpenwerk Präzision GmbH · Austria · German"
        )
    page.get_by_role("link", name=f"Open {CONTROL}").click()
    paper = page.get_by_role("region", name="Working paper")
    expect(paper).to_contain_text("Answer language: German")

    with page.expect_response("**/api/assessments"):
        paper.get_by_role("button", name=re.compile(rf"^Assess (again )?{re.escape(CONTROL)}$")).click()

    result = paper.get_by_role("region", name="Verdict and draft answer")
    expect(result.get_by_text("Teilweise nachgewiesen", exact=True)).to_be_visible()
    expect(result).to_contain_text(GERMAN_ANSWER)
    expect(result.get_by_text("Partly evidenced", exact=True)).to_have_count(0)
    # Marked as German, so a screen reader reads it as German (WCAG 3.1.2).
    expect(result.get_by_text("Teilweise nachgewiesen", exact=True)).to_have_attribute("lang", "de")
    expect(result.get_by_text(GERMAN_ANSWER, exact=True)).to_have_attribute("lang", "de")
    expect(result.get_by_text("Draft answer", exact=True)).not_to_have_attribute("lang", "de")
    # The interface stays English.
    expect(result.get_by_text("Draft answer", exact=True)).to_be_visible()
    expect(paper.get_by_text("How Vouch reached this", exact=True)).to_be_visible()
    expect(paper.get_by_role("button", name=f"Assess again {CONTROL}", exact=True)).to_be_visible()
    expect(paper.get_by_role("list", name="Agent steps")).to_contain_text(
        "Checked the answer: it claims no audit result."
    )

    # A requirement row's verdict is German too (#200); its column name stays English.
    m1 = paper.get_by_role("table", name="Requirements").get_by_role("row").filter(has_text="M1")
    expect(m1.locator(".chip")).to_have_text("Nicht nachgewiesen")
    expect(m1.locator(".chip")).to_have_attribute("lang", "de")
    expect(m1).to_contain_text("Must")

    # And its control's card in the Dossier (#200).
    page.route("**/api/overview", overview_with_the_run)
    with page.expect_response("**/api/overview"):
        paper.get_by_role("link", name="← Overview").click()
    card = page.get_by_role("article", name=f"Control {CONTROL}")
    expect(card.locator(".chip")).to_have_text("Teilweise nachgewiesen")
    expect(card.locator(".chip")).to_have_attribute("lang", "de")
    expect(card).to_contain_text("Proposed")
