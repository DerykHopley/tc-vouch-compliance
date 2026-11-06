import collections
import json
import pathlib
import sys

import yaml

wt = pathlib.Path(sys.argv[1])
b = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(wt / "src"))
exp = yaml.safe_load((wt / "fixtures/nordlicht/expected-answers.yaml").read_text())
cat = {
    c["id"]: c
    for c in yaml.safe_load((wt / "src/vouch/data/vda-isa-2027.1/controls.yaml").read_text())[
        "controls"
    ]
}


def roll(pairs):
    counted = [v for col, v in pairs if col in {"must", "high", "very high", "overlay"}]
    if not counted:
        return "not-applicable"
    e = counted.count("evidenced")
    return "evidenced" if e == len(counted) else ("partly-evidenced" if e else "not-evidenced")


res = {}
for m in sys.argv[3:]:
    ans = {(a["control"], a["requirement"]): a for a in json.loads((b / f"{m}.json").read_text())}
    right = total = cr = cites_ok = cites_all = 0
    conf = collections.Counter()
    per = []
    for c in exp["controls"]:
        cid = str(c["id"])
        col = {r["id"]: r["column"] for r in cat[cid]["requirements"]}
        ep, gp = [], []
        rr = 0
        for r in c["requirements"]:
            a = ans.get((cid, r["id"]))
            total += 1
            v = a["verdict"] if a else None
            if v == r["verdict"]:
                right += 1
                rr += 1
            conf[(r["verdict"], v)] += 1
            ep.append((col[r["id"]], r["verdict"]))
            gp.append((col[r["id"]], v or "missing"))
            if a:
                for d in set(a.get("cites") or []):
                    cites_all += 1
                    cites_ok += d in set(r.get("cites") or [])
        ok = roll(ep) == roll(gp)
        cr += ok
        per.append((cid, rr, len(c["requirements"]), roll(gp), roll(ep)))
    res[m] = dict(  # noqa: C408 (keyword form reads like the scores it writes)
        verdicts=f"{right}/{total} ({right / total:.0%})",
        controls=f"{cr}/12 ({cr / 12:.0%})",
        citations=f"{cites_ok}/{cites_all} ({cites_ok / max(cites_all, 1):.0%})",
        stricter=sum(
            n
            for (e, g), n in conf.items()
            if g
            and ["not-evidenced", "partly-evidenced", "evidenced"].index(g)
            < ["not-evidenced", "partly-evidenced", "evidenced"].index(e)
        ),
        generous=sum(
            n
            for (e, g), n in conf.items()
            if g
            and ["not-evidenced", "partly-evidenced", "evidenced"].index(g)
            > ["not-evidenced", "partly-evidenced", "evidenced"].index(e)
        ),
        missing=conf.get(("evidenced", None), 0)
        + conf.get(("partly-evidenced", None), 0)
        + conf.get(("not-evidenced", None), 0),
        per=per,
    )
(b / "scores.json").write_text(json.dumps(res, indent=1))
for m, r in res.items():
    print(m, {k: v for k, v in r.items() if k != "per"})
    print("   ", "  ".join(f"{c}:{a}/{n}" for c, a, n, *_ in r["per"]))
