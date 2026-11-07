"""Answer languages: their names for the prompts, which language a text is written in, and
the words with which a text in each claims an audit result.

A compliance profile names its answer language by ISO 639-1 code. Each language Vouch answers
in has one entry in LANGUAGES. Vouch tells them apart by their common function words ("und",
"the"), enough to catch a model that answered in English when German was asked for (#51). It
never translates.

Vouch prepares for an audit; it never claims the audit's result (GLOSSARY.md, "Verdict"). A
negation ("not compliant", "nicht vollständig konform", "kein zertifiziertes ISMS") is not a
claim, nor is a quote from the evidence: the prompts ask for passages copied unchanged from
the documents, so a quoted span is left out only when a passage the assessment gathered holds
it (#140). "Not only compliant" and "nicht nur konform" are claims, not negations.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from vouch.citations import occurs


@dataclass(frozen=True)
class Language:
    """One answer language: its name, its function words, the audit-result words a prompt
    names, the patterns that catch a claim with them, and the words that negate one."""

    name: str
    function_words: frozenset[str]
    audit_words: tuple[str, ...]  # as the prompts name them
    claims: tuple[str, ...]  # regexes, each catching a claim's word in its group "word"
    negation: str  # regex: a negating word, at most one word before the claim's word


_IN_FORMS = r"(?:e[mnrs]?)?"  # German endings: konforme, zertifiziertes, bestandenen
_AUDIT = r"(?:audit|prüfung|zertifizierung|tisax|assessment)[\w-]*"
_NEAR = r"(?:[^\S\n]+[\w-]+){0,4}?[^\S\n]+"  # up to four words between, within one clause

LANGUAGES: dict[str, Language] = {
    "en": Language(
        name="English",
        function_words=frozenset(
            {"and", "the", "is", "are", "of", "with", "for", "not", "be", "to"}
        ),
        audit_words=("compliant", "certified", "passed"),
        claims=(
            r"\b(?P<word>compliant|certified)\b",
            # Handing something on ("passed to the CISO", "passed on to IT") is no claim.
            r"\b(?P<word>passed)\b(?!\s+(?:on\s+)?to\b)",
        ),
        # "not only compliant" and "not just certified" claim it all the same.
        negation=r"\b(?:not|non|never|no)(?!\s+(?:only|just)\b)(?:\s+[\w-]+)?[\s-]*",
    ),
    "de": Language(
        name="German",
        function_words=frozenset(
            {"und", "der", "die", "das", "ist", "wird", "werden", "nicht", "mit", "für"}
        ),
        audit_words=("konform", "zertifiziert", "bestanden"),
        claims=(
            # konform and its compounds (normkonform, DSGVO-konform), not nichtkonform.
            rf"\b(?P<word>(?!nicht)(?:\w+-)?\w*konform{_IN_FORMS})\b",
            rf"\b(?P<word>zertifiziert{_IN_FORMS})\b",
            # bestanden only for an audit, test or certification, never "Mängel bestanden fort".
            rf"\b{_AUDIT}{_NEAR}(?P<word>bestanden{_IN_FORMS})\b",
            rf"\b(?P<word>bestanden{_IN_FORMS}){_NEAR}{_AUDIT}",
        ),
        # "nicht nur konform" and "nicht bloß zertifiziert" claim it all the same.
        negation=r"\b(?:nicht|nie|kein\w*)(?!\s+(?:nur|bloß)\b)(?:\s+[\w-]+)?[\s-]*",
    ),
}

# A quoted span: „…“, “…”, "…", «…» or »…«. Single quotes are left alone: they are apostrophes too.
_QUOTED = re.compile(r"„[^“”\"]*[“”\"]|“[^”]*”|\"[^\"]*\"|«[^»]*»|»[^«]*«")
_MIN_WORDS = 3  # fewer function words than this, and the text is too short to tell
_NEGATION_REACH = 40  # characters before a claim's word searched for its negation


def without_quotes(text: str) -> str:
    """The text with every quoted span left out."""
    return _QUOTED.sub(" ", text)


def without_evidence_quotes(text: str, passages: Iterable[str]) -> str:
    """The text with each quoted span left out that a passage holds as evidence, by the
    citations' quote-match rule (vouch.citations.occurs); every other span stays in."""
    held = list(passages)

    def from_evidence(span: re.Match[str]) -> str:
        inner = span.group()[1:-1]  # the quote marks left out
        return " " if any(occurs(inner, p) for p in held) else span.group()

    return _QUOTED.sub(from_evidence, text)


def language_name(code: str) -> str:
    """The language's English name for a prompt ("German"), or its code when Vouch has none."""
    known = LANGUAGES.get(code)
    return known.name if known else f"the language with ISO 639-1 code '{code}'"


def written_in_not(found: str, expected: str) -> str:
    """An answer's language beside the one asked for, by name: "written in English, not
    German". The assessment's and the chat's steps say it alike (#51, #208)."""
    return f"written in {found}, not {expected}"


def kept_in_other_language(found: str, expected: str) -> str:
    """An answer kept in another language after its one retry, by name (#51, #208)."""
    return f"still {written_in_not(found, expected)}; it was kept as it is"


def language_refusal(found: str, expected: str) -> str:
    """Why an answer in another language is refused, and what to write instead, by name: the
    assessment's refusal and the chat's say it alike (#51, #208)."""
    return (
        f"it is written in {found}, but the answer must be in {expected}. Write it in "
        f"{expected}, quoting documents unchanged in their own language"
    )


def audit_words_in(code: str) -> str:
    """The language's audit-result words as a prompt lists them: "konform, zertifiziert or
    bestanden"; empty for a language Vouch has none for."""
    known = LANGUAGES.get(code)
    if known is None:
        return ""
    *first, last = known.audit_words
    return f"{', '.join(first)} or {last}" if first else last


def _words(text: str) -> list[str]:
    """The text's words, lower case: not quoted ones, nor parts of an id such as IS-01."""
    tokens = re.findall(r"[\w-]+", without_quotes(text))
    return [t.lower() for t in tokens if t.isalpha()]


def written_in(text: str) -> str | None:
    """The language the text is written in, quotes left out: one with at least _MIN_WORDS
    function words and more than twice those of every other. None when it is too short to
    tell, or no language leads so far."""
    words = _words(text)
    counts = {
        code: sum(w in lang.function_words for w in words) for code, lang in LANGUAGES.items()
    }
    for code, count in counts.items():
        others = max((c for other, c in counts.items() if other != code), default=0)
        if count >= _MIN_WORDS and count > 2 * others:
            return code
    return None


def mismatch(text: str, expected: str) -> str | None:
    """The language the text is written in, when Vouch can tell and it isn't `expected`."""
    found = written_in(text)
    return found if found is not None and found != expected else None


def audit_claim(text: str, passages: Iterable[str] = ()) -> str | None:
    """The word with which the text claims an audit result, in any language Vouch knows
    (lower case), or None. Negated words are no claim, nor is a quote from one of the
    `passages` the assessment gathered; no passages, and every quote is checked."""
    unquoted = without_evidence_quotes(text, passages)
    for lang in LANGUAGES.values():
        negated = re.compile(f"(?:{lang.negation})$", re.IGNORECASE)
        for claim in lang.claims:
            for found in re.finditer(claim, unquoted, re.IGNORECASE):
                start = found.start("word")
                if not negated.search(unquoted[max(0, start - _NEGATION_REACH) : start]):
                    return found.group("word").lower()
    return None
