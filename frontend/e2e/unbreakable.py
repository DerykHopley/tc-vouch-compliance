"""Free texts holding a token no hyphenation can break (#530's verify, #538), for the report's, the
working paper's and the chat's checks: each widened its column or box past its room while the text
broke words only at hyphens or spaces. And the checks' shared parts for a quote holding one."""

import json
import re
from collections.abc import Callable

from fonts import FONTS_SETTLED
from playwright.sync_api import Page, Route

UNBREAKABLE = {
    "link": "Siehe https://intranet.alpenwerk.example/isms/richtlinien/"
    "Protokollierungsrichtlinie_Version3_freigegeben.pdf für Details.",
    "signed link": "Kein Nachweis unter https://intranet.alpenwerk.example/isms/dokument?"
    "id=8f14e45fceea167a5a36dedd4bea2543&sig=c9a1b2d3e4f506178f14e45fceea167a5a36dedd hinterlegt.",
    "file name": "Siehe Zugriffsmatrix_Q3_2026_v2.xlsx dazu.",
    "compound": "The Informationssicherheitsmanagementsystem was signed off.",
}
"""A link, a signed link (a browser may break a link after a slash, not in its id and signature), a
file name under 31 characters, and a German compound in Nordlicht's English report (#530)."""

LONG_PATH = (
    "Abgelegt unter /srv/nachweise/zugriffssteuerung/"
    "Berechtigungskonzept_Rollenmatrix_Administratorkonten_2026_freigegeben.pdf zur Prüfung."
)
"""A quote's long file path (#538): Firefox may break it at its slashes, Chromium doesn't."""

LONG_QUOTES = (UNBREAKABLE["signed link"], UNBREAKABLE["link"], LONG_PATH)
"""The quotes #538's checks put in place of served ones: each widened its table or the chat's
message while a quote broke only at hyphens or spaces."""

LONG_QUOTES_LEAST = (
    "async ({ box, hide }) => {"
    + FONTS_SETTLED
    + f"  const texts = {json.dumps(LONG_QUOTES)}\n"
    + """  const quotes = [...document.querySelectorAll('blockquote')].filter((q) => texts.includes(q.textContent))
  const least = quotes.map((q) => {
    q.style.width = 'min-content'
    const wide = q.offsetWidth
    q.style.width = ''
    return wide
  })
  if (hide) quotes.forEach((q) => { q.style.display = 'none' })
  const rooms = quotes.map((q) => q.closest(box).clientWidth)
  quotes.forEach((q) => { q.style.display = '' })
  return quotes.map((q, i) => ({ wide: least[i], room: rooms[i] }))
}"""
)
"""Each LONG_QUOTES quote on the page, once the fonts have settled: its least width (`wide`: set at
min-content, which a table cell or a grid sizes to) and the width of the box it must fit in
(`room`: its nearest ancestor matching `box`, read with the long quotes hidden when `hide`, so a
column's width is its own and not one a quote widened). In one page load, so it doesn't compare
two layouts. `page.evaluate(LONG_QUOTES_LEAST, {"box": ..., "hide": ...})`."""


def with_long_quotes(
    page: Page, url: str | re.Pattern[str], profile: str, edit: Callable[[dict], None]
) -> None:
    """The response to `url` as served under `profile` (as_company's header: this route answers
    before its), after `edit` has changed it in place to hold LONG_QUOTES."""

    def patched(route: Route) -> None:
        headers = {**route.request.headers, "x-vouch-profile": profile}
        served = route.fetch(headers=headers).json()
        edit(served)
        route.fulfill(json=served)

    page.route(url, patched)
