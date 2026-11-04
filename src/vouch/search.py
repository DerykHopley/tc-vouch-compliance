"""search_policies: keyword search over the policy folder a compliance profile allows."""

import re
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from vouch.front_matter import split_front_matter

MAX_QUERY_CHARS = 200
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
SOURCE_UNAVAILABLE = "source_unavailable"
INVALID_INPUT = "invalid_input"
FOLDER_ESCAPED = "folder_escaped"

_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_WORD = re.compile(r"\w+")


class Passage(TypedDict):
    doc_id: str
    heading: str
    text: str


class ErrorResult(TypedDict):
    error: str
    detail: NotRequired[str]


def search_policies(
    query: str, limit: int = DEFAULT_LIMIT, *, folder: Path
) -> list[Passage] | ErrorResult:
    """Return up to `limit` passages that match the query's keywords, best first.

    `folder` is the policy folder the compliance profile allows, resolved as the profile
    resolved it on load. It is resolved again here, on the path actually read: one that now
    resolves elsewhere (swapped for a symlink) is refused with `folder_escaped`. Errors come
    back as a dict with an `error` key; nothing is raised.
    """
    problem = _check_input(query, limit)
    if problem:
        return ErrorResult(error=INVALID_INPUT, detail=problem)
    root = folder.resolve()
    if root != folder:
        return ErrorResult(
            error=FOLDER_ESCAPED, detail="the folder now resolves outside where it was allowed"
        )
    try:
        passages = [p for doc in _policy_files(root) for p in _passages(doc)]
    except (OSError, UnicodeDecodeError):  # missing, unreadable, or not text
        return ErrorResult(error=SOURCE_UNAVAILABLE)
    terms = {t.lower() for t in _WORD.findall(query)}
    scored = [(passage, _score(passage, terms)) for passage in passages]
    hits = [(passage, score) for passage, score in scored if score > 0]
    hits.sort(key=lambda hit: hit[1], reverse=True)  # stable: ties keep folder order
    return [passage for passage, _ in hits[:limit]]


def _check_input(query: Any, limit: Any) -> str | None:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY_CHARS:
        return f"query must be 1 to {MAX_QUERY_CHARS} characters after trimming"
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        return f"limit must be an integer from 1 to {MAX_LIMIT}"
    return None


def _policy_files(root: Path) -> list[Path]:
    """The .md files directly in the resolved folder; a link pointing elsewhere is left out."""
    if not root.is_dir():
        raise FileNotFoundError(root)
    return sorted(
        f
        for f in root.iterdir()
        if f.suffix == ".md" and f.is_file() and f.resolve().parent == root
    )


def _passages(doc: Path) -> list[Passage]:
    """One passage per paragraph, each under the nearest heading above it."""
    passages: list[Passage] = []
    heading = ""
    paragraph: list[str] = []

    def close_paragraph() -> None:
        if paragraph:
            passages.append(Passage(doc_id=doc.stem, heading=heading, text="\n".join(paragraph)))
            paragraph.clear()

    _, body = split_front_matter(doc.read_text(encoding="utf-8"))
    for line in body.splitlines():
        match = _HEADING.match(line)
        if match or not line.strip():
            close_paragraph()
            if match:
                heading = match.group(1)
        else:
            paragraph.append(line.strip())
    close_paragraph()
    return passages


def _score(passage: Passage, terms: set[str]) -> int:
    """How many of the query's words occur in the passage or its heading."""
    haystack = f"{passage['heading']} {passage['text']}".lower()
    return sum(term in haystack for term in terms)
