"""Quotes in a chat answer (#46): the whole reply is checked before it is shown. Each
`<cite doc="…">…</cite>` is checked against its document; any other quoted run of
MIN_QUOTE_WORDS or more words is checked against what the tools returned. One that fails is
replaced with QUOTE_REMOVED, with a guard step."""

import logging
import sys
import unicodedata

import pytest

from vouch.chat_citations import (
    NO_DOCUMENT,
    NOT_CLOSED,
    NOT_THE_FORM,
    QUOTE_REMOVED,
    STRAY_TAG,
    checked_reply,
    dropped_step,
)

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
        f'<cite source="doc">{QUOTE}</cite>',
        f"< cite >{QUOTE}</ cite >",
    ],
)
def test_a_citation_naming_no_document_is_removed_unchecked(cite):
    text, steps, check = checked(f"It says {cite} here.", QUOTE)

    assert check.asked == []
    assert text == f"It says {QUOTE_REMOVED} here."
    assert steps == [dropped_step(None, NO_DOCUMENT)]


@pytest.mark.parametrize(
    "cite",
    [
        f'<CITE doc="doc">{QUOTE}</CITE>',
        f'<Cite doc="doc">{QUOTE}</cite>',
        f"<cite doc='doc'>{QUOTE}</cite>",
        f'< cite doc="doc">{QUOTE}</cite>',
        f'<cite doc="doc" page="2">{QUOTE}</cite>',
        f'<cite doc="doc">{QUOTE}</CITE>',
    ],
)
def test_a_citation_in_any_other_form_is_removed_unchecked(cite):
    text, steps, check = checked(f"It says {cite} here.", QUOTE)

    assert check.asked == []
    assert text == f"It says {QUOTE_REMOVED} here."
    assert steps == [dropped_step(None, NOT_THE_FORM)]


def test_a_citation_never_closed_is_removed_to_the_end_of_the_reply():
    text, steps, _ = checked(f'It says <cite doc="doc">{QUOTE}. And more.', QUOTE)

    assert text == f"It says {QUOTE_REMOVED}"
    assert steps == [dropped_step("doc", NOT_CLOSED)]


@pytest.mark.parametrize("tag", ["<cite>", "<CITE>", "</cite>", "< cite", "<cite doc='x'>"])
def test_a_stray_cite_tag_is_removed_alone_with_a_guard_step(tag):
    reply = f"Use the {tag} tag. Then a long answer follows here with more text."

    text, steps, check = checked(reply)

    assert text == reply.replace(tag, "")
    assert steps == [STRAY_TAG]
    assert check.asked == []


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
        f"‟{QUOTE}”",
        f"‛{QUOTE}’",
        f",,{QUOTE}''",
        f"〝{QUOTE}〞",
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


@pytest.mark.parametrize(
    "reply",
    [
        f"The policy says“{QUOTE}”.",  # opened right after a letter
        f"It says “{QUOTE}”s and more.",  # closed right before a letter
        f"Version 2“{QUOTE}”3 applies.",  # between digits
    ],
)
def test_paired_marks_open_and_close_next_to_letters_and_digits(reply):
    text, steps, check = checked(reply)

    assert check.asked == [(QUOTE, None)]
    assert QUOTE not in text and steps == [dropped_step(None, NOT_FOUND)]


def test_a_cjk_quote_after_cjk_text_counts_its_characters():
    text, steps, check = checked("「全てのUSBポートは常に無効です」とある")

    assert check.asked == [("全てのUSBポートは常に無効です", None)]
    assert text == f"{QUOTE_REMOVED}とある" and len(steps) == 1


def test_a_short_cjk_quote_is_text():
    reply = "「無効です」とある"

    assert checked(reply)[:2] == (reply, [])


REAL = "Every company laptop uses full-disk encryption with AES-256."


def test_a_quotation_wrapping_a_real_cite_is_checked_whole_against_the_tools_results():
    reply = (
        f'Note: “The policy says <cite doc="enc">{REAL}</cite> and all USB ports are always '
        "disabled” today."
    )

    text, steps, check = checked(reply, REAL)

    wrapped = f"The policy says {REAL} and all USB ports are always disabled"
    assert check.asked == [(wrapped, None)]
    assert text == f"Note: {QUOTE_REMOVED} today."
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_a_kept_quotation_still_has_the_cite_inside_it_checked_against_its_document():
    wrapped = f"The policy says {REAL} and all USB ports are always disabled"
    reply = f'“The policy says <cite doc="enc">{REAL}</cite> and all USB ports are always disabled”'

    text, _, check = checked(reply, wrapped)

    assert check.asked == [(wrapped, None), (REAL, "enc")]
    assert text == f"“The policy says {QUOTE_REMOVED} and all USB ports are always disabled”"


