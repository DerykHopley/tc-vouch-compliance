"""Answer languages: their names for the prompts, and which language a text is written in.

A compliance profile names its answer language by ISO 639-1 code. Vouch tells the languages
it answers in apart by their common function words ("und", "the"), enough to catch a model
that answered in English when German was asked for (#51). It never translates.
"""

import re

LANGUAGE_NAMES = {"en": "English", "de": "German"}

_FUNCTION_WORDS = {
    "en": frozenset({"and", "the", "is", "are", "of", "with", "for", "not", "be", "to"}),
    "de": frozenset({"und", "der", "die", "das", "ist", "wird", "werden", "nicht", "mit", "für"}),
}
_MIN_WORDS = 3  # fewer function words than this, and the text is too short to tell


def language_name(code: str) -> str:
    """The language's English name for a prompt ("German"), or its code when Vouch has none."""
    return LANGUAGE_NAMES.get(code, f"the language with ISO 639-1 code '{code}'")


def written_in(text: str) -> str | None:
    """The language the text is written in: one with more than twice the function words of
    every other. None when it is too short to tell, or no language leads so far."""
    words = re.findall(r"\w+", text.lower())
    counts = {code: sum(w in known for w in words) for code, known in _FUNCTION_WORDS.items()}
    for code, count in counts.items():
        others = max(c for other, c in counts.items() if other != code)
        if count >= _MIN_WORDS and count > 2 * others:
            return code
    return None


def mismatch(text: str, expected: str) -> str | None:
    """The language the text is written in, when Vouch can tell and it isn't `expected`."""
    found = written_in(text)
    return found if found is not None and found != expected else None
