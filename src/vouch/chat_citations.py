"""Quotes in a chat answer (#46), checked on the whole reply before it is shown.

The chat prompt asks the model to write every quote as `<cite doc="DOC_ID">exact words</cite>`.
Each such cite is checked against its document; one naming no document, or never closed,
fails unchecked. Any other quoted run of MIN_QUOTE_WORDS or more words, in any quotation marks,
is checked as citing no document: against what the tools returned this turn. A shorter one is
plain text ("the term “ISMS”"); prose without quotation marks is never a quote.

The check decides (vouch.chat_agent, ChatTools.check_quote); this module finds the quotes,
replaces each dropped one with QUOTE_REMOVED, logs it once and says why in a guard step.
"""

import logging
import re
from collections.abc import Callable

from vouch.citations import MIN_QUOTE_WORDS

log = logging.getLogger(__name__)

QUOTE_REMOVED = "[quote removed]"
"""What a dropped quote leaves in the answer, so the sentence it was in still reads as cut."""
UNCITED = "it isn't in what Vouch found for this question"
"""Why an uncited quote is dropped (ChatTools.check_quote)."""
NO_DOCUMENT = 'its cite names no document (<cite doc="…">)'
NOT_CLOSED = "its cite is never closed (</cite>)"

CITE = re.compile(r'<cite doc="([^"<>]+)">(.*?)</cite>', re.DOTALL)
"""A cite in the one form the chat prompt asks for; the frontend parses the same form."""
_CITE_OPENING = re.compile(r"<cite\b[^>]*>?")
_CITE_CLOSING = "</cite>"

PAIRS = {
    "“": "”“",
    "”": "”",
    "„": "“”",
    '"': '"',
    "‘": "’‘",
    "’": "’",
    "‚": "‘’",
    "'": "'",
    "«": "»",
    "»": "«»",
    "‹": "›",
    "›": "‹›",
    "「": "」",
    "『": "』",
    "〝": "〞〟",
    "＂": "＂",
    "`": "`",
}
"""Each opening quotation mark and the marks that may close it. A mark inside a word (company’s,
don't) is an apostrophe: it neither opens nor closes a quote."""
_OPENING = re.compile(r"(?<!\w)[" + re.escape("".join(PAIRS)) + "]")
_QUOTED = {
    opening: re.compile(
        rf"{re.escape(opening)}((?:[^{re.escape(closing)}\n]|\n(?!\s*\n)"
        rf"|(?<=\w)[{re.escape(closing)}](?=\w))+?)[{re.escape(closing)}](?!\w)"
    )
    for opening, closing in PAIRS.items()
}
"""The run a mark opens: to the first closing mark not inside a word, within one paragraph."""

CheckQuote = Callable[[str, str | None], str | None]
"""Checks a quote (its text, and the document id it cites, or None): None to keep it, or why
it is dropped."""


def dropped_step(doc_id: str | None, reason: str) -> str:
    """The guard step's sentence for a quote dropped from a chat answer."""
    where = f"from {doc_id}" if doc_id is not None else "citing no document"
    return f"Dropped a quote {where}: {reason}."


def checked_reply(reply: str, check: CheckQuote) -> tuple[str, list[str]]:
    """The reply with every quote that fails the check replaced by QUOTE_REMOVED, and a guard
    step for each one dropped."""
    out: list[str] = []
    steps: list[str] = []

    def dropped(doc_id: str | None, reason: str) -> None:
        log.warning("dropped a quote in a chat answer from %s (%s)", doc_id, reason)
        out.append(QUOTE_REMOVED)
        steps.append(dropped_step(doc_id, reason))

    pos = 0
    while (opening := _CITE_OPENING.search(reply, pos)) is not None:
        _uncited(reply[pos : opening.start()], check, out, dropped)
        cite = CITE.match(reply, opening.start())
        closing = reply.find(_CITE_CLOSING, opening.end())
        if cite is not None:
            reason = check(cite.group(2), cite.group(1))
            if reason is None:
                out.append(cite.group())
            else:
                dropped(cite.group(1), reason)
            pos = cite.end()
        elif closing < 0:
            named = re.match(r'<cite doc="([^"<>]+)">', opening.group())
            dropped(named.group(1) if named else None, NOT_CLOSED if named else NO_DOCUMENT)
            pos = len(reply)
        else:
            dropped(None, NO_DOCUMENT)
            pos = closing + len(_CITE_CLOSING)
    _uncited(reply[pos:], check, out, dropped)
    return "".join(out), steps


def _uncited(
    text: str, check: CheckQuote, out: list[str], dropped: Callable[[str | None, str], None]
) -> None:
    """Text outside any cite, onto `out`: each quoted run of MIN_QUOTE_WORDS or more words in it
    checked as citing no document."""
    pos = scan = 0
    while (opening := _OPENING.search(text, scan)) is not None:
        quoted = _QUOTED[opening.group()].match(text, opening.start())
        if quoted is None or len(quoted.group(1).split()) < MIN_QUOTE_WORDS:
            scan = quoted.end() if quoted is not None else opening.end()
            continue
        reason = check(quoted.group(1), None)
        out.append(text[pos : quoted.end()] if reason is None else text[pos : quoted.start()])
        if reason is not None:
            dropped(None, reason)
        pos = scan = quoted.end()
    out.append(text[pos:])
