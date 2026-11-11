"""The current company (#480, M9 decision 1): every user sees the one a developer made current on
the Developer page; no top bar shows a profile switcher, and the browser keeps no choice of its
own. Each company still shows its own progress and chat (#47, #251).

They run on PROFILES_URL, whose profiles folder holds Nordlicht, a second test profile (Testwerk)
and a broken one; the bundled companies' check on DOSSIER_URL. A check that makes another company
current puts Nordlicht back after (current_company.py). Every server runs with the suite's raised
request rate (Fix #138)."""

import json
import re
import uuid

import pytest
from current_company import current_company, nordlicht_after
from playwright.sync_api import APIRequestContext, Browser, Page, expect
from reviewing import reviewed
from top_bar import bar_company, bar_tag

TESTWERK = "Testwerk Prüfstand GmbH"


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
    reviewed(request, url, thread_id, headers={"X-Vouch-Profile": profile})


def opened(page: Page, url: str) -> None:
    with page.expect_response("**/api/profiles"), page.expect_response("**/api/overview"):
        page.goto(url)


def card(page: Page, control_id: str):
    return page.get_by_role("article", name=f"Control {control_id}", exact=True)


def accepted_tile(page: Page):
    band = page.get_by_role("region", name="Readiness")
    return band.locator(".tile").filter(has=page.get_by_text("accepted of 12", exact=True))


def companies(page: Page):
    return page.get_by_role("region", name="Companies")


def seeded(request: APIRequestContext, url: str) -> None:
    """Seeded on the server's fake model: Nordlicht has 4.1.2 accepted; Testwerk 1.1.1 proposed."""
    thread_id = assessed(request, url, "nordlicht", "4.1.2")
    accepted(request, url, "nordlicht", thread_id)
    assessed(request, url, "testwerk", "1.1.1")


def shows_nordlicht(page: Page) -> None:
    expect(bar_company(page)).to_have_text("Nordlicht Komponenten GmbH")  # the top bar's (#495)
    expect(bar_tag(page)).to_have_text("DE · EN")
    expect(accepted_tile(page).locator("b")).to_have_text("1")
    expect(card(page, "4.1.2")).to_contain_text("Accepted")
    expect(card(page, "1.1.1")).to_contain_text("Not assessed")


def shows_testwerk(page: Page) -> None:
    expect(bar_company(page)).to_have_text("Testwerk Prüfstand GmbH")
    expect(bar_tag(page)).to_contain_text("AT · ")
    expect(accepted_tile(page).locator("b")).to_have_text("0")
    expect(card(page, "4.1.2")).to_contain_text("Not assessed")
    expect(card(page, "1.1.1")).to_contain_text("Proposed")


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("role", ["developer", "lead"])
def test_no_top_bar_shows_a_profile_switcher_and_nothing_is_kept_in_the_browser(
    page: Page, profiles_url, width, role
):
    """Two companies listed, at desktop and phone width, for a developer and a compliance lead
    (the developer API refused, as test_developer_page's lead): no switcher, and nothing under
    the old `vouch.profile` key."""
    if role == "lead":
        page.route("**/api/developer/**", lambda route: route.fulfill(status=403, json={}))
    page.set_viewport_size({"width": width, "height": 900})
    opened(page, profiles_url)

    banner = page.get_by_role("banner")
    if width < 700:
        banner.get_by_role("button", name="Menu", exact=True).click()
    expect(banner.get_by_role("button", name=re.compile(r"^Profile"))).to_have_count(0)
    expect(banner.get_by_role("menuitemradio")).to_have_count(0)
    expect(banner.locator(".prof, .prof-button")).to_have_count(0)
    assert page.evaluate("window.localStorage.getItem('vouch.profile')") is None
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_story_2_a_company_made_current_on_the_developer_page_is_every_users(
    page: Page, browser: Browser, profiles_url
):
    """Story 2's end-to-end check: a developer makes Testwerk current; their app is reopened under
    it on the Developer page, saying so, and a second browser that then opens the app shows
    Testwerk's progress. Nordlicht is put back after."""
    seeded(page.request, profiles_url)
    opened(page, profiles_url)
    shows_nordlicht(page)

    with nordlicht_after(page.request, profiles_url):
        with page.expect_response("**/api/developer/settings"):
            page.goto(f"{profiles_url}/developer")
        with page.expect_response("**/api/developer/current-company"):
            companies(page).get_by_role("button", name=f"Make {TESTWERK} current").click()
        note = companies(page).get_by_role("status")
        expect(note).to_have_text(f"{TESTWERK} is now the current company.")
        expect(note).to_be_focused()
        expect(page).to_have_url(f"{profiles_url}/developer")
        row = companies(page).get_by_role("listitem").filter(has_text=TESTWERK)
        expect(row.get_by_text("Current", exact=True)).to_be_visible()
        expect(row.get_by_role("button")).to_have_count(0)

        other = browser.new_context()
        try:
            second = other.new_page()
            with second.expect_response(lambda r: r.url.endswith("/api/overview")) as answer:
                second.goto(profiles_url)
            assert answer.value.request.header_value("X-Vouch-Profile") == "testwerk"
            shows_testwerk(second)
        finally:
            other.close()

        # The developer's own app is under Testwerk too.
        with page.expect_response(lambda r: r.url.endswith("/api/overview")):
            page.get_by_role("navigation", name="Main").get_by_role("link", name="Overview").click()
        shows_testwerk(page)


