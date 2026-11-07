"""The top bar's profile switcher (#47): with two profiles, switching shows each one's own
progress; with one, its name and no menu; a broken profile is left out.

They run on PROFILES_URL, whose profiles folder holds Nordlicht, a second test profile and a
broken one; the one-profile check on DOSSIER_URL (the fixtures' one profile). Every server runs
with the suite's raised request rate (Fix #138)."""

import json
import re
import uuid

import pytest
from playwright.sync_api import APIRequestContext, Page, expect
from top_bar import open_profile_menu, profile_button, switch_profile

NORDLICHT = "Nordlicht Komponenten GmbH · Germany · English"
TESTWERK = "Testwerk Prüfstand GmbH · Austria · English"


def assessed(request: APIRequestContext, url: str, profile: str, control_id: str) -> str:
    """Assesses a control under `profile` on the server's fake model, up to the review."""
    response = request.post(
        f"{url}/api/assessments",
        data={"control_id": control_id},
        headers={"X-Vouch-Profile": profile},
    )
    assert response.ok, response.status
    lines = [line.strip() for line in response.text().splitlines()]
    data = lines[lines.index("event: result") + 1].removeprefix("data:").strip()
    return json.loads(data)["assessment"]["thread_id"]


def accepted(request: APIRequestContext, url: str, profile: str, thread_id: str) -> None:
    response = request.post(
        f"{url}/api/assessments/{thread_id}/review",
        data={"decision": "accept"},
        headers={"X-Vouch-Profile": profile},
    )
    assert response.ok, response.status


def opened(page: Page, url: str) -> None:
    with page.expect_response("**/api/profiles"), page.expect_response("**/api/overview"):
        page.goto(url)


def card(page: Page, control_id: str):
    return page.get_by_role("article", name=f"Control {control_id}", exact=True)


def accepted_tile(page: Page):
    band = page.get_by_role("region", name="Readiness")
    return band.locator(".tile").filter(has=page.get_by_text("accepted of 12", exact=True))


def test_switching_shows_each_profiles_own_progress(page: Page, profiles_url):
    # Seeded on the server's fake model: Nordlicht has 4.1.2 accepted; Testwerk has 1.1.1 proposed.
    seeded = assessed(page.request, profiles_url, "nordlicht", "4.1.2")
    accepted(page.request, profiles_url, "nordlicht", seeded)
    assessed(page.request, profiles_url, "testwerk", "1.1.1")

    opened(page, profiles_url)
    # The broken profile is left out.
    expect(open_profile_menu(page)).to_have_text([NORDLICHT, TESTWERK])
    page.keyboard.press("Escape")
    expect(page.locator(".pill").first).to_have_text("Nordlicht Komponenten GmbH · Germany")
    expect(accepted_tile(page).locator("b")).to_have_text("1")
    expect(card(page, "4.1.2")).to_contain_text("Accepted")
    expect(card(page, "1.1.1")).to_contain_text("Not assessed")

    with page.expect_response(lambda r: r.url.endswith("/api/overview")) as answer:
        switch_profile(page, TESTWERK)
    assert answer.value.request.header_value("X-Vouch-Profile") == "testwerk"

    expect(page.locator(".pill").first).to_have_text("Testwerk Prüfstand GmbH · Austria")
    expect(accepted_tile(page).locator("b")).to_have_text("0")
    expect(card(page, "4.1.2")).to_contain_text("Not assessed")
    expect(card(page, "1.1.1")).to_contain_text("Proposed")

    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        switch_profile(page, NORDLICHT)
    expect(accepted_tile(page).locator("b")).to_have_text("1")
    expect(card(page, "1.1.1")).to_contain_text("Not assessed")


def sent(page: Page, message: str):
    """Sends `message` in the open chat; the chat request it made."""
    page.get_by_role("textbox", name="Message to Vouch").fill(message)
    with page.expect_response("**/api/chat") as answer:
        page.get_by_role("button", name="Send").click()
    return answer.value.request


