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
