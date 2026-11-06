import pathlib
import sys

import yaml

wt = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
exp = yaml.safe_load((wt / "fixtures/nordlicht/expected-answers.yaml").read_text())
cat = {
    c["id"]: c
    for c in yaml.safe_load((wt / "src/vouch/data/vda-isa-2027.1/controls.yaml").read_text())[
        "controls"
    ]
}
lines = [
    "# Nordlicht Komponenten GmbH: assess 12 ISA controls",
    "",
    "You are assessing a fictional company's evidence against VDA ISA 2027.1 requirements, as an experienced TISAX assessor would when preparing for an audit. Judge each requirement listed below from the evidence documents only.",
    "",
    "## Rules",
    "",
    "- Verdicts: `evidenced`, `partly-evidenced` or `not-evidenced`.",
    "- A requirement about doing something (reviewing, training, testing, informing) needs implemented evidence: a record that it was done. A policy saying it will be done is at most `partly-evidenced`.",
    "- Staleness is judged as of 2026-11-04: a document past its review cycle (from its approval date) caps any requirement it supports at `partly-evidenced`.",
    "- Cite the ids of the documents (the `id:` in each document's front matter) a good answer relies on; cite nothing for `not-evidenced`.",
    "- Never say a control passed or failed an audit.",
    "",
    "## Requirements to judge",
    "",
]
for c in exp["controls"]:
    cid = str(c["id"])
    cc = cat[cid]
    lines += [f"### {cid}: {cc['question']}", ""]
    for r in c["requirements"]:
        req = next(x for x in cc["requirements"] if x["id"] == r["id"])
        lines.append(f"- **{r['id']}** ({req['column']}): {req['text']}")
    lines.append("")
lines += ["## Evidence documents", ""]
for kind in ("policies", "records", "patch"):
    for p in sorted((wt / "fixtures/nordlicht" / kind).glob("*.md")):
        lines += [
            f'<document kind="{kind}" file="{p.name}">',
            p.read_text().strip(),
            "</document>",
            "",
        ]
(out / "bundle.md").write_text("\n".join(lines))
print(
    len("\n".join(lines)),
    "chars;",
    sum(len(c["requirements"]) for c in exp["controls"]),
    "requirements",
)
