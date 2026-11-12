"""The click-burst guard's timing and a tap burst, for the browser checks (#527, #562, #563)."""

from playwright.sync_api import Locator, Page

DOUBLE_CLICK_MS = 500
"""The front end's DOUBLE_CLICK_MS: after a pointer click on an in-app ask's confirm, the chat's
Close or its pin toggle, a click anywhere within that long of the burst's last click presses
nothing, even once the ask has left the page. A person's next click comes after it."""


def tap_burst(page: Page, target: Locator, count: int) -> None:
    """`count` taps on the middle of where `target` is at the first tap: a finger taps the same
    spot while what was under it moves away. The page needs a touch context (has_touch)."""
    b = target.bounding_box()
    assert b is not None
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    for _ in range(count):
        page.touchscreen.tap(x, y)
