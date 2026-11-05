"""The guided chat (#45): Vouch greets the lead with their progress and the next step, and runs an
assessment from the chat, its steps in the chat and its verdict linked to the working paper.

On the chat's server (chat_url), on the fake models: no key, no paid call."""

import re

import pytest
from playwright.sync_api import Page, expect

CHAT = "aside[aria-label='Chat with Vouch']"


def open_chat(page: Page, url: str, width: int):
    page.set_viewport_size({"width": width, "height": 800})
    with page.expect_response("**/api/overview"):
        page.goto(url + "/")
    with page.expect_response("**/api/chat"):  # the greeting
        page.get_by_role("button", name="Ask Vouch").click()
    return page.locator(CHAT).get_by_role("log")


@pytest.mark.parametrize("width", [1400, 400])
def test_assess_in_the_chat_streams_its_steps_and_links_the_verdict_to_its_paper(
    page: Page, chat_url, width
):
    log = open_chat(page, chat_url, width)
    expect(log).to_contain_text("The next step is to")

    page.get_by_role("textbox", name="Message to Vouch").fill("Assess 5.1.1")
    page.get_by_role("button", name="Send").click()

    expect(log.locator(".msg-step").first).to_contain_text("Loaded control 5.1.1")
    expect(log).to_contain_text("The assessment of control 5.1.1 has finished")
    verdict = log.locator(".msg .chip")
    expect(verdict).to_have_count(1)
    link = log.get_by_role("link", name="Open the working paper")
    # The log follows the newest message: the verdict line, last, is in view without scrolling.
    expect(log.locator(".msg").last).to_contain_text("Open the working paper")
    expect(link).to_be_in_viewport()
    link.click()
    expect(page).to_have_url(re.compile(r"/controls/5\.1\.1$"))
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_text("Proposed", exact=True)).to_be_attached()


TOKEN = """(name) => {
  const probe = document.createElement('div')
  probe.style.color = `var(${name})`
  document.body.append(probe)
  const colour = getComputedStyle(probe).color
  probe.remove()
  return colour
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_working_paper_link_in_the_chat_is_drawn_in_the_navy_token_with_a_focus_ring(
    page: Page, chat_url, scheme
):
    page.emulate_media(color_scheme=scheme)
    log = open_chat(page, chat_url, 1400)
    field = page.get_by_role("textbox", name="Message to Vouch")
    field.fill("Assess 5.1.1")
    page.get_by_role("button", name="Send").click()
    link = log.get_by_role("link", name="Open the working paper")
    expect(link).to_be_visible()

    assert link.evaluate("el => getComputedStyle(el).color") == page.evaluate(TOKEN, "--navy")
    field.focus()
    page.keyboard.press("Shift")  # a keyboard user: focus is shown
    link.focus()
    ring = link.evaluate(
        "el => [getComputedStyle(el).outlineStyle, getComputedStyle(el).outlineColor]"
    )
    assert ring == ["solid", page.evaluate(TOKEN, "--navy")]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_returning_lead_is_greeted_with_their_progress_and_the_next_step(
    page: Page, chat_url, scheme
):
    page.emulate_media(color_scheme=scheme)
    started = page.request.post(f"{chat_url}/api/assessments", data={"control_id": "4.1.2"})
    assert started.ok and "event: result" in started.text()

    log = open_chat(page, chat_url, 1400)

    expect(log).to_contain_text(re.compile(r"You last worked on \d+ \w+ \d{4}\."))
    expect(log).to_contain_text("proposed and waiting for your review")
    # A waiting review comes first, in the base framework's order (4.1.2 before 5.1.1).
    expect(log).to_contain_text("The next step is to review control 4.1.2 on its working paper.")
    chip = log.get_by_role("link", name="Review 4.1.2")
    chip.click()
    expect(page).to_have_url(re.compile(r"/controls/4\.1\.2$"))
