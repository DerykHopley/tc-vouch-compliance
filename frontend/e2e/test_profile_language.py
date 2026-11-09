"""Text in the profile's language carries `lang` (#244, after #200, #207, #224 and PR #227).

On Alpenwerk (German), the check walks the overview, a working paper with an assessment and the
chat, and fails on any visible text that reads as German (German letters or common German words)
without a `lang="de"` around it, naming the text. The interface stays English and unmarked; proper
names, the same in every language, are allowed as they are.

The assessment and the chat's reply are seeded, as the server's fake model answers in English,
which the output check refuses for a German profile (#51)."""

import json
import re

from playwright.sync_api import Page, Route, expect
from test_german_answers import ALPENWERK_ASSESSMENT, CONTROL, seeded_stream
from top_bar import switch_profile

ALPENWERK = "Alpenwerk Präzision GmbH · Austria · German"
PROPER_NAMES = ["Alpenwerk Präzision GmbH"]
"""Names, the same in every language: allowed without `lang`."""
GERMAN_REPLY = "Für 4.1.2 fehlt die Risikobewertung der Anmeldeverfahren."

# Each visible text node that reads as German with no lang="de" around it: its text.
UNMARKED_GERMAN = r"""(names) => {
  const german = /[äöüÄÖÜß]|\b([Uu]nd|[Dd]er|[Dd]as|[Ww]erden|[Ww]ird|[Nn]icht|[Ss]ind|ist|die|Die|Inwieweit|Anforderungen?|nachgewiesen|fehlt)\b/
  const found = []
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    let text = node.textContent.trim()
    for (const name of names) text = text.replaceAll(name, '')
    if (!german.test(text)) continue
    const el = node.parentElement
    if (!el || !el.checkVisibility()) continue
    const lang = el.closest('[lang]')?.getAttribute('lang') ?? ''
    if (!lang.startsWith('de')) found.push(node.textContent.trim())
  }
  return found
}"""


def assert_marked(page: Page, *own_words: str) -> None:
    """Every German text on the page carries lang="de", but the lead's `own_words`."""
    unmarked = [t for t in page.evaluate(UNMARKED_GERMAN, PROPER_NAMES) if t not in own_words]
    assert unmarked == [], 'German text without lang="de":\n' + "\n".join(unmarked)


def german_reply(route: Route) -> None:
    events = [
        ("thread", {"thread_id": "a" * 32}),
        ("token", {"text": GERMAN_REPLY}),
        ("done", {}),
    ]
    body = "".join(f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for e, d in events)
    route.fulfill(status=200, content_type="text/event-stream", body=body)


def on_alpenwerk(page: Page, url: str) -> None:
    page.set_viewport_size({"width": 1400, "height": 900})
    with page.expect_response("**/api/overview"):
        page.goto(url)
    with page.expect_response("**/api/overview"):
        switch_profile(page, ALPENWERK)


def test_on_alpenwerk_every_german_text_carries_lang_de(page: Page, vouch_url):
    page.route("**/api/assessments", seeded_stream)
    page.route("**/api/chat", german_reply)
    on_alpenwerk(page, vouch_url)

    # The overview: the controls' German questions.
    expect(page.get_by_role("article", name=f"Control {CONTROL}")).to_be_visible()
    assert_marked(page)

    # A working paper, with an assessment: heading, requirements, reasons, the answer, verdicts.
    page.get_by_role("link", name=f"Open {CONTROL}").click()
    paper = page.get_by_role("region", name="Working paper")
    with page.expect_response("**/api/assessments"):
        paper.get_by_role(
            "button", name=re.compile(rf"^Assess (again )?{re.escape(CONTROL)}$")
        ).click()
    expect(paper).to_contain_text(ALPENWERK_ASSESSMENT["requirements"][0]["reason"])
    assert_marked(page)

    # The chat: a German reply.
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("textbox", name="Message to Vouch").fill("Was fehlt?")
    with page.expect_response("**/api/chat"):
        page.get_by_role("button", name="Send").click()
    expect(page.locator("aside[aria-label='Chat with Vouch']")).to_contain_text(GERMAN_REPLY)
    assert_marked(page, "Was fehlt?")  # the lead's own words are theirs, in any language

    # The readiness report: its words follow the profile's language (#56).
    page.get_by_role("link", name="Report").click()
    report = page.locator("article.report-doc")
    expect(report).to_have_attribute("lang", "de")
    expect(report.get_by_role("heading", level=1)).to_have_text("Bereitschaftsbericht")
    assert_marked(page, "Was fehlt?")

    # The Documents tab: its words follow the profile's language (#424); the tab name stays English.
    with page.expect_response("**/api/documents"):
        page.get_by_role("link", name="Documents").click()
    documents = page.locator("article.documents-doc")
    expect(documents).to_have_attribute("lang", "de")
    expect(documents.get_by_role("heading", level=1)).to_have_text("Dokumente")
    assert_marked(page, "Was fehlt?")
