"""search_policies: keyword search over the company's policy folder, within an allow-list."""

import os
import re
from pathlib import Path
from typing import Any, NotRequired, TypedDict

DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "nordlicht" / "policies"
POLICY_DIR_ENV = "ASSESS_POLICY_DIR"
MAX_QUERY_CHARS = 200
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
SOURCE_UNAVAILABLE = "source_unavailable"
INVALID_INPUT = "invalid_input"

_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_WORD = re.compile(r"\w+")


class Passage(TypedDict):
    doc_id: str
    heading: str
    text: str


class ErrorResult(TypedDict):
    error: str
    detail: NotRequired[str]


def search_policies(query: str, limit: int = DEFAULT_LIMIT) -> list[Passage] | ErrorResult:
    """Return up to `limit` passages that match the query's keywords, best first.

    Errors come back as a dict with an `error` key; nothing is raised.
    """
    problem = _check_input(query, limit)
    if problem:
        return ErrorResult(error=INVALID_INPUT, detail=problem)
    try:
        passages = [p for doc in _policy_files(_policy_dir()) for p in _passages(doc)]
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


def _policy_dir() -> Path:
    return Path(os.environ.get(POLICY_DIR_ENV) or DEFAULT_POLICY_DIR)


def _policy_files(folder: Path) -> list[Path]:
    """The .md files directly in the folder; a link pointing elsewhere is left out."""
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    root = folder.resolve()
    return sorted(
        f
        for f in folder.iterdir()
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

    for line in _body(doc.read_text(encoding="utf-8")).splitlines():
        match = _HEADING.match(line)
        if match or not line.strip():
            close_paragraph()
            if match:
                heading = match.group(1)
        else:
            paragraph.append(line.strip())
    close_paragraph()
    return passages


def _body(text: str) -> str:
    """The text after a leading front matter block (between `---` lines); all of it if none."""
    if text.startswith("---\n"):
        _, closed, body = text[4:].partition("\n---\n")
        if closed:
            return body
    return text


def _score(passage: Passage, terms: set[str]) -> int:
    """How many of the query's words occur in the passage or its heading."""
    haystack = f"{passage['heading']} {passage['text']}".lower()
    return sum(term in haystack for term in terms)
