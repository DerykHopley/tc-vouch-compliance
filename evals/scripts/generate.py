"""Draft the eval's fake-model scripts from a profile's expected answers.

`uv run python evals/scripts/generate.py [--profile nordlicht] [--out <folder>]` writes one script per control
to `evals/scripts/<profile>/<control id>.json`, replacing what is there. Each script searches
the policies and the records (and asks Patch when an expected answer cites its response), then
judges every requirement as the expected answers do; its draft answer is in the profile's
answer language, in the gather prompt's two sections (#670): each cited document quoted, then
each requirement not evidenced. Each citation quotes the sentence, list
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
from vouch.language import ANSWER_HEADINGS, audit_claim
from vouch.patch import RESPONSE_ID as PATCH_RESPONSE  # cited: the script asks Patch
from vouch.profile import load_named_profile
from vouch.settings import load_settings

ANSWER_WORDS = {  # in the profile's answer language, so the language check passes (#51); a
    # gap names its verdict, not its `why`: Alpenwerk's expected answers give theirs in English
    "en": {
        "quote": "{}: “{}”",
        "nothing_shown": "No document found shows what {} asks for.",
        "none_missing": "Nothing is missing.",
        "partly-evidenced": "{} is only partly evidenced.",
        "not-evidenced": "{} is not evidenced.",
    },
    "de": {
        "quote": "{}: „{}“",
        "nothing_shown": "Kein gefundenes Dokument zeigt, was {} verlangt.",
        "none_missing": "Es fehlt nichts.",
        "partly-evidenced": "{} ist nur teilweise nachgewiesen.",
        "not-evidenced": "{} ist nicht nachgewiesen.",
    },
}
LIST_ITEM = re.compile(r"^([-*+]|\d+[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> set[str]:
    """Words of four letters or more; German ones keep their umlauts and ß."""
    return set(re.findall(r"[a-z0-9äöüß]{4,}", text.lower()))


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


def _draft_answer(control_id: str, judged: list[dict], language: str) -> str:
    """The draft answer in its two sections (#670): a paragraph quoting each cited document
    once, then a bullet per requirement not evidenced, naming its verdict. A quote with an audit
    word ("passed a rescan") is left out: the output guard lets it through only when a search
    gathered its passage, and the scripted searches may not."""
    words = ANSWER_WORDS[language]
    shows_heading, missing_heading = ANSWER_HEADINGS[language]
    quoted: dict[str, str] = {}
    for requirement in judged:
        for citation in requirement["citations"]:
            if audit_claim(citation["quote"]) is None:
                quoted.setdefault(citation["doc_id"], citation["quote"])
    shows = [words["quote"].format(doc_id, quote) for doc_id, quote in quoted.items()]
    gaps = [
        f"- {words[r['verdict']].format(r['requirement_id'])}"
        for r in judged
        if r["verdict"] != "evidenced"
    ]
    return (
        f"### {shows_heading}\n\n"
        + ("\n\n".join(shows) or words["nothing_shown"].format(control_id))
        + f"\n\n### {missing_heading}\n\n"
        + ("\n".join(gaps) or words["none_missing"])
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", default="nordlicht")
    parser.add_argument("--out", type=Path, help="default: evals/scripts/<profile>/")
    args = parser.parse_args(argv)
    profiles_dir = load_settings().profiles_dir  # the API's profiles folder (#47)
    profile = load_named_profile(args.profile, profiles_dir)
    folders = [source.folder for source in profile.evidence_sources.values()]
    cases = load_cases(profiles_dir / profile.id / EXPECTED_ANSWERS, profile.id).cases
    out = args.out or FAKE_SCRIPTS_DIR / profile.id
    out.mkdir(parents=True, exist_ok=True)

    def find(doc_id: str) -> Path:
        for folder in folders:
            if (path := document_path(folder, doc_id)) is not None:
                return path
        raise SystemExit(f"{doc_id} is in none of {profile.id}'s evidence sources")

    for case in cases:
        # In the profile's ISA edition, its overlay's requirements (O1) included.
        control = profile.isa_control(case.control_id)
        texts = {r.id: r.text for r in profile.requirements_for(control).applicable}
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
        gather.append({"answer": _draft_answer(case.control_id, judged, profile.answer_language)})
        script = out / f"{case.control_id}.json"
        script.write_text(_script_text(gather, judged), encoding="utf-8")
    print(f"wrote {len(cases)} scripts to {out}")


if __name__ == "__main__":
    main()
