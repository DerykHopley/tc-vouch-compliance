"""The web fonts, for the browser checks: wait for them to settle before measuring (#211, #512,
#516), or block them, as on a slow network where the page keeps its fallback fonts (#512)."""

import re

from playwright.sync_api import Page

FONTS_SETTLED = """
  await document.fonts.ready
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
"""
"""The opening statements of an async page function that measures layout: the fonts loaded (or
failed) and two frames drawn, so a late font can't rewrap what it then reads."""

WEB_FONTS = re.compile(r"^https://fonts\.(googleapis|gstatic)\.com/")


def without_web_fonts(page: Page) -> None:
    """The page never gets its web fonts (Google Fonts), so it is set in the fallback fonts."""
    page.route(WEB_FONTS, lambda route: route.abort())
