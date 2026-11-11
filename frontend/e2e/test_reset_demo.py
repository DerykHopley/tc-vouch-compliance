"""Reset demo (#605), the Nordlicht story, on a Vouch of its own (reset_url): the developer
resets Nordlicht from its row on the Developer page, OKs the confirm naming it, and Company ›
Rounds then lists exactly Q2 2026 (closed) and Q3 2026 (open), comparing with Q2 2026: Better 3,
Worse 0. The fake model throughout; the reset's rules are tests/test_reset_demo.py's."""

import re

from playwright.sync_api import Page, expect

NORDLICHT = "Nordlicht Komponenten GmbH"


def test_reset_demo_on_nordlicht_restores_the_demo_round(page: Page, reset_url):
    page.set_viewport_size({"width": 1280, "height": 900})
    with page.expect_response("**/api/developer/settings"):
        page.goto(f"{reset_url}/developer")
    asked: list[str] = []

    def ok(dialog) -> None:
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", ok)
    with page.expect_response("**/api/developer/profiles/nordlicht/reset-demo", timeout=60_000):
        page.get_by_role("button", name=f"Reset the demo of {NORDLICHT}").click()
    assert asked and asked[0].startswith(f"Reset the demo of {NORDLICHT}?")
    expect(page.get_by_role("region", name="Companies").get_by_role("status")).to_have_text(
        f"Reset {NORDLICHT} to its demo state: Q2 2026 closed, Q3 2026 open."
    )

    with page.expect_response(re.compile(r".*/api/rounds$")):
        page.get_by_role("navigation", name="Main").get_by_role("link", name="Company").click()
    section = page.get_by_role("region", name="Rounds", exact=True)
    expect(section.locator(".rounds-name")).to_have_text(["Q3 2026", "Q2 2026"])  # newest first
    expect(section).not_to_contain_text("fresh start")
    marked = section.get_by_role("listitem").filter(has_text="Compared by default")
    expect(marked).to_contain_text("Q2 2026")
    with page.expect_response(re.compile(r".*/api/rounds/\d+/comparison$")):
        marked.get_by_role("link").click()
    changes = page.get_by_role("region", name="Summary").get_by_role("list", name="Changes")
    expect(changes).to_contain_text("Better 3")
    expect(changes).to_contain_text("Worse 0")
