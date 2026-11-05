"""Quotes in a chat answer (#46): each quotation the model writes is found as the reply streams,
checked whole, and sent or replaced; a cut-off one is checked too, never sent unchecked."""

import pytest

from vouch.chat_citations import QUOTE_REMOVED, CheckedCitations, dropped_step

NOT_FOUND = "the quote isn't in that document"


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
    quotes = CheckedCitations(check)
    pieces = [piece for chunk in chunks for piece in quotes.add(chunk)] + list(quotes.flush())
    text = "".join(text for text, _ in pieces)
    return text, [step for _, steps in pieces for step in steps], check


QUOTE = "every laptop is encrypted daily"


def test_text_before_a_quote_is_sent_at_once_and_the_quote_held_until_its_citation_ends():
    quotes = CheckedCitations(Checks())

    assert list(quotes.add("It says “every laptop")) == [("It says ", [])]
    assert list(quotes.add(" is encrypted daily” [encr")) == []
    assert list(quotes.add("yption-standard] today.")) == [
        (f"{QUOTE_REMOVED} today.", [dropped_step("encryption-standard", NOT_FOUND)])
    ]


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
    quotes = CheckedCitations(Checks())

    sent = list(quotes.add(f"Die Richtlinie sagt „{QUOTE}“ [encryption-standard]. Danach "))
    sent += list(quotes.add("folgt mehr"))

    assert "".join(text for text, _ in sent).startswith(
        f"Die Richtlinie sagt {QUOTE_REMOVED}. Danach folgt"
    )


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
    quotes = CheckedCitations(Checks())

    sent = list(quotes.add("A stray “ mark, then " + "words " * 400))

    assert "".join(text for text, _ in sent).startswith("A stray “ mark, then words")