def test_a_cite_in_quotation_marks_alone_is_checked_only_against_its_document():
    reply = f'It says “<cite doc="enc">{REAL}</cite>”.'

    text, steps, check = checked(reply, REAL)

    assert check.asked == [(REAL, "enc")]
    assert (text, steps) == (reply, [])


def test_a_blockquote_of_four_or_more_words_is_checked_as_citing_no_document():
    text, steps, check = checked("The standard reads:\n> every laptop is\n> encrypted daily\nDone.")

    assert check.asked == [("every laptop is\nencrypted daily", None)]
    assert text == f"The standard reads:\n{QUOTE_REMOVED}\nDone."
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_a_blockquote_holding_only_a_cite_is_checked_only_against_its_document():
    reply = f'The standard reads:\n> <cite doc="enc">{REAL}</cite>\nDone.'

    text, _, check = checked(reply, REAL)

    assert check.asked == [(REAL, "enc")]
    assert text == reply


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
        "> A short line.\n\nSee encryption-standard.",  # under four words: not a quote
    ],
)
def test_prose_without_quotes_or_cites_passes_unchanged(reply):
    text, steps, check = checked(reply)

    assert (text, steps, check.asked) == (reply, [], [])


# Verify round 2's leaks: mixed marks, unclosed quotations, more marks, broken cite tags

INVENTED = "Every laptop is encrypted twice a day by the night shift."


@pytest.mark.parametrize(
    "quoted",
    [
        f'“{QUOTE}"',  # curly open, straight close
        f'"{QUOTE}”',  # straight open, curly close
        f"„{QUOTE}»",
        f"«{QUOTE}”",
        f"「{QUOTE}”",
    ],
)
def test_any_quotation_mark_closes_any_other(quoted):
    text, steps, check = checked(f"It says {quoted}, as noted.")

    assert check.asked == [(QUOTE, None)]
    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_a_quotation_never_closed_is_checked_to_the_end_of_the_answer():
    text, steps, check = checked(f"The standard says “{INVENTED}")

    assert check.asked == [(INVENTED, None)]
    assert text == f"The standard says {QUOTE_REMOVED}"
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_a_quotation_never_closed_is_kept_when_the_tools_returned_it():
    reply = f"The standard says “{INVENTED}"

    assert checked(reply, INVENTED)[:2] == (reply, [])


def test_a_short_quotation_never_closed_is_text():
    reply = "Use the term “ISMS here."

    assert checked(reply)[:2] == (reply, [])


NEW_MARKS = [("❝", "❞"), ("《", "》"), ("【", "】"), ("<q>", "</q>")]


@pytest.mark.parametrize(("opening", "closing"), NEW_MARKS)
def test_more_quotation_marks_and_q_tags_are_checked(opening, closing):
    text, steps, check = checked(f"It says {opening}{QUOTE}{closing}, as noted.")

    assert check.asked == [(QUOTE, None)]
    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step(None, NOT_FOUND)]


@pytest.mark.parametrize(("opening", "closing"), NEW_MARKS)
def test_a_real_quote_in_the_new_marks_is_kept(opening, closing):
    reply = f"It says {opening}{QUOTE}{closing}, as noted."

    assert checked(reply, QUOTE)[:2] == (reply, [])


def test_a_quotation_broken_by_a_blank_line_is_checked_whole():
    text, steps, check = checked("It says “every laptop\n\nis encrypted daily” here.")

    assert check.asked == [("every laptop\n\nis encrypted daily", None)]
    assert text == f"It says {QUOTE_REMOVED} here."
    assert len(steps) == 1


@pytest.mark.parametrize(
    "cite",
    [
        f'<cite\ndoc="doc">{QUOTE}</cite>',
        f'<\u200bcite doc="doc">{QUOTE}</cite>',
        f'<cite\u200b doc="doc">{QUOTE}</cite>',
        f'<cite doc="doc">{QUOTE}</\u200bcite>',
        f'<ci\u200bte doc="doc">{QUOTE}</cite>',
    ],
)
def test_a_cite_tag_with_whitespace_or_zero_width_characters_inside_is_removed(cite):
    text, steps, check = checked(f"It says {cite} here.", QUOTE)

    assert check.asked == []
    assert text == f"It says {QUOTE_REMOVED} here."
    assert len(steps) == 1


