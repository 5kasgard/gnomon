"""Summarise results/<branch>.json files into one table; optionally compare two result sets.

Usage: python3 bench/issue48/collect.py DIR [OTHER_DIR]
DIR holds <branch>.json for issue-48 (main's code) and each arm48-* branch.
With OTHER_DIR, also report any field that differs between the two runs of the same branch
(commit ids, timings and absolute paths ignored).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CH = ["P10", "P11", "P12", "P12r", "P14", "R", "L", "V", "C"]


def load(d):
    return {p.stem: json.loads(p.read_text()) for p in sorted(Path(d).glob("*.json"))}


def row(name, r, base):
    ch = r["channels"]
    cross = "".join("X" if ch.get(c, {}).get("crosses") else "." for c in CH)
    reg = [k for k, v in r["probes"]["crosses"].items() if v != base["probes"]["crosses"].get(k)]
    errs = [k for k, v in r["probes"].get("errors", {}).items() if v] if isinstance(r["probes"].get("errors"), dict) else []
    s = r["suites"]
    cargo = s["cargo"]["failed"]
    py = s["python"]["failures"] + s["python"]["errors"]
    rep = r["replay"]["hashes"] == base["replay"]["hashes"]
    sz = r["size"]
    lines = "/".join(str(sz[k]["net"]) for k in ("rust", "python", "spec", "tests"))
    return f"| {name} | {cross} | {','.join(reg) or '-'} | {cargo}/{py} | {'same' if rep else 'DIFF'} | {lines} |"


STRIP = {"commit", "seconds", "path", "dir", "tmp"}


def norm(x):
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items() if k not in STRIP}
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, str) and ("/tmp" in x or "/home/" in x):
        return "<path>"
    return x


def diff(a, b, p=""):
    if type(a) is not type(b):
        return [p]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k), b.get(k), f"{p}/{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [p + "[len]"]
        return [q for i, (x, y) in enumerate(zip(a, b)) for q in diff(x, y, f"{p}[{i}]")]
    return [] if a == b else [p]


if __name__ == "__main__":
    runs = load(sys.argv[1])
    base = runs["issue-48"]
    print("channels:", " ".join(CH), "(X = crosses)\n")
    print("| branch | channels | probe changes vs main | cargo/python fails | old logs | net lines R/Py/spec/tests |")
    print("|---|---|---|---|---|---|")
    for name, r in runs.items():
        print(row(name, r, base))
    if len(sys.argv) > 2:
        other = load(sys.argv[2])
        print()
        for name in runs:
            if name not in other:
                print(f"{name}: missing in second run")
                continue
            d = diff(norm(runs[name]), norm(other[name]))
            print(f"{name}: {'identical' if not d else str(len(d)) + ' differences, e.g. ' + ', '.join(d[:4])}")
