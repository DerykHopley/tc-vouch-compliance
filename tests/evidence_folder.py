"""A company's evidence fixtures folder as the evidence tests read it: its documents' front
matter, their text, which are stale, and its expected-answers draft. Shared by the Nordlicht
(#18) and Alpenwerk (#49) evidence tests."""

import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from vouch.documents import stale_because
from vouch.evals import EXPECTED_ANSWERS
from vouch.front_matter import split_front_matter


class FrontMatter(BaseModel):
    """The fields every evidence document carries (spec #2, Evidence documents)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    title: str
    kind: Literal["documented", "implemented"]
    version: str
    owner: str
    approval_date: datetime.date
    review_cycle: Literal["quarterly", "half-yearly", "yearly", "two-yearly"]
    language: str


@dataclass
class EvidenceFolder:
    """The folder `root`, whose subfolders hold documents of one kind each (`kinds`, folder
    name to kind), all written in `language`."""

    root: Path
    kinds: Mapping[str, str]
    language: str
    _draft: dict | None = field(default=None, init=False, repr=False)

    @property
    def documents(self) -> list[Path]:
        return sorted(p for folder in self.kinds for p in (self.root / folder).glob("*.md"))

    def read(self, path: Path) -> tuple[FrontMatter, str]:
        """The document's front matter, checked to be in the folder's language, and body."""
        data, body = split_front_matter(path.read_text(encoding="utf-8"))
        assert data is not None, f"{path.name} has no front matter"
        meta = FrontMatter.model_validate(data)
        assert meta.language == self.language, f"{path.name} is in {meta.language}"
        return meta, body

    def document(self, doc_id: str) -> tuple[FrontMatter, str]:
        [path] = [p for p in self.documents if p.stem == doc_id]
        return self.read(path)

    def mentioning(self, pattern: str) -> list[str]:
        """The documents whose text matches this regular expression, ignoring case."""
        return [
            p.stem
            for p in self.documents
            if re.search(pattern, p.read_text(encoding="utf-8"), re.IGNORECASE)
        ]

    def stale_on(self, day: datetime.date) -> list[str]:
        """The documents stale on this day, by vouch's rule (vouch.documents)."""
        return [p.stem for p in self.documents if stale_because(self.read(p)[0].model_dump(), day)]

    def draft(self) -> dict:
        """The expected-answers draft, read once."""
        if self._draft is None:
            text = (self.root / EXPECTED_ANSWERS).read_text(encoding="utf-8")
            self._draft = yaml.safe_load(text)
        return self._draft

    def expected_answers(self) -> dict[str, dict[str, dict]]:
        """The draft as {control id: {requirement id: entry}}."""
        return {c["id"]: {r["id"]: r for r in c["requirements"]} for c in self.draft()["controls"]}

    def listed_twice(self) -> list[str]:
        """Each control, or control and requirement, the draft lists more than once."""
        controls = [str(c["id"]) for c in self.draft()["controls"]]
        named = controls + [
            f"{c['id']} {r['id']}" for c in self.draft()["controls"] for r in c["requirements"]
        ]
        return sorted({n for n in named if named.count(n) > 1})


def table_rows(body: str) -> list[dict[str, str]]:
    """The first Markdown table in the body, one {header: cell} per data row."""
    lines: list[str] = []
    for line in map(str.strip, body.splitlines()):
        if line.startswith("|"):
            lines.append(line)
        elif lines:  # the first table has ended
            break
    header, _, *rows = ([cell.strip() for cell in line.strip("|").split("|")] for line in lines)
    return [dict(zip(header, row, strict=True)) for row in rows]
