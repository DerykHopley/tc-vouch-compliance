"""Draft the eval's fake-model scripts from a profile's expected answers.

`uv run python evals/scripts/generate.py [--profile nordlicht] [--out <folder>]` writes one script per control
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

from vouch.citations import MIN_QUOTE_WORDS, blocks
from vouch.documents import document_path, sections
from vouch.evals import EXPECTED_ANSWERS, FAKE_SCRIPTS_DIR, load_cases
from vouch.front_matter import split_front_matter
from vouch.graph import SEARCH_POLICIES, SEARCH_RECORDS, VULNERABILITY_STATUS
from vouch.isa import load_isa_control
from vouch.patch import RESPONSE_ID as PATCH_RESPONSE  # cited: the script asks Patch
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile

LIST_ITEM = re.compile(r"^([-*+]|\d+[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", text.lower()))


def _is_separator(block: str) -> bool:
    return block.startswith("|") and set(block) <= set("-|: ")


def _quotable(section_blocks: list[str]) -> Iterator[str]:
    """The parts of a section's blocks a quote can be: a table's data row without its outer
    bars (never its header or separator row), or each sentence of a paragraph or list item."""
    for i, block in enumerate(section_blocks):
        if not block.startswith("|"):
            yield from SENTENCE_END.split(LIST_ITEM.sub("", block))
        elif not _is_separator(block) and not (
            i + 1 < len(section_blocks) and _is_separator(section_blocks[i + 1])
        ):
            yield block.strip("| ")


def _candidates(path: Path) -> list[str]:
    _, body = split_front_matter(path.read_text(encoding="utf-8"))
    return [
        quote
        for section in sections(body)
        for quote in _quotable(blocks(section))
        if len(quote.split()) >= MIN_QUOTE_WORDS
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


def _quote(path: Path, doc_id: str, wanted: set[str]) -> str:
    """The quotable part of the document sharing most words with `wanted` (the first, on a tie)."""
    candidates = _candidates(path)
    if not candidates:
        raise SystemExit(f"{doc_id} has no sentence or table row of {MIN_QUOTE_WORDS}+ words")
    return max(candidates, key=lambda q: len(_words(q) & wanted))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", default="nordlicht")
    parser.add_argument("--out", type=Path, help="default: evals/scripts/<profile>/")
    args = parser.parse_args(argv)
    profile = load_named_profile(args.profile)
    folders = [source.folder for source in profile.evidence_sources.values()]
    cases = load_cases(FIXTURES_DIR / profile.id / EXPECTED_ANSWERS, profile.id).cases
    out = args.out or FAKE_SCRIPTS_DIR / profile.id
    out.mkdir(parents=True, exist_ok=True)

    def find(doc_id: str) -> Path:
        for folder in folders:
            if (path := document_path(folder, doc_id)) is not None:
                return path
        raise SystemExit(f"{doc_id} is in none of {profile.id}'s evidence sources")

    for case in cases:
        control = load_isa_control(case.control_id)
        texts = {r.id: r.text for r in control.requirements}
        judged = []
        for req in case.requirements:
            wanted = _words(req.why) | _words(texts[req.id])
            citations = [
                {"doc_id": doc_id, "quote": _quote(find(doc_id), doc_id, wanted)}
                for doc_id in sorted(req.cites)
            ]
            judged.append(
                {
                    "requirement_id": req.id,
                    "verdict": req.verdict,
                    "reason": req.why,
                    "citations": citations,
                }
            )
        query = " ".join(sorted(_words(control.question), key=lambda w: (-len(w), w))[:4])
        gather: list[dict] = [
            {"tool": SEARCH_POLICIES, "args": {"query": query}},
            {"tool": SEARCH_RECORDS, "args": {"query": query}},
        ]
        if any(PATCH_RESPONSE in r.cites for r in case.requirements):
            gather.append({"tool": VULNERABILITY_STATUS, "args": {}})
        gather.append({"answer": f"Draft answer for {case.control_id}, from the evidence found."})
        script = out / f"{case.control_id}.json"
        script.write_text(_script_text(gather, judged), encoding="utf-8")
    print(f"wrote {len(cases)} scripts to {out}")


if __name__ == "__main__":
    main()
