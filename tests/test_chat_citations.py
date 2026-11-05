"""Quotes in a chat answer (#46): each quotation the model writes is found as the reply streams,
checked whole, and sent or replaced; a cut-off one is checked too, never sent unchecked."""

import pytest

from vouch.chat_citations import QUOTE_REMOVED, CheckedCitations, dropped_step

NOT_FOUND = "the quote isn't in that document"
DOCUMENTS = frozenset({"encryption-standard", "doc"})
"""The documents the profile allows, by id."""


class Checks:
    """A quote check that fails every quote, noting what it was asked: (quote, doc_id)."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    def __call__(self, quote: str, doc_id: str | None) -> str | None:
        self.asked.append((quote, doc_id))
        return NOT_FOUND


def checked(chunks: list[str]) -> tuple[str, list[str], Checks]:
    """What CheckedCitations sends of a reply written in `chunks`, every quote failing: the
    text, the guard steps, and the check (what it was asked)."""
    check = Checks()
    quotes = CheckedCitations(check, DOCUMENTS)
    pieces = [piece for chunk in chunks for piece in quotes.add(chunk)] + list(quotes.flush())
    text = "".join(text for text, _ in pieces)
    return text, [step for _, steps in pieces for step in steps], check


QUOTE = "every laptop is encrypted daily"


def test_text_before_a_quote_is_sent_at_once_and_the_quote_held_until_its_citation_ends():
    quotes = CheckedCitations(Checks(), DOCUMENTS)

    assert list(quotes.add("It says “every laptop")) == [("It says ", [])]
    assert list(quotes.add(" is encrypted daily” [encr")) == []
    assert list(quotes.add("yption-standard] today.")) == [
        (QUOTE_REMOVED, [dropped_step("encryption-standard", NOT_FOUND)])
    ]
    assert list(quotes.flush()) == [(" today.", [])]  # its own sentence, held until it ended


@pytest.mark.parametrize(
    "citation",
    [
        f"“{QUOTE}” [encryption-standard]",
        f"„{QUOTE}“ [encryption-standard]",  # German
        f"„{QUOTE}” [encryption-standard]",
        f'"{QUOTE}" [encryption-standard]',
        f"«{QUOTE}» [encryption-standard]",
        f"»{QUOTE}« [encryption-standard]",
        f"‚{QUOTE}‘ [encryption-standard]",
        f"“{QUOTE}” (encryption-standard)",  # in parentheses
        f"“{QUOTE}” [ encryption-standard ]",  # spaces inside the brackets
        f"“{QUOTE}”[encryption-standard]",
    ],
)
def test_every_quotation_form_with_a_document_id_is_checked(citation):
    text, steps, check = checked([f"It says {citation[:12]}", f"{citation[12:]}, as noted."])

    assert check.asked == [(QUOTE, "encryption-standard")]
    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step("encryption-standard", NOT_FOUND)]


@pytest.mark.parametrize("marks", ["“”", "„“", '""', "«»", "‚‘"])
def test_a_quote_of_citation_length_with_no_document_is_checked_as_uncited(marks):
    opening, closing = marks
    text, steps, check = checked([f"It says {opening}{QUOTE}{closing} in full."])

    assert check.asked == [(QUOTE, None)]
    assert text == f"It says {QUOTE_REMOVED} in full."
    assert steps == [dropped_step(None, NOT_FOUND)]


def test_a_short_quoted_phrase_with_no_document_is_text_and_not_checked():
    reply = "The control asks about “information security” and „ISMS“ in general."

    text, steps, check = checked([reply])

    assert (text, steps, check.asked) == (reply, [], [])


def test_german_quotes_dont_hold_back_the_text_after_them():
    quotes = CheckedCitations(Checks(), DOCUMENTS)

    sent = list(quotes.add(f"Die Richtlinie sagt „{QUOTE}“ [encryption-standard]. Danach "))
    sent += list(quotes.add("folgt mehr. Und so"))

    assert "".join(text for text, _ in sent).startswith(
        f"Die Richtlinie sagt {QUOTE_REMOVED}. Danach folgt mehr."
    )


# Any text cited by a document id: the quotation, blockquote or sentence before it


@pytest.mark.parametrize(
    "cited",
    [
        f"‘{QUOTE}’ [doc]",
        f"'{QUOTE}' [doc]",
        f"”{QUOTE}” [doc]",
        f"「{QUOTE}」 [doc]",
        f"『{QUOTE}』 [doc]",
        f"`{QUOTE}` [doc]",
        f"‹{QUOTE}› [doc]",
        f"〝{QUOTE}〞 [doc]",
    ],
)
def test_text_in_any_pair_of_quotation_marks_before_a_document_id_is_checked(cited):
    text, steps, check = checked([f"It says {cited[:10]}", f"{cited[10:]}, as noted."])

    assert check.asked == [(QUOTE, "doc")]
    assert text == f"It says {QUOTE_REMOVED}, as noted."
    assert steps == [dropped_step("doc", NOT_FOUND)]


def test_an_apostrophe_inside_a_quotation_doesnt_end_it():
    _, _, check = checked([f"The company’s policy: ‘the staff’s {QUOTE}’ [doc]."])

    assert check.asked == [(f"the staff’s {QUOTE}", "doc")]


def test_the_blockquote_lines_before_a_document_id_are_checked_whole():
    quotes = CheckedCitations(check := Checks(), DOCUMENTS)

    assert list(quotes.add("It reads:\n> every laptop is\n> encrypted daily\n")) == [
        ("It reads:\n", [])
    ]
    sent = list(quotes.add("[doc]\nThat is all.")) + list(quotes.flush())

    assert check.asked == [("every laptop is encrypted daily", "doc")]
    assert "".join(text for text, _ in sent) == f"{QUOTE_REMOVED}\nThat is all."


def test_the_sentence_before_a_document_id_is_held_until_it_ends_then_checked():
    quotes = CheckedCitations(check := Checks(), DOCUMENTS)

    assert list(quotes.add(f"Noted. It says {QUOTE} [encryption-sta")) == [("Noted. ", [])]
    assert list(quotes.add("ndard]")) == []  # mid-sentence or at its end: not yet known
    sent = list(quotes.add(". And more."))

    assert check.asked == [(f"It says {QUOTE}", "encryption-standard")]
    assert "".join(text for text, _ in sent).startswith(QUOTE_REMOVED)


@pytest.mark.parametrize(
    "reply",
    [
        f"The standard (doc) says {QUOTE}.",
        f"The standard [doc] says {QUOTE}.",
        f"See the standard [doc], which says {QUOTE}.",
        f"> {QUOTE} [doc] in a blockquote line that goes on.\n",
    ],
)
def test_a_document_id_mid_sentence_is_a_reference_and_checks_nothing(reply):
    text, steps, check = checked([reply[:20], reply[20:]])

    assert (text, steps, check.asked) == (reply, [], [])


@pytest.mark.parametrize("after", [".", "!", ").", ".”", "\n", ""])
def test_a_document_id_ending_the_sentence_or_line_cites_it(after):
    _, steps, check = checked([f"It says {QUOTE} [doc]{after}"])

    assert check.asked == [(f"It says {QUOTE}", "doc")]
    assert len(steps) == 1


def test_whether_an_id_ends_its_sentence_is_waited_for_then_decided():
    quotes = CheckedCitations(check := Checks(), DOCUMENTS)

    assert list(quotes.add(f"It says {QUOTE} [doc]")) == []
    sent = list(quotes.add(" and more besides. Next"))

    assert check.asked == []
    assert "".join(text for text, _ in sent) == f"It says {QUOTE} [doc] and more besides. "


def test_a_finished_sentence_is_sent_once_the_next_one_starts():
    quotes = CheckedCitations(Checks(), DOCUMENTS)

    sent = list(quotes.add("The first sentence is here. The second"))

    assert "".join(text for text, _ in sent) == "The first sentence is here. "


def test_a_bracket_naming_no_document_is_text():
    text, steps, check = checked(["See the notes on this topic [appendix] and (below)."])

    assert (text, steps, check.asked) == (
        "See the notes on this topic [appendix] and (below).",
        [],
        [],
    )


def test_a_document_id_cut_off_at_the_end_of_the_reply_has_its_sentence_checked():
    text, steps, check = checked([f"It says {QUOTE} [encryption-st"])

    assert check.asked == [(f"It says {QUOTE}", None)]
    assert QUOTE not in text and len(steps) == 1


@pytest.mark.parametrize(
    "reply",
    [
        f"It says “{QUOTE}",  # cut off inside the quote
        f"It says “{QUOTE}” [encryp",  # cut off inside its document id
        f"It says “{QUOTE}”",  # cut off before its document id
    ],
)
def test_a_citation_cut_off_at_the_end_of_the_reply_is_checked_not_sent(reply):
    text, steps, check = checked([reply])

    assert check.asked == [(QUOTE, None)]
    assert text.startswith(f"It says {QUOTE_REMOVED}")
    assert QUOTE not in text
    assert len(steps) == 1


def test_a_nested_quote_is_checked_as_the_innermost_whole_citation():
    text, _, check = checked([f"“Nested “{QUOTE}” [doc] is cut” here."])

    assert check.asked[0] == (QUOTE, "doc")
    assert QUOTE not in text


def test_a_quote_mark_never_closed_is_passed_on_once_it_is_too_long_to_be_a_quote():
    quotes = CheckedCitations(Checks(), DOCUMENTS)

    sent = list(quotes.add("A stray “ mark, then " + "words " * 400))

    assert "".join(text for text, _ in sent).startswith("A stray “ mark, then words")