def sent(page: Page, message: str):
    """Sends `message` in the open chat; the chat request it made."""
    page.get_by_role("textbox", name="Message to Vouch").fill(message)
    with page.expect_response("**/api/chat") as answer:
        page.get_by_role("button", name="Send").click()
    return answer.value.request


def test_another_current_company_shows_its_own_chat_and_the_next_message_is_under_it(
    page: Page, profiles_url
):
    """Since #251 each company keeps its own conversation: under another current company the
    chat shows that one's, never the old one's. The question is this run's own, and each opening
    waits for the kept conversation to be read before looking (#317)."""
    question = f"Where do I stand? (run {uuid.uuid4().hex[:8]})"
    opened(page, profiles_url)
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    first = sent(page, question)
    log = page.locator("aside[aria-label='Chat with Vouch']").get_by_role("log")
    expect(log).to_contain_text(question)
    assert first.header_value("X-Vouch-Profile") == "nordlicht"

    with current_company(page.request, profiles_url, "testwerk"):
        opened(page, profiles_url)
        with page.expect_response("**/api/chat/current"):
            page.get_by_role("button", name="Ask Vouch").click()
        expect(log).not_to_contain_text(question)
        second = sent(page, "What is missing?")
        assert second.header_value("X-Vouch-Profile") == "testwerk"


def test_the_bundled_companies_are_both_in_the_companies_section(page: Page, dossier_url):
    """The fixtures hold Nordlicht and Alpenwerk (#49): the Developer page lists both, Nordlicht
    current."""
    with page.expect_response("**/api/developer/settings"):
        page.goto(f"{dossier_url}/developer")

    rows = companies(page).get_by_role("listitem")
    expect(rows.locator(".company-name")).to_have_text(
        ["Alpenwerk Präzision GmbH", "Nordlicht Komponenten GmbH"]
    )
    expect(rows.nth(1).get_by_text("Current", exact=True)).to_be_visible()
    expect(
        rows.nth(0).get_by_role("button", name="Make Alpenwerk Präzision GmbH current")
    ).to_be_visible()


@pytest.mark.parametrize(
    ("profile", "company"),
    [("nordlicht", "Nordlicht Komponenten GmbH"), ("testwerk", TESTWERK)],
)
def test_a_cross_control_answer_links_each_named_control_to_its_paper_in_that_profile(
    page: Page, profiles_url, profile, company
):
    """#217: the chat's answer across controls links each control it names to its working paper,
    opened under the company the chat answered for."""
    assessed(page.request, profiles_url, profile, "1.1.1")
    with current_company(page.request, profiles_url, profile):
        opened(page, profiles_url)
        page.get_by_role("button", name="Ask Vouch").click()
        asked = sent(page, "Which controls have documented evidence only?")
        assert asked.header_value("X-Vouch-Profile") == profile
        log = page.locator("aside[aria-label='Chat with Vouch']").get_by_role("log")
        link = log.get_by_role("link", name="Working paper 1.1.1")
        expect(link).to_be_visible()

        # The paper opens in the app, reading the control's stored assessment under the chat's
        # company.
        with page.expect_response(re.compile(r"/api/assessments/[^/]+$")) as read:
            link.click()
        expect(page).to_have_url(re.compile(r"/controls/1\.1\.1$"))
        assert read.value.request.header_value("X-Vouch-Profile") == profile
        paper = page.get_by_role("region", name="Working paper")
        expect(paper.get_by_text(f"Working paper · {company}")).to_be_visible()
