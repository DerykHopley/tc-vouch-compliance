"""Ask Vouch keeps its conversation (#251): one per user and profile, kept on the server, restored
on return as it was shown, with a "New conversation" control. Local mode, on the canned chat model:
it has no sign-in, so a reload stands in for signing out and in (the conversation is the server's,
not the browser's, so both read it the same way)."""

import re

from playwright.sync_api import Page, expect
from test_profiles import NORDLICHT, TESTWERK
from top_bar import switch_profile

CHAT = "aside[aria-label='Chat with Vouch']"


def opened_chat(page: Page, url: str):
    """The page, then the chat opened: once it has read its kept conversation (and greeted, with
    none)."""
    with page.expect_response("**/api/overview"):
        page.goto(url + "/")
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    return page.locator(CHAT).get_by_role("log")


def asked(page: Page, message: str) -> None:
    """Sends `message`, and waits for the whole reply: the response event fires on the stream's
    headers, before the reply is in; the log is busy until it is (#240's lesson)."""
    page.get_by_role("textbox", name="Message to Vouch").fill(message)
    with page.expect_response("**/api/chat"):
        page.get_by_role("button", name="Send").click()
    log = page.locator(CHAT).get_by_role("log")
    expect(log.locator(".msg-lead").last).to_have_text(message)
    expect(log).to_have_attribute("aria-busy", "false")


def test_a_reload_shows_the_question_and_its_answer_again(page: Page, chat_url):
    log = opened_chat(page, chat_url)
    asked(page, "Where do I stand?")
    expect(log.locator(".msg-lead").last).to_have_text("Where do I stand?")
    answer = log.locator(".msg").last.inner_text()

    page.reload()
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    expect(log.locator(".msg-lead").last).to_have_text("Where do I stand?")
    expect(log.locator(".msg").last).to_have_text(answer)


def test_each_profile_keeps_its_own_conversation(page: Page, profiles_url):
    log = opened_chat(page, profiles_url)
    asked(page, "Is there evidence of encryption?")
    expect(log).to_contain_text("Is there evidence of encryption?")

    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        switch_profile(page, TESTWERK)
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    expect(log).not_to_contain_text("Is there evidence of encryption?")
    asked(page, "Where do I stand?")

    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        switch_profile(page, NORDLICHT)
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    expect(log).to_contain_text("Is there evidence of encryption?")
    expect(log).not_to_contain_text("Where do I stand?")


def test_new_conversation_empties_the_chat_and_a_reload_keeps_it_new(page: Page, chat_url):
    log = opened_chat(page, chat_url)
    asked(page, "An earlier question about 5.1.1")
    expect(log).to_contain_text("An earlier question about 5.1.1")

    with page.expect_response(
        lambda r: r.url.endswith("/api/chat/current") and r.request.method == "DELETE"
    ):
        page.locator(CHAT).get_by_role("button", name="New conversation").click()
    expect(log.locator(".msg")).to_have_count(0)

    page.reload()
    with page.expect_response("**/api/chat/current"):
        page.get_by_role("button", name="Ask Vouch").click()
    expect(log.locator(".msg").first).to_be_visible()  # the greeting of the new one
    expect(log).not_to_contain_text(re.compile("An earlier question"))
