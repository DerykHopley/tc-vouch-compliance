"""The guided chat (#45): Vouch greets the lead with their progress and the next step, and runs an
assessment from the chat, its steps in the chat and its verdict linked to the working paper.

On the chat's server (chat_url), on the fake models: no key, no paid call. The chat's action links
(#176) are one 44px pill: "Open the working paper" and both suggestions."""

import json
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


# The chat's action links (#176): one 44px pill (design.md, Accessibility floor: touch targets at
# least 44 by 44px).
LINES = """el => {
  const range = document.createRange()
  range.selectNodeContents(el)
  return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size
}"""
PILL = """el => {
  const s = getComputedStyle(el)
  return {
    height: el.getBoundingClientRect().height, background: s.backgroundColor, border: s.borderTopColor,
    borderWidth: s.borderTopWidth, radius: s.borderTopLeftRadius, padding: s.padding, colour: s.color,
    weight: s.fontWeight, size: s.fontSize, underline: s.textDecorationLine,
  }
}"""
RING = "el => [getComputedStyle(el).outlineStyle, getComputedStyle(el).outlineColor]"


def assess_in_the_chat(page: Page, url: str, width: int):
    log = open_chat(page, url, width)
    page.get_by_role("textbox", name="Message to Vouch").fill("Assess 5.1.1")
    page.get_by_role("button", name="Send").click()
    link = log.get_by_role("link", name="Open the working paper")
    expect(link).to_be_visible()
    return link


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1400, 400])
def test_open_the_working_paper_is_a_44px_pill_on_one_line(page: Page, chat_url, width, scheme):
    page.emulate_media(color_scheme=scheme)
    link = assess_in_the_chat(page, chat_url, width)

    box = link.bounding_box()
    assert box and box["height"] >= 44 and box["width"] >= 44, box
    assert link.evaluate(LINES) == 1
    assert link.evaluate("el => getComputedStyle(el).textDecorationLine") == "none"


def reply(*events: tuple[str, dict]) -> str:
    """A chat reply as the API streams it: server-sent events, ending with `done`."""
    blocks = [("thread", {"thread_id": "t-176"}), *events, ("done", {})]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in blocks)


def greet_with(page: Page, url: str, action: str, sent: list[dict]):
    """Opens the chat on a greeting whose next step is `action` on 1.1.1; a message gets a short
    reply. Each chat request's body lands in `sent`. Returns the suggestion."""

    def answer(route):
        body = route.request.post_data_json
        sent.append(body)
        events = (
            [("token", {"text": "On it."})]
            if "message" in body
            else [("token", {"text": "Hello."}), ("suggestion", {"control_id": "1.1.1", "action": action})]
        )
        route.fulfill(status=200, content_type="text/event-stream", body=reply(*events))

    page.route("**/api/chat", answer)
    return open_chat(page, url, 1400).locator(".suggestion")


def test_the_assess_and_review_suggestions_share_one_44px_pill(page: Page, chat_url):
    styles = {}
    for action, words in (("assess", "Assess 1.1.1"), ("review", "Review 1.1.1")):
        chip = greet_with(page, chat_url, action, [])
        expect(chip).to_have_text(words)
        styles[action] = chip.evaluate(PILL)
        page.unroute("**/api/chat")

    assert styles["assess"] == styles["review"]
    assert styles["review"]["height"] >= 44
    assert styles["review"]["underline"] == "none"


def keyboard_to(page: Page, chip):
    """From the message field, Shift+Tab back to the log's last control, as a keyboard user would."""
    expect(chip).to_be_visible()
    page.get_by_role("textbox", name="Message to Vouch").focus()
    page.keyboard.press("Shift+Tab")
    expect(chip).to_be_focused()
    assert chip.evaluate(RING) == ["solid", page.evaluate(TOKEN, "--navy")]


def test_from_the_keyboard_the_assess_suggestion_shows_the_focus_ring_and_sends_its_message(
    page: Page, chat_url
):
    sent: list[dict] = []
    chip = greet_with(page, chat_url, "assess", sent)
    keyboard_to(page, chip)

    page.keyboard.press("Enter")
    expect(page.locator(CHAT).get_by_role("log")).to_contain_text("On it.")
    assert sent[-1]["message"] == "Assess 1.1.1"


def test_from_the_keyboard_the_review_suggestion_shows_the_focus_ring_and_opens_its_paper(
    page: Page, chat_url
):
    chip = greet_with(page, chat_url, "review", [])
    keyboard_to(page, chip)

    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/controls/1\.1\.1$"))


def test_from_the_keyboard_open_the_working_paper_opens_its_paper(page: Page, chat_url):
    link = assess_in_the_chat(page, chat_url, 1400)
    page.keyboard.press("Shift")  # a keyboard user (the next-step chip may follow it in the log)
    link.focus()
    assert link.evaluate(RING) == ["solid", page.evaluate(TOKEN, "--navy")]

    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/controls/5\.1\.1$"))
