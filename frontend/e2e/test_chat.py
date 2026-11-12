"""Vouch's floating chat (#44): its four states and the sheet, the keyboard, pinned beside the
page, the page it's on, a reply from the fake, a reply's numbered lists, and the chat endpoint down.

On their own server (chat_url), so their requests don't spend another suite's rate budget."""

import json

import pytest
from click_burst import DOUBLE_CLICK_MS
from dossier_cards import click_on_card
from fonts import FONTS_SETTLED
from playwright.sync_api import Page, Route, expect
from touch_targets import small_targets
from unbreakable import LONG_QUOTES, LONG_QUOTES_LEAST

# Wide enough to pin the chat at any width from 360px to 720px: pinning is offered from 1402px, and the
# page keeps 1042px beside it (chatWidth.ts, #465).
WIDE = 1800
CHAT = "aside[aria-label='Chat with Vouch']"
HANDLE = "[role=separator][aria-label='Chat width']"
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


def pin(page: Page) -> None:
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()
    expect(chat(page)).to_have_class("bubble pinned")


def drag_handle(page: Page, dx: float) -> None:
    """Drags the pinned chat's resize handle `dx` px with the mouse (negative: to the left)."""
    handle = box(page, HANDLE)
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y, steps=8)
    page.mouse.up()


