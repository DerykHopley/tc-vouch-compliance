"""Evidence documents in a folder: where one lives, and its sections. Shared by the search
tools and the quote check, so both read a document the same way."""

import re
from dataclasses import dataclass
from pathlib import Path

HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
SUFFIX = ".md"


@dataclass(frozen=True)
class Section:
    """A heading and the lines under it, up to the next heading ("" before the first)."""

    heading: str
    lines: tuple[str, ...]


def document_path(folder: Path, doc_id: str) -> Path | None:
    """The document with this id (its file name without `.md`, which may be given) directly in
    the folder, or None. An id holding a path, or a link pointing out of the folder, is None;
    so is every document when the folder (resolved by the profile on load) now resolves
    elsewhere, swapped for a symlink since."""
    doc_id = doc_id.removesuffix(SUFFIX)
    if not doc_id or doc_id in {".", ".."} or "/" in doc_id or "\\" in doc_id:
        return None
    root = folder.resolve()
    if root != folder:
        return None
    path = root / f"{doc_id}{SUFFIX}"
    if not path.is_file() or path.resolve().parent != root:
        return None
    return path


def sections(body: str) -> list[Section]:
    """The body's sections in order; the text before the first heading is the first one."""
    found: list[tuple[str, list[str]]] = [("", [])]
    for line in body.splitlines():
        match = HEADING.match(line)
        if match:
            found.append((match.group(1), []))
        else:
            found[-1][1].append(line)
    return [Section(heading, tuple(lines)) for heading, lines in found]
