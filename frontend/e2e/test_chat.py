"""Vouch's floating chat (#44): its four states and the sheet, the keyboard, pinned beside the
page, the page it's on, a reply from the fake, and the chat endpoint down.

On their own server (chat_url), so their requests don't spend another suite's rate budget."""

import pytest
from playwright.sync_api import Page, expect

CHAT = "aside[aria-label='Chat with Vouch']"
SCROLLS_SIDEWAYS = (
    "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
)
TOKEN = """(name) => {
  const probe = document.createElement('div')
  probe.style.background = `var(${name})`
  document.body.append(probe)
  const colour = getComputedStyle(probe).backgroundColor
  probe.remove()
  return colour
}"""


def open_page(page: Page, url: str, width: int, path: str = "/") -> None:
    page.set_viewport_size({"width": width, "height": 800})
    with page.expect_response("**/api/overview"):
        page.goto(url + path)


def chat(page: Page):
    return page.locator(CHAT)


def box(page: Page, selector: str) -> dict:
    """Where the element sits once its animations (the bubble's rise) have finished."""
    element = page.locator(selector)
    element.evaluate("el => Promise.all(el.getAnimations().map((a) => a.finished))")
    found = element.bounding_box()
    assert found is not None, selector
    return found


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_each_state_is_reachable_and_drawn_in_the_theme(page: Page, chat_url, scheme):
    page.emulate_media(color_scheme=scheme)
    open_page(page, chat_url, 1400)
    sheet = page.evaluate(TOKEN, "--sheet")

    # Closed: the button, bottom right.
    ask = page.get_by_role("button", name="Ask Vouch")
    fab = box(page, ".fab")
    assert fab["x"] + fab["width"] > 1300 and fab["y"] + fab["height"] > 700

    # Bubble: floating bottom right, on the sheet colour, with a shadow.
    ask.click()
    expect(chat(page)).to_be_visible()
    bubble = box(page, CHAT)
    assert 1360 < bubble["x"] + bubble["width"] <= 1400 and bubble["width"] < 420
    style = chat(page).evaluate(
        "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).boxShadow]"
    )
    assert style[0] == sheet and style[1] != "none"

    # Enlarged: wider and taller, still floating.
    page.get_by_role("button", name="Make larger").click()
    enlarged = box(page, CHAT)
    assert enlarged["width"] > bubble["width"] + 200 and enlarged["height"] > bubble["height"]

    # Pinned: a column of about 400px at the right edge, full height, flat.
    pin = page.get_by_role("button", name="Pin beside the page")
    expect(pin).to_have_attribute("aria-pressed", "false")
    pin.click()
    expect(pin).to_have_attribute("aria-pressed", "true")
    pinned = box(page, CHAT)
    assert (
        pinned["x"] + pinned["width"] == 1400 and pinned["width"] == 400 and pinned["height"] == 800
    )
    assert chat(page).evaluate("el => getComputedStyle(el).boxShadow") == "none"
    assert chat(page).evaluate("el => getComputedStyle(el).backgroundColor") == sheet

    # Closed again: the button is back.
    page.get_by_role("button", name="Close the chat").click()
    expect(ask).to_be_focused()


def test_ctrl_or_cmd_k_opens_and_esc_closes_a_bubble_but_not_a_pinned_chat(page: Page, chat_url):
    open_page(page, chat_url, 1400)
    field = page.get_by_role("textbox", name="Message to Vouch")

    page.keyboard.press("Control+k")
    expect(chat(page)).to_be_visible()
    expect(field).to_be_focused()
    page.keyboard.press("Escape")
    expect(chat(page)).to_have_count(0)

    page.keyboard.press("Meta+k")
    expect(chat(page)).to_be_visible()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()
    page.keyboard.press("Escape")
    expect(chat(page)).to_be_visible()
    field.click()
    page.keyboard.press("Escape")
    expect(chat(page)).to_be_visible()


