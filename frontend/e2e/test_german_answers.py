"""Alpenwerk's working paper shows German verdict words and answer, the interface English (#51).

The app serves one profile (Nordlicht) until profiles can be switched, so the assessment's
stream is seeded: an Alpenwerk assessment of 4.1.2, as the API sends it."""

import json

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
    "requirements": [],
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


def test_the_working_paper_shows_german_verdict_words_for_alpenwerk_in_an_english_interface(
    page: Page, vouch_url
):
    page.route("**/api/assessments", seeded_stream)
    page.goto(vouch_url)
    card = page.get_by_role("article").filter(has_text=CONTROL)

    with page.expect_response("**/api/assessments"):
        card.get_by_role("button", name=f"Assess {CONTROL}").click()

    paper = page.get_by_role("region", name="Working paper")
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
