"""Score the profile pass sample run (#430) from its kept raw results, without calling a model.

    uv run python -I evals/profile-pass/score.py .

Reads `<profile>-pass.json` (the `POST /api/profile-pass` answer, with usage) and
`<profile>-assessments.json` (each assessment as `GET /api/assessments/<thread>` gave it) in
this folder, scores each requirement's verdict and citations against
`fixtures/<profile>/expected-answers.yaml` (the same comparison as `evals/full-context/score.py`),
and writes `scores.json` here.
"""

import collections
import json
import pathlib
import sys

import yaml

ORDER = ["not-evidenced", "partly-evidenced", "evidenced"]
repo = pathlib.Path(sys.argv[1])
here = pathlib.Path(__file__).parent
scores = {}
for profile in ["nordlicht", "alpenwerk"]:
    run = json.loads((here / f"{profile}-pass.json").read_text())
    assessments = json.loads((here / f"{profile}-assessments.json").read_text())
    expected_file = repo / f"fixtures/{profile}/expected-answers.yaml"
    expected = {
        (str(c["id"]), r["id"]): r
        for c in yaml.safe_load(expected_file.read_text())["controls"]
        for r in c["requirements"]
    }
    right = total = stricter = generous = cites_ok = cites_all = 0
    guards = collections.Counter()
    for a in assessments:
        for step in a["steps"]:
            if step["node"] == "guard":
                guards[step["text"]] += 1
        for r in a["requirements"]:
            e = expected.get((a["control_id"], r["id"]))
            if e is None:
                continue
            total += 1
            if r["verdict"] == e["verdict"]:
                right += 1
            elif r["verdict"] in ORDER and e["verdict"] in ORDER:
                if ORDER.index(r["verdict"]) < ORDER.index(e["verdict"]):
                    stricter += 1
                else:
                    generous += 1
            for c in r.get("citations") or []:
                cites_all += 1
                cites_ok += c["doc_id"] in set(e.get("cites") or [])
    scores[profile] = {
        "verdicts": f"{right}/{total} ({right / total:.0%})",
        "stricter": stricter,
        "generous": generous,
        "citations": f"{cites_ok}/{cites_all}",
        "controls": {a["control_id"]: a["verdict"] for a in assessments},
        "guard_steps": dict(guards),
        "usage": run["usage"],
        "skipped": run["skipped"],
    }
(here / "scores.json").write_text(json.dumps(scores, indent=1, ensure_ascii=False) + "\n")
for profile, s in scores.items():
    u = s["usage"]
    print(
        f"{profile}: verdicts {s['verdicts']}, stricter {s['stricter']}, generous {s['generous']},"
        f" citations {s['citations']}, {u['input_tokens']:,} in / {u['output_tokens']:,} out,"
        f" ${u['cost_usd']:.3f}"
    )
