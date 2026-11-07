"""A Dossier control card, for the browser checks that open a control from it."""

from playwright.sync_api import Locator, Page


def card(page: Page, control_id: str) -> Locator:
    return page.get_by_role("article", name=f"Control {control_id}", exact=True)


def click_on_card(page: Page, control_id: str, part: str = ".control-title", **click) -> None:
    """A click on a part of a card (its question by default), as a mouse makes it. The card's one
    link is stretched over the whole card by design (#231), so Playwright calls the part covered:
    force skips that check only; the mouse still clicks at the part's centre, where the link takes
    the click. `click` passes on (modifiers, button)."""
    card(page, control_id).locator(part).click(force=True, **click)


def first_quote(page: Page) -> tuple[Locator, str]:
    """The overview's first evidence quote card and the control it was cited for (#311): its one
    link, "for <control> <requirement>", is stretched over the card as a control card's is."""
    quote = page.get_by_role("region", name="What the evidence says").get_by_role("article").first
    href = quote.get_by_role("link").get_attribute("href")
    assert href and href.startswith("/controls/"), href
    return quote, href.removeprefix("/controls/")
