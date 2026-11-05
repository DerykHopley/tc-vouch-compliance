"""Quotes in a chat answer (#46): the whole reply is checked before it is shown. Each
`<cite doc="…">…</cite>` is checked against its document; any other quoted run of
MIN_QUOTE_WORDS or more words is checked against what the tools returned. One that fails is
replaced with QUOTE_REMOVED, with a guard step."""

import logging

import pytest

from vouch.chat_citations import NO_DOCUMENT, NOT_CLOSED, QUOTE_REMOVED, checked_reply, dropped_step

NOT_FOUND = "the quote isn't in that document"
QUOTE = "every laptop is encrypted daily"


class Checks:
    """A quote check that keeps only `kept` quotes, noting what it was asked: (quote, doc_id)."""

    def __init__(self, *kept: str) -> None:
        self.kept = kept
        self.asked: list[tuple[str, str | None]] = []

    def __call__(self, quote: str, doc_id: str | None) -> str | None:
        self.asked.append((quote, doc_id))
        return None if quote in self.kept else NOT_FOUND


def checked(reply: str, *kept: str) -> tuple[str, list[str], Checks]:
    check = Checks(*kept)
    text, steps = checked_reply(reply, check)
    return text, steps, check


# <cite doc="…">: checked against its document


def test_a_cite_is_checked_against_its_document_and_kept_as_written_when_it_is_there():
    reply = f'It says <cite doc="encryption-standard">{QUOTE}</cite>, as noted.'

    text, steps, check = checked(reply, QUOTE)

    assert check.asked == [(QUOTE, "encryption-standard")]
    assert (text, steps) == (reply, [])


def test_an_invented_cite_is_removed_with_a_guard_step():
    reply = f'It says <cite doc="encryption-standard">{QUOTE}</cite>, as noted.'

    text, steps, _ = checked(reply)

    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step("encryption-standard", NOT_FOUND)]


def test_a_cite_over_several_lines_is_checked_whole():
    _, _, check = checked('<cite doc="doc">every laptop\nis encrypted daily</cite>')

    assert check.asked == [("every laptop\nis encrypted daily", "doc")]


@pytest.mark.parametrize(
    "cite",
    [
        f"<cite>{QUOTE}</cite>",
        f'<cite doc="">{QUOTE}</cite>',
        f"<cite doc='doc'>{QUOTE}</cite>",  # not the form the prompt asks for
        f'<cite source="doc">{QUOTE}</cite>',
    ],
)
def test_a_cite_naming_no_document_is_removed_unchecked(cite):
    text, steps, check = checked(f"It says {cite} here.", QUOTE)

    assert check.asked == []
    assert text == f"It says {QUOTE_REMOVED} here."
    assert steps == [dropped_step(None, NO_DOCUMENT)]


def test_a_cite_never_closed_is_removed_to_the_end_of_the_reply():
    text, steps, _ = checked(f'It says <cite doc="doc">{QUOTE}. And more.', QUOTE)

    assert text == f"It says {QUOTE_REMOVED}"
    assert steps == [dropped_step("doc", NOT_CLOSED)]


def test_each_drop_is_logged_once(caplog):
    with caplog.at_level(logging.WARNING):
        checked(f'<cite doc="doc">{QUOTE}</cite> and “{QUOTE} again”.')

    dropped = [r.getMessage() for r in caplog.records if "dropped a quote" in r.getMessage()]
    assert len(dropped) == 2


# Any other quoted run of MIN_QUOTE_WORDS or more words: checked against the tools' results


@pytest.mark.parametrize(
    "quoted",
    [
        f"“{QUOTE}”",
        f"„{QUOTE}“",  # German
        f"‚{QUOTE}‘",
        f'"{QUOTE}"',
        f"‘{QUOTE}’",
        f"'{QUOTE}'",
        f"”{QUOTE}”",  # a closing mark used to open
        f"«{QUOTE}»",
        f"»{QUOTE}«",
        f"‹{QUOTE}›",
        f"「{QUOTE}」",
        f"『{QUOTE}』",
        f"`{QUOTE}`",
    ],
)
def test_a_quoted_run_in_any_marks_is_checked_as_citing_no_document(quoted):
    text, steps, check = checked(f"It says {quoted}, as noted.")

    assert check.asked == [(QUOTE, None)]
    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_a_quoted_run_the_tools_returned_is_kept():
    reply = f"It says “{QUOTE}”, as noted."

    assert checked(reply, QUOTE)[:2] == (reply, [])


def test_a_short_quoted_phrase_is_text_and_not_checked():
    reply = "The control asks about “information security” and „ISMS“ in general."

    text, steps, check = checked(reply)

    assert (text, steps, check.asked) == (reply, [], [])


def test_apostrophes_neither_open_nor_close_a_quote():
    reply = "The company’s users' rights aren't changed, and the staff's 'own list' stays."

    text, steps, check = checked(reply)

    assert (text, steps, check.asked) == (reply, [], [])


def test_an_apostrophe_inside_a_quote_doesnt_end_it():
    _, _, check = checked(f"It says ‘the staff’s {QUOTE}’ here.")

    assert check.asked == [(f"the staff’s {QUOTE}", None)]


def test_quotation_marks_inside_a_cite_are_part_of_the_cite():
    reply = f'<cite doc="doc">the “{QUOTE}” rule</cite>'

    _, _, check = checked(reply, f"the “{QUOTE}” rule")

    assert check.asked == [(f"the “{QUOTE}” rule", "doc")]


@pytest.mark.parametrize(
    "reply",
    [
        "For details, see encryption-standard.",
        "The standard [encryption-standard] requires full-disk encryption on every laptop.",
        "Every company laptop uses full-disk encryption with AES-256. [encryption-standard]",
        "> Every laptop is encrypted.\n\nSee encryption-standard.",
    ],
)
def test_prose_without_quotes_or_cites_passes_unchanged(reply):
    text, steps, check = checked(reply)

    assert (text, steps, check.asked) == (reply, [], [])