def test_pinned_the_page_and_chat_sit_side_by_side_with_no_sideways_scroll_at_1400px(
    page: Page, chat_url
):
    open_page(page, chat_url, 1400)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()

    pinned = box(page, CHAT)
    for part in ["header.top", "main"]:
        side = box(page, part)
        assert side["x"] + side["width"] <= pinned["x"], part
    assert page.evaluate(SCROLLS_SIDEWAYS) is False
    # The page under it still works: a control's card can be opened beside the chat.
    page.get_by_role("article", name="Control 4.1.2", exact=True).get_by_role("link").first.click()
    expect(page.get_by_role("region", name="Working paper")).to_be_visible()
    expect(chat(page)).to_contain_text("on 4.1.2 · Working paper")
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_pin_isnt_offered_at_900px_only_bubble_and_enlarged(page: Page, chat_url):
    open_page(page, chat_url, 900)
    page.get_by_role("button", name="Ask Vouch").click()
    expect(page.get_by_role("button", name="Pin beside the page")).to_have_count(0)

    page.get_by_role("button", name="Make larger").click()

    expect(chat(page)).to_have_class("bubble enlarged")
    expect(page.get_by_role("button", name="Pin beside the page")).to_have_count(0)
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_a_chat_pinned_at_1400px_falls_back_to_enlarged_at_900px_and_the_page_takes_the_width(
    page: Page, chat_url
):
    open_page(page, chat_url, 1400)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()

    page.set_viewport_size({"width": 900, "height": 800})

    expect(chat(page)).to_have_class("bubble enlarged")
    assert box(page, "main")["width"] == 900  # no room kept for a column that is gone
    page.keyboard.press("Escape")  # enlarged now: Esc closes it
    expect(chat(page)).to_have_count(0)


def test_a_chat_pinned_at_1400px_is_a_sheet_at_400px_and_esc_closes_it(page: Page, chat_url):
    open_page(page, chat_url, 1400)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()

    page.set_viewport_size({"width": 400, "height": 800})

    assert box(page, CHAT) == {"x": 0, "y": 0, "width": 400, "height": 800}
    page.keyboard.press("Escape")
    expect(chat(page)).to_have_count(0)


def test_at_400px_the_chat_is_a_full_screen_sheet_without_pin_or_enlarge(page: Page, chat_url):
    open_page(page, chat_url, 400)
    page.get_by_role("button", name="Ask Vouch").click()

    assert box(page, CHAT) == {"x": 0, "y": 0, "width": 400, "height": 800}
    expect(page.get_by_role("button", name="Make larger")).to_have_count(0)
    expect(page.get_by_role("button", name="Pin beside the page")).to_have_count(0)
    expect(page.get_by_role("button", name="Send")).to_be_visible()
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_the_header_names_the_page_and_the_control_on_the_working_paper(page: Page, chat_url):
    open_page(page, chat_url, 1400)
    page.get_by_role("button", name="Ask Vouch").click()
    expect(chat(page).locator(".page-context")).to_have_text("on Dossier")
    page.get_by_role("button", name="Close the chat").click()

    open_page(page, chat_url, 1400, "/controls/4.1.2")
    expect(page.get_by_role("region", name="Working paper")).to_be_visible()
    page.get_by_role("button", name="Ask Vouch").click()
    expect(chat(page).locator(".page-context")).to_have_text("on 4.1.2 · Working paper")


def test_a_message_gets_vouchs_formal_reply(page: Page, chat_url):
    open_page(page, chat_url, 1400)
    page.get_by_role("button", name="Ask Vouch").click()
    send = page.get_by_role("button", name="Send")
    expect(send).to_be_disabled()

    field = page.get_by_role("textbox", name="Message to Vouch")
    field.fill("Where do I stand?")
    with page.expect_response("**/api/chat"):
        send.click()

    log = chat(page).get_by_role("log")
    expect(log).to_contain_text("Where do I stand?")
    expect(log).to_contain_text("Thank you for your question.")
    expect(field).to_have_value("")


def test_with_the_chat_endpoint_down_a_message_shows_and_the_typed_text_stays(page: Page, chat_url):
    page.route("**/api/chat", lambda route: route.abort())
    open_page(page, chat_url, 1400)
    page.get_by_role("button", name="Ask Vouch").click()
    field = page.get_by_role("textbox", name="Message to Vouch")
    field.fill("Where do I stand?")
    page.get_by_role("button", name="Send").click()

    expect(chat(page).get_by_role("log")).to_contain_text("Vouch could not be reached")
    expect(field).to_have_value("Where do I stand?")
    expect(page.get_by_role("button", name="Send")).to_be_enabled()


@pytest.mark.parametrize("width", [1400, 400])
def test_scrolled_to_the_end_the_closed_button_hides_nothing(page: Page, chat_url, width):
    """From #40's verify: mid-scroll a floating button always sits over something, but the page
    leaves room under its last content, so everything can be scrolled clear of it."""
    open_page(page, chat_url, width)
    expect(page.locator(".quote, .dossier .empty").first).to_be_visible()
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    fab = box(page, ".fab")
    covered = page.evaluate(
        """(fab) => [...document.querySelectorAll('main *')].filter((el) => {
          if (el.children.length) return false  // leaves only: text, links, chips
          const r = el.getBoundingClientRect()
          return r.width && r.height && r.left < fab.x + fab.width && r.right > fab.x
            && r.top < fab.y + fab.height && r.bottom > fab.y
        }).map((el) => el.textContent.trim().slice(0, 40))""",
        fab,
    )
    assert covered == []