@pytest.mark.parametrize(
    "reply",
    [
        "The users' rights and the staff's list aren't changed by the company’s new policy text.",
        "In the ’90s, and the '90s, the company changed its whole policy on laptops.",
    ],
)
def test_apostrophes_open_no_quotation_never_closed(reply):
    assert checked(reply)[:2] == (reply, [])


OTHER = "staff lock every screen at night"


@pytest.mark.parametrize(
    "reply",
    [
        f"It says “{QUOTE}” and “{OTHER}” here.",
        f'It says "{QUOTE}" and "{OTHER}" here.',
        f"It says <q>{QUOTE}</q> and ❝{OTHER}❞ here.",
    ],
)
def test_two_real_quotes_in_one_sentence_are_each_kept(reply):
    text, steps, check = checked(reply, QUOTE, OTHER)

    assert check.asked == [(QUOTE, None), (OTHER, None)]
    assert (text, steps) == (reply, [])


def test_a_quotation_nested_in_another_is_checked_with_it():
    nested = "The policy says ‘laptops’ are encrypted daily by staff"
    reply = f"It says “{nested}”."

    text, steps, check = checked(reply, nested)

    assert check.asked == [(nested, None)]
    assert (text, steps) == (reply, [])


def test_the_sentence_after_a_quote_in_mixed_marks_stays():
    reply = f'It says “{QUOTE}." Check it now please, auditor.'

    assert checked(reply, f"{QUOTE}.")[:2] == (reply, [])


def test_mixed_marks_around_two_quotations_leave_neither_unchecked():
    text, _, check = checked(f'It says “{QUOTE}" more text “{OTHER}" end.', QUOTE, OTHER)

    assert text == f"It says {QUOTE_REMOVED} end."
    assert len(check.asked) == 1 and QUOTE in check.asked[0][0] and OTHER in check.asked[0][0]


# Every quotation mark, tag form and blockquote depth (#181)

MORE_FORMS = [
    ("｢", "｣"),  # half-width corner brackets
    ("<Q>", "</Q>"),
    ('<q cite="x">', "</q>"),
    ("＇", "＇"),
    ("﹁", "﹂"),
    ("﹃", "﹄"),
    ("⹂", "“"),
    ("❛", "❜"),
    ("❠", "❞"),
    ("〈", "〉"),
    ("〖", "〗"),
    ("″", "″"),
    ("‵", "′"),
    ("<blockquote>", "</blockquote>"),
    ('<BLOCKQUOTE class="a">', "</Blockquote >"),
]


@pytest.mark.parametrize(("opening", "closing"), MORE_FORMS)
def test_an_invented_quote_in_every_other_form_is_dropped(opening, closing):
    text, steps, check = checked(f"It says {opening}{QUOTE}{closing}, as noted.")

    assert check.asked == [(QUOTE, None)]
    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step(None, NOT_FOUND)]


@pytest.mark.parametrize(("opening", "closing"), MORE_FORMS)
def test_a_real_quote_in_every_other_form_is_kept(opening, closing):
    reply = f"It says {opening}{QUOTE}{closing}, as noted."

    assert checked(reply, QUOTE)[:2] == (reply, [])


UNICODE_QUOTES = sorted(
    chr(c) for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) in ("Pi", "Pf")
)


@pytest.mark.parametrize("mark", UNICODE_QUOTES, ids=lambda m: f"U+{ord(m):04X}")
def test_every_unicode_initial_or_final_quotation_mark_opens_a_checked_quote(mark):
    text, steps, check = checked(f"It says {mark}{QUOTE}” here.")

    assert check.asked == [(QUOTE, None)]
    assert text == f"It says {QUOTE_REMOVED} here."
    assert steps == [dropped_step(None, NOT_FOUND)]


@pytest.mark.parametrize("item", ["- ", "* ", "1. ", "  - ", "- 2) "])
def test_a_blockquote_in_a_list_item_is_checked_and_the_item_kept(item):
    text, steps, check = checked(f"Items:\n{item}> {QUOTE}\nDone.")

    assert check.asked == [(QUOTE, None)]
    assert text == f"Items:\n{item}{QUOTE_REMOVED}\nDone."
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_an_inch_mark_opens_no_quotation():
    reply = 'The laptop has a 15" screen and all company laptops use encryption daily.'

    assert checked(reply)[:2] == (reply, [])


def test_a_quote_after_an_inch_mark_is_still_checked():
    text, _, check = checked(f'A 15" screen; the policy says "{QUOTE}" here.')

    assert check.asked == [(QUOTE, None)]
    assert text == f'A 15" screen; the policy says {QUOTE_REMOVED} here.'
