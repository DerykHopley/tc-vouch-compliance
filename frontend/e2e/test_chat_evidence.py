"""The chat's answers about the evidence (#46): each quote it cites, checked, is shown in the
working paper's Citation style (serif italic, brass rule) with its document id.

On the chat's server (chat_url), on the fake models: no key, no paid call."""

import pytest
from playwright.sync_api import Page, expect

CHAT = "aside[aria-label='Chat with Vouch']"
TOKEN = """(name) => {
  const probe = document.createElement('div')
  probe.style.color = `var(${name})`
  document.body.append(probe)
  const colour = getComputedStyle(probe).color
  probe.remove()
  return colour
}"""
SCROLLS_SIDEWAYS = (
    "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1400, 400])
def test_a_cited_quote_shows_as_a_quote_with_its_document_id(page: Page, chat_url, width, scheme):
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 800})
    page.request.delete(
        f"{chat_url}/api/chat/current"
    )  # a fresh chat: the server keeps the last (#251)
    with page.expect_response("**/api/overview"):
        page.goto(chat_url + "/")
    with page.expect_response("**/api/chat"):  # the greeting
        page.get_by_role("button", name="Ask Vouch").click()
    log = page.locator(CHAT).get_by_role("log")

    page.get_by_role("textbox", name="Message to Vouch").fill(
        "Is there evidence of full-disk encryption?"
    )
    with page.expect_response("**/api/chat") as answered:
        page.get_by_role("button", name="Send").click()
    answered.value.finished()

    quote = log.locator("figure.cite").filter(has_text="Every company laptop uses full-disk")
    expect(quote.locator("blockquote")).to_contain_text("encryption with AES-256.")
    expect(quote.locator("figcaption")).to_have_text("encryption-standard")
    expect(log).not_to_contain_text("<cite")
    style = quote.locator("blockquote").evaluate(
        "el => { const s = getComputedStyle(el);"
        " return [s.fontStyle, s.borderLeftColor, s.borderLeftStyle] }"
    )
    assert style == ["italic", page.evaluate(TOKEN, "--brass"), "solid"]
    assert not page.evaluate(SCROLLS_SIDEWAYS)
