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


def escaped(folder: Path) -> bool:
    """Whether a folder the profile resolved on load now resolves elsewhere (swapped for a
    symlink since): nothing in it may be read."""
    return folder.resolve() != folder


def document_path(folder: Path, doc_id: str) -> Path | None:
    """The document with this id (its file name without `.md`, which may be given) directly in
    the folder, or None. An id holding a path, or a link pointing out of the folder, is None;
    so is every document when the folder has `escaped`."""
    doc_id = doc_id.removesuffix(SUFFIX)
    if not doc_id or doc_id in {".", ".."} or "/" in doc_id or "\\" in doc_id:
        return None
    if escaped(folder):
        return None
    path = folder / f"{doc_id}{SUFFIX}"
    if not path.is_file() or path.resolve().parent != folder:
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


def paragraphs(body: str) -> list[tuple[str, str]]:
    """Each paragraph of the body (a table counts as one), with the heading of its section:
    (heading, text), the text's lines stripped."""
    found: list[tuple[str, str]] = []
    for section in sections(body):
        paragraph: list[str] = []
        for line in (*section.lines, ""):  # a blank line closes the last paragraph
            if line.strip():
                paragraph.append(line.strip())
            elif paragraph:
                found.append((section.heading, "\n".join(paragraph)))
                paragraph = []
    return found
