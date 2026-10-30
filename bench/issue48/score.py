"""Score the bake-off (BAKEOFF.md) from the scoring run's outputs.

Usage: python3 bench/issue48/score.py SCORE_DIR
SCORE_DIR holds, per branch b: b.heldout.json (held-out probes), b.gemini.txt, b.review.txt,
b.run.json (channels, P1-P23, suites, replay), b.maintests.txt (main's unchanged Python suite).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ARMS = ["arm48-combo3", "arm48-SC", "arm48-SO", "arm48-SZ", "arm48-SZ2"]
UNSCORED = {("probes_SC.py", "clock_far_future"), ("probes_SZ.py", "through_lease"),
            ("probes_SO.py", "operator"), ("probes_SO.py", "operator_datalog"),
            ("probes_SO.py", "through"), ("probes_SO.py", "failed_write_side_effect")}
RUN_CH = ["P10", "P11", "P12", "P12r", "P14", "R", "L", "V", "C"]


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if k != "setup"}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


def heldout(d):
    out = {}
    for r in json.loads((d).read_text())["records"]:
        key = (r["file"], r["name"])
        if r.get("status", "").startswith("error") or "hidden" not in r:
            out[key] = ("error", None)
        else:
            out[key] = ("X" if strip(r["hidden"]) != strip(r["without"]) else ".", strip(r["without"]))
    return out


def checks(path):
    return {m.group(1).strip(): m.group(2) == "True"
            for m in re.finditer(r"^(\S.*?): (?:crosses|differs)=(True|False)", path.read_text(), re.M)}


def main(sd):
    sd = Path(sd)
    base = heldout(sd / "main.heldout.json")
    rows = []
    for arm in ["main"] + ARMS:
        h = heldout(sd / f"{arm}.heldout.json")
        scored = [k for k in base if k not in UNSCORED]
        cross = [k for k in scored if h.get(k, ("error",))[0] != "."]
        side = [k for k in scored if h.get(k, (None, None))[1] != base[k][1]]
        run = json.loads((sd / f"{arm}.run.json").read_text())
        ch = [c for c in RUN_CH if run["channels"].get(c, {}).get("crosses")]
        g = checks(sd / f"{arm}.gemini.txt")
        rv = checks(sd / f"{arm}.review.txt")
        mt = (sd / f"{arm}.maintests.txt").read_text()
        mt_fail = re.search(r"FAILED \((.*?)\)", mt)
        s = run["suites"]
        replay = run["replay"]["hashes"] == json.loads((sd / "main.run.json").read_text())["replay"]["hashes"]
        size = run.get("size", {})
        lines = "/".join(str(size.get(k, {}).get("net", "?")) for k in ("rust", "python", "spec", "tests"))
        rows.append(dict(arm=arm, heldout_cross=len(cross), heldout_of=len(scored), cross=cross, side=side,
                         run_ch=ch, gemini=[k for k, v in g.items() if v], review=[k for k, v in rv.items() if v],
                         cargo_fail=s["cargo"]["failed"], py_fail=s["python"]["failures"] + s["python"]["errors"],
                         main_tests=mt_fail.group(1) if mt_fail else "ok", replay=replay, lines=lines))
    print("| arm | held-out crossing | run.py channels crossing | own suites fail (cargo/py) | main's tests | old logs | side effects | lines R/Py/spec/tests |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['arm']} | {r['heldout_cross']}/{r['heldout_of']} | {' '.join(r['run_ch']) or '-'} | "
              f"{r['cargo_fail']}/{r['py_fail']} | {r['main_tests']} | {'same' if r['replay'] else 'DIFF'} | "
              f"{len(r['side'])} | {r['lines']} |")
    print()
    for r in rows:
        print(f"{r['arm']}: crossing {[f'{f[7:9]}:{n}' for f, n in r['cross']]}")
        print(f"   side effects {[f'{f[7:9]}:{n}' for f, n in r['side']]}")
        print(f"   gemini_checks crossing {r['gemini']}; review_checks crossing {r['review']}")
    json.dump(rows, open(sd / "score.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