def test_switching_shows_the_new_profiles_own_chat_and_the_next_message_is_under_it(
    page: Page, profiles_url
):
    """Since #251 each profile keeps its own conversation: after a switch the chat shows the
    new profile's, never the old one's. The question is this run's own (an earlier test on this
    server may have asked "Where do I stand?" under Testwerk), and each opening waits for the
    kept conversation to be read before looking (#317: a check before it raced the restore)."""
    question = f"Where do I stand? (run {uuid.uuid4().hex[:8]})"
    opened(page, profiles_url)
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    first = sent(page, question)
    log = page.locator("aside[aria-label='Chat with Vouch']").get_by_role("log")
    expect(log).to_contain_text(question)
    assert first.header_value("X-Vouch-Profile") == "nordlicht"

    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        switch_profile(page, TESTWERK)
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    expect(log).not_to_contain_text(question)
    second = sent(page, "What is missing?")
    assert second.header_value("X-Vouch-Profile") == "testwerk"


def test_a_reload_or_a_pasted_link_keeps_the_chosen_profile(page: Page, profiles_url):
    """#201: the browser remembers the switch, so a working paper opened afresh is the same company's."""
    opened(page, profiles_url)
    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        switch_profile(page, TESTWERK)

    for arrive in (lambda: page.goto(f"{profiles_url}/controls/1.1.1"), page.reload):
        with page.expect_response(lambda r: r.url.endswith("/api/overview")) as answer:
            arrive()
        assert answer.value.request.header_value("X-Vouch-Profile") == "testwerk"
        expect(profile_button(page)).to_have_accessible_name(f"Profile: {TESTWERK}")
        expect(page.get_by_role("banner").get_by_role("status")).to_have_count(0)


def test_the_switcher_fits_a_phone_in_dark(page: Page, profiles_url):
    page.set_viewport_size({"width": 400, "height": 900})
    page.emulate_media(color_scheme="dark")
    opened(page, profiles_url)

    # On a phone the profile sits behind the top bar's Menu button (#222).
    page.get_by_role("banner").get_by_role("button", name="Menu", exact=True).click()
    expect(profile_button(page)).to_be_visible()
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_the_bundled_profiles_are_both_in_the_menu(page: Page, dossier_url):
    """The fixtures hold Nordlicht and Alpenwerk (#49), so the menu lists both; one profile's
    name without a menu is checked below the UI (profiles.test.tsx)."""
    opened(page, dossier_url)

    expect(open_profile_menu(page)).to_have_text(
        ["Alpenwerk Präzision GmbH · Austria · German", NORDLICHT]
    )


@pytest.mark.parametrize(
    ("profile", "label", "company"),
    [
        ("nordlicht", NORDLICHT, "Nordlicht Komponenten GmbH"),
        ("testwerk", TESTWERK, "Testwerk Prüfstand GmbH"),
    ],
)
def test_a_cross_control_answer_links_each_named_control_to_its_paper_in_that_profile(
    page: Page, profiles_url, profile, label, company
):
    """#217: the chat's answer across controls links each control it names to its working paper,
    opened in the profile the chat answered for."""
    assessed(page.request, profiles_url, profile, "1.1.1")
    opened(page, profiles_url)
    if profile != "nordlicht":
        with page.expect_response(lambda r: r.url.endswith("/api/overview")):
            switch_profile(page, label)
    page.get_by_role("button", name="Ask Vouch").click()
    asked = sent(page, "Which controls have documented evidence only?")
    assert asked.header_value("X-Vouch-Profile") == profile
    log = page.locator("aside[aria-label='Chat with Vouch']").get_by_role("log")
    link = log.get_by_role("link", name="Working paper 1.1.1")
    expect(link).to_be_visible()

    # The paper opens in the app, reading the control's stored assessment under the chat's profile.
    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")) as read:
        link.click()
    expect(page).to_have_url(re.compile(r"/controls/1\.1\.1$"))
    assert read.value.request.header_value("X-Vouch-Profile") == profile
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_text(f"Working paper · {company}")).to_be_visible()
