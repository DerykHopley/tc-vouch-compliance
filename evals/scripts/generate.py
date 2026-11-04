"""Draft the eval's fake-model scripts from a profile's expected answers.

`uv run python evals/scripts/generate.py [--profile nordlicht]` writes one script per control
to `evals/scripts/<profile>/<control id>.json`, replacing what is there. Each script searches
the policies and the records (and asks Patch when an expected answer cites its response), then
judges every requirement as the expected answers do. Each citation quotes the sentence, list
item or table row of the cited document sharing most words with the requirement and its `why`.

The scripts are drafted from the expected answers, so on the fake model the eval checks the
harness (quote check, stale and documented-only rules, roll-up), not a model. After the
expected answers change (#19), regenerate them, run `uv run python -m vouch.evals`, and read
the diff: a quote the picker chose badly shows as a dropped citation or a wrong verdict.
"""

import argparse
import json
import re
from collections.abc import Iterator
from pathlib import Path

import yaml

from vouch.citations import MIN_QUOTE_WORDS, _blocks
from vouch.documents import document_path, sections
from vouch.evals import EXPECTED_ANSWERS, FAKE_SCRIPTS_DIR, load_cases
from vouch.front_matter import split_front_matter
from vouch.graph import SEARCH_POLICIES, SEARCH_RECORDS
from vouch.isa import load_isa_control
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile

VULNERABILITY_STATUS = "vulnerability_status"
LIST_ITEM = re.compile(r"^([-*+]|\d+[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", text.lower()))


def _is_separator(block: str) -> bool:
    return block.startswith("|") and set(block) <= set("-|: ")


def _quotable(blocks: list[str]) -> Iterator[str]:
    """The parts of a section's blocks a quote can be: a table's data row without its outer
    bars (never its header or separator row), or each sentence of a paragraph or list item."""
    for i, block in enumerate(blocks):
        if not block.startswith("|"):
            yield from SENTENCE_END.split(LIST_ITEM.sub("", block))
        elif not _is_separator(block) and not (
            i + 1 < len(blocks) and _is_separator(blocks[i + 1])
        ):
            yield block.strip("| ")


def _candidates(path: Path) -> list[str]:
    _, body = split_front_matter(path.read_text(encoding="utf-8"))
    return [
        quote
        for section in sections(body)
        for quote in _quotable(_blocks(section))
        if len(quote.split()) > MIN_QUOTE_WORDS
    ]


def _line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _script_text(gather: list[dict], judged: list[dict]) -> str:
    """One turn and one requirement per line, as the hand-written scripts in tests/ are."""
    turns = ",\n    ".join(map(_line, gather))
    requirements = ",\n      ".join(map(_line, judged))
    return (
        f'{{\n  "gather": [\n    {turns}\n  ],\n'
        f'  "judge": {{\n    "requirements": [\n      {requirements}\n    ]\n  }}\n}}\n'
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", default="nordlicht")
    args = parser.parse_args(argv)
    profile = load_named_profile(args.profile)
    folders = {name: source.folder for name, source in profile.evidence_sources.items()}
    answers = FIXTURES_DIR / profile.id / EXPECTED_ANSWERS
    cases, whys = load_cases(answers), _whys(answers)
    out = FAKE_SCRIPTS_DIR / profile.id
    out.mkdir(parents=True, exist_ok=True)

    def find(doc_id: str) -> tuple[str, Path]:
        for source, folder in folders.items():
            if (path := document_path(folder, doc_id)) is not None:
                return source, path
        raise SystemExit(f"{doc_id} is in none of {profile.id}'s evidence sources")

    for case in cases.cases:
        control = load_isa_control(case.control_id)
        texts = {r.id: r.text for r in control.requirements}
        sources, judged = set(), []
        for req in case.requirements:
            wanted = _words(whys[case.control_id, req.id]) | _words(texts[req.id])
            citations = []
            for doc_id in sorted(req.cites):
                source, path = find(doc_id)
                sources.add(source)
                quote = max(_candidates(path), key=lambda q: len(_words(q) & wanted))
                citations.append({"doc_id": doc_id, "quote": quote})
            judged.append(
                {
                    "requirement_id": req.id,
                    "verdict": req.verdict,
                    "reason": whys[case.control_id, req.id],
                    "citations": citations,
                }
            )
        query = " ".join(sorted(_words(control.question), key=len, reverse=True)[:4])
        gather: list[dict] = [
            {"tool": SEARCH_POLICIES, "args": {"query": query}},
            {"tool": SEARCH_RECORDS, "args": {"query": query}},
        ]
        if "patch" in sources:
            gather.append({"tool": VULNERABILITY_STATUS, "args": {}})
        gather.append({"answer": f"Draft answer for {case.control_id}, from the evidence found."})
        (out / f"{case.control_id}.json").write_text(_script_text(gather, judged))
    print(f"wrote {len(cases.cases)} scripts to {out}")


def _whys(path: Path) -> dict[tuple[str, str], str]:
    """The expected answers' `why` line per (control id, requirement id): the scripts' reasons."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        (str(control["id"]), str(r["id"])): r["why"]
        for control in data["controls"]
        for r in control["requirements"]
    }


if __name__ == "__main__":
    main()