def chat_width(page: Page) -> float:
    return box(page, CHAT)["width"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_each_state_is_reachable_and_drawn_in_the_theme(page: Page, chat_url, scheme):
    page.emulate_media(color_scheme=scheme)
    open_page(page, chat_url, WIDE)
    sheet = page.evaluate(TOKEN, "--sheet")

    # Closed: the button, bottom right.
    ask = page.get_by_role("button", name="Ask Vouch")
    fab = box(page, ".fab")
    assert fab["x"] + fab["width"] > WIDE - 100 and fab["y"] + fab["height"] > 700

    # Bubble: floating bottom right, on the sheet colour, with a shadow.
    ask.click()
    expect(chat(page)).to_be_visible()
    bubble = box(page, CHAT)
    assert WIDE - 40 < bubble["x"] + bubble["width"] <= WIDE and bubble["width"] < 420
    style = chat(page).evaluate(
        "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).boxShadow]"
    )
    assert style[0] == sheet and style[1] != "none"

    # Enlarged: wider and taller, still floating.
    page.get_by_role("button", name="Make larger").click()
    enlarged = box(page, CHAT)
    assert enlarged["width"] > bubble["width"] + 200 and enlarged["height"] > bubble["height"]

    # Pinned: a column of 480px (#231) at the right edge, full height, flat.
    pin = page.get_by_role("button", name="Pin beside the page")
    expect(pin).to_have_attribute("aria-pressed", "false")
    pin.click()
    expect(pin).to_have_attribute("aria-pressed", "true")
    pinned = box(page, CHAT)
    assert (
        pinned["x"] + pinned["width"] == WIDE and pinned["width"] == 480 and pinned["height"] == 800
    )
    assert chat(page).evaluate("el => getComputedStyle(el).boxShadow") == "none"
    assert chat(page).evaluate("el => getComputedStyle(el).backgroundColor") == sheet

    # Closed again: the button is back. A person's next click comes after the pin's burst (#563).
    page.wait_for_timeout(DOUBLE_CLICK_MS)
    page.get_by_role("button", name="Close the chat").click()
    expect(ask).to_be_focused()


def test_ctrl_or_cmd_k_opens_and_esc_closes_a_bubble_but_not_a_pinned_chat(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
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


def test_pinned_the_page_and_chat_sit_side_by_side_with_no_sideways_scroll(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()

    pinned = box(page, CHAT)
    for part in ["header.top", "main"]:
        side = box(page, part)
        assert side["x"] + side["width"] <= pinned["x"], part
    assert page.evaluate(SCROLLS_SIDEWAYS) is False
    # The page under it still works: a click on a card's question opens its paper beside the chat (#231),
    # after the pin's burst (#563).
    page.wait_for_timeout(DOUBLE_CLICK_MS)
    click_on_card(page, "4.1.2")
    expect(page.get_by_role("region", name="Working paper")).to_be_visible()
    expect(chat(page)).to_contain_text("on 4.1.2 · Working paper")
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_pin_isnt_offered_at_900px_only_bubble_and_enlarged(page: Page, chat_url):
    open_page(page, chat_url, 900)
    page.get_by_role("button", name="Ask Vouch").click()
    expect(page.get_by_role("button", name="Pin beside the page")).to_have_count(0)
    expect(page.locator(HANDLE)).to_have_count(0)  # no resize handle on the bubble (#231)

    page.get_by_role("button", name="Make larger").click()

    expect(chat(page)).to_have_class("bubble enlarged")
    expect(page.get_by_role("button", name="Pin beside the page")).to_have_count(0)
    expect(page.locator(HANDLE)).to_have_count(0)
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_a_pinned_chat_falls_back_to_enlarged_below_1402px_and_the_page_takes_the_width(
    page: Page, chat_url
):
    open_page(page, chat_url, WIDE)
    pin(page)
    drag_handle(page, -100)  # pinned at a dragged width (#231)

    page.set_viewport_size({"width": 1401, "height": 800})

    expect(chat(page)).to_have_class("bubble enlarged")
    expect(page.locator(HANDLE)).to_have_count(0)
    # No room kept for a column that is gone: the page takes the whole width again.
    assert page.evaluate("getComputedStyle(document.body).paddingRight") == "0px"
    assert page.evaluate("document.documentElement.dataset.chat") is None
    assert box(page, "header.top")["width"] == 1401
    page.keyboard.press("Escape")  # enlarged now: Esc closes it
    expect(chat(page)).to_have_count(0)


def test_a_pinned_chat_is_a_sheet_at_400px_and_esc_closes_it(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
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
    expect(page.locator(HANDLE)).to_have_count(0)
    expect(page.get_by_role("button", name="Make larger")).to_have_count(0)
    expect(page.get_by_role("button", name="Pin beside the page")).to_have_count(0)
    expect(page.get_by_role("button", name="Send")).to_be_visible()
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_dragging_the_handle_200px_left_makes_the_pinned_chat_680px_and_the_page_gives_way(
    page: Page, chat_url
):
    """#231: the pinned chat's width follows a drag on its left edge, and the page makes room."""
    open_page(page, chat_url, WIDE)
    pin(page)
    assert chat_width(page) == 480

    drag_handle(page, -200)

    assert chat_width(page) == 680
    expect(page.locator(HANDLE)).to_have_attribute("aria-valuenow", "680")
    pinned = box(page, CHAT)
    for part in ["header.top", "main"]:
        side = box(page, part)
        assert side["x"] + side["width"] <= pinned["x"], part
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_a_drag_past_either_end_stops_at_720px_and_360px(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    pin(page)

    drag_handle(page, -600)
    assert chat_width(page) == 720
    main = box(page, "main")
    assert main["x"] + main["width"] <= WIDE - 720
    assert page.evaluate(SCROLLS_SIDEWAYS) is False

    drag_handle(page, 600)
    assert chat_width(page) == 360


def test_the_handle_takes_keyboard_focus_with_a_visible_ring_and_a_tall_hit_area(
    page: Page, chat_url
):
    open_page(page, chat_url, WIDE)
    pin(page)  # focus is on the pin button; the handle is the chat's first stop, just before it
    page.keyboard.press("Shift+Tab")
    handle = page.get_by_role("separator", name="Chat width")
    expect(handle).to_be_focused()
    assert handle.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
    assert box(page, HANDLE)["height"] >= 44
    page.keyboard.press("ArrowLeft")
    assert chat_width(page) == 512
    # Pinned, every control in the chat and on the page keeps the touch-target floor.
    assert small_targets(page) == []


def test_a_chosen_width_survives_unpin_and_pin_a_working_paper_and_a_reload(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    pin(page)
    drag_handle(page, -120)
    assert chat_width(page) == 600

    pin_button = page.get_by_role("button", name="Pin beside the page")
    # A person's next click comes after the burst of the pin and the drag's click (#563).
    page.wait_for_timeout(DOUBLE_CLICK_MS)
    pin_button.click()  # unpinned: enlarged
    expect(chat(page)).to_have_class("bubble enlarged")
    page.wait_for_timeout(DOUBLE_CLICK_MS)
    pin_button.click()
    assert chat_width(page) == 600

    page.wait_for_timeout(DOUBLE_CLICK_MS)
    click_on_card(page, "4.1.2")
    expect(page.get_by_role("region", name="Working paper")).to_be_visible()
    assert chat_width(page) == 600

    page.reload()
    expect(page.get_by_role("region", name="Working paper")).to_be_visible()
    pin(page)
    assert chat_width(page) == 600
    assert page.evaluate(SCROLLS_SIDEWAYS) is False


def test_a_drag_shows_as_it_moves_and_is_kept_only_on_release(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    pin(page)
    handle = box(page, HANDLE)
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 100, y, steps=4)
    assert chat_width(page) == 580  # shown while dragging
    assert page.evaluate("() => localStorage.getItem('vouch.chatWidth')") is None
    page.mouse.up()
    assert page.evaluate("() => localStorage.getItem('vouch.chatWidth')") == "580"
    expect(page.locator(HANDLE)).to_have_attribute("aria-valuenow", "580")


def test_a_click_on_the_handle_in_a_narrow_window_keeps_the_remembered_width(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    pin(page)
    drag_handle(page, -240)
    assert chat_width(page) == 720
    page.set_viewport_size({"width": 1500, "height": 900})
    expect(page.locator(HANDLE)).to_have_attribute("aria-valuenow", "458")
    drag_handle(page, 0)  # a plain click: down and up without moving
    page.set_viewport_size({"width": WIDE, "height": 900})
    expect(page.locator(HANDLE)).to_have_attribute("aria-valuenow", "720")
    assert page.evaluate("() => localStorage.getItem('vouch.chatWidth')") == "720"


def test_new_conversation_stays_reachable_at_the_narrowest_and_widest_pinned_chat(
    page: Page, chat_url
):
    open_page(page, chat_url, WIDE)
    pin(page)
    for dx, width in ((240, 360), (-360, 720)):  # 480 -> 360, then -> 720
        drag_handle(page, dx)
        assert chat_width(page) == width
        new = chat(page).get_by_role("button", name="New conversation")
        expect(new).to_be_in_viewport(ratio=1)
        new.click()  # reachable: a real click lands on it
        expect(new).to_be_visible()


def test_only_the_primary_button_drags_the_handle(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    pin(page)
    handle = box(page, HANDLE)
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down(button="right")
    page.mouse.move(x - 100, y, steps=4)
    page.mouse.up(button="right")
    assert chat_width(page) == 480


def test_the_page_beside_the_pinned_chat_keeps_its_pointer_up_to_the_chats_edge(
    page: Page, chat_url
):
    open_page(page, chat_url, WIDE)
    pin(page)
    edge = box(page, CHAT)["x"]
    at = page.evaluate(
        """(x) => { const el = document.elementFromPoint(x, 400)
          return [el.closest('aside') !== null, getComputedStyle(el).cursor] }""",
        edge - 2,
    )
    assert at[0] is False and at[1] != "col-resize"


def test_the_header_names_the_page_and_the_control_on_the_working_paper(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    page.get_by_role("button", name="Ask Vouch").click()
    expect(chat(page).locator(".page-context")).to_have_text("on Dossier")
    page.get_by_role("button", name="Close the chat").click()

    open_page(page, chat_url, WIDE, "/controls/4.1.2")
    expect(page.get_by_role("region", name="Working paper")).to_be_visible()
    page.get_by_role("button", name="Ask Vouch").click()
    expect(chat(page).locator(".page-context")).to_have_text("on 4.1.2 · Working paper")


def test_on_the_readiness_report_the_header_names_the_report(page: Page, chat_url):
    page.set_viewport_size({"width": 1400, "height": 800})
    with page.expect_response("**/api/report"):
        page.goto(chat_url + "/report")
    page.get_by_role("button", name="Ask Vouch").click()
    expect(chat(page).locator(".page-context")).to_have_text("on Report")


def test_a_message_gets_vouchs_formal_reply(page: Page, chat_url):
    open_page(page, chat_url, WIDE)
    page.get_by_role("button", name="Ask Vouch").click()
    send = page.get_by_role("button", name="Send")
    expect(send).to_be_disabled()

    field = page.get_by_role("textbox", name="Message to Vouch")
    field.fill("What does TISAX ask of us?")
    with page.expect_response("**/api/chat"):
        send.click()

    log = chat(page).get_by_role("log")
    expect(log).to_contain_text("What does TISAX ask of us?")
    expect(log).to_contain_text("Thank you for your question.")
    expect(field).to_have_value("")


def fulfil_reply(route: Route, thread_id: str, text: str) -> None:
    """Answers a routed chat message with `text` as Vouch's whole reply, in the chat's
    server-sent events: no model call."""
    events = [("thread", {"thread_id": thread_id}), ("token", {"text": text}), ("done", {})]
    sse = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    route.fulfill(status=200, content_type="text/event-stream", body=sse)


# Two list shapes a model writes (#223): numbers across blank lines, and an item's indented sub-bullets.
LISTS = {
    "blank lines": "Schritte:\n1. a\n\n2. b\n\n3. c",
    "sub-bullets": "Schritte:\n1. Erfassen\n   - Datum\n2. Bewerten",
}


def test_a_numbered_list_stays_one_list_across_blank_lines_and_sub_bullets(page: Page, chat_url):
    """Each message is answered with the list shape it names, as a routed reply: no model call."""

    def answer(route: Route) -> None:
        body = route.request.post_data_json
        fulfil_reply(route, "t-223", LISTS.get(body.get("message", ""), "Hello."))

    page.route("**/api/chat", answer)
    open_page(page, chat_url, WIDE)
    page.get_by_role("button", name="Ask Vouch").click()
    field = page.get_by_role("textbox", name="Message to Vouch")
    replies = chat(page).get_by_role("log").locator(".msg")

    for message in LISTS:
        field.fill(message)
        with page.expect_response("**/api/chat"):
            page.get_by_role("button", name="Send").click()
        reply = replies.filter(has_text="Schritte:").last
        expect(reply.locator("ol")).to_have_count(1)
        if message == "blank lines":
            expect(reply.locator("ol > li")).to_have_text(["a", "b", "c"])
        else:
            items = reply.locator("ol > li")
            expect(items).to_have_count(2)
            expect(items.first.locator("ul.prose-list > li")).to_have_text(["Datum"])
            expect(items.last).to_have_text("Bewerten")


LOG_WIDTH = (
    "async (log) => {"
    + FONTS_SETTLED
    + "  return { wide: log.scrollWidth, room: log.clientWidth }\n}"
)
"""The chat log's width against its box's, once the fonts have settled."""


@pytest.mark.parametrize("width", [1440, 390])
def test_a_quote_holding_a_long_link_or_path_stays_inside_the_chat(page: Page, chat_url, width):
    """#538: a quote broke only at hyphens or spaces (#511), overriding the message's break
    anywhere, so a cited link, signed link or path widened the message past the bubble and its log
    scrolled sideways (by 200-285px). Answered as a routed reply: no model call."""
    question = "Wo liegen die Nachweise?"
    cited = "Die Nachweise:\n" + "\n".join(
        f'- <cite doc="logging-policy">{quote}</cite>' for quote in LONG_QUOTES
    )

    def answer(route: Route) -> None:  # the chat's opening greeting is a POST too: a plain hello
        asked = route.request.post_data_json.get("message") == question
        fulfil_reply(route, "t-538", cited if asked else "Hello.")

    page.route("**/api/chat", answer)
    open_page(page, chat_url, width)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("textbox", name="Message to Vouch").fill(question)
    with page.expect_response("**/api/chat"):
        page.get_by_role("button", name="Send").click()

    log = chat(page).get_by_role("log")
    for quote in LONG_QUOTES:
        expect(log.locator("blockquote").filter(has_text=quote)).to_have_count(1)
    quotes = page.evaluate(LONG_QUOTES_LEAST, {"box": ".bubble-log", "hide": False})
    assert len(quotes) == len(LONG_QUOTES), quotes
    assert all(q["wide"] <= q["room"] for q in quotes), (width, quotes)
    measured = log.evaluate(LOG_WIDTH)
    assert measured["wide"] <= measured["room"], (width, measured)


def test_with_the_chat_endpoint_down_a_message_shows_and_the_typed_text_stays(page: Page, chat_url):
    page.route("**/api/chat", lambda route: route.abort())
    open_page(page, chat_url, WIDE)
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


def test_a_developer_sets_the_chat_limit_and_at_it_the_chat_says_so_in_place_of_a_reply(
    page: Page, chat_url
):
    # Issue 628: the limit is the server's, shared, so it's put back after.
    before = page.request.get(f"{chat_url}/api/developer/settings").json()["chat_daily_limit"]
    page.goto(f"{chat_url}/developer")
    settings = page.get_by_role("region", name="Developer settings")
    try:
        limit = settings.get_by_role("textbox", name="Chat messages per day")
        limit.fill("1")
        with page.expect_response("**/api/developer/chat-daily-limit") as answered:
            limit.press("Enter")
        assert answered.value.status == 200
        expect(settings).to_contain_text("1 chat message per user per day (UTC)")

        open_page(page, chat_url, WIDE)
        page.get_by_role("button", name="Ask Vouch").click()
        field = page.get_by_role("textbox", name="Message to Vouch")
        for message in ("First question", "Second question"):  # the second is over any count
            field.fill(message)
            with page.expect_response("**/api/chat"):
                page.get_by_role("button", name="Send").click()

        expect(chat(page).get_by_role("log")).to_contain_text(
            "You have reached today's limit of 1 chat message. It resets at 00:00 UTC on"
        )
    finally:
        reset = page.request.put(
            f"{chat_url}/api/developer/chat-daily-limit", data={"chat_daily_limit": before}
        )
        assert reset.status == 200, reset.text()
