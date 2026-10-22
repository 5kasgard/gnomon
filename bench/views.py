"""What text the reader holds: protocol in views.md.

    python bench/views.py --beam BEAM --locomo locomo10.json --lme longmemeval_s_cleaned.json \
        --convomem DIR [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import cluster_ci, tokens  # noqa: E402
from boundaries import BUDGET, CACHE, LINE  # noqa: E402
from situation_reader import STOP  # noqa: E402

ARMS = ("A", "H", "K")
TERM = re.compile(r"[a-z][a-z'-]{2,}|\d+(?:[.,:]\d+)*")
SENT = re.compile(r"[^.!?\n]+[.!?]?\s*")


def terms(s):
    return set(TERM.findall(s.lower()))


def window(text, start):
    return text[start:start + 4 * LINE]


def kwic(text, qw, idf):
    best, at = 0.0, 0
    for m in SENT.finditer(text):
        sc = sum(idf.get(w, 0.0) for w in qw & terms(m.group(0)))
        if sc > best:
            best, at = sc, m.start()
    return window(text, at)


def run(name, loader, cache_file, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        msgs = idf = None
        for q in qs:
            if msgs is None:
                msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
                df = Counter(w for t in msgs.values() for w in terms(t))
                idf = {w: math.log(len(msgs) / c) for w, c in df.items()}
            ev = set(q["ev"]) & set(msgs)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            qw = terms(q["question"])
            gold = {w for w in terms(q.get("answer") or q.get("rubric") or "") if w not in STOP} - qw
            if not gold:
                continue
            row = {"dataset": name, "conv": conv, "category": q["category"]}
            for arm in ARMS:
                text, got, used = [], set(), 0
                for g in c["hybrid"]:
                    if g not in msgs:
                        continue
                    body = msgs[g] if arm == "A" else window(msgs[g].strip(), 0) if arm == "H" else kwic(msgs[g], qw, idf)
                    cost = tokens(body) + 6
                    if used + cost > BUDGET:
                        break
                    text.append(body)
                    got.add(g)
                    used += cost
                seen = terms(" ".join(text))
                row[arm] = len(gold & seen) / len(gold)
                row[arm + " ev"] = len(ev & got) / len(ev)
            rows.append(row)
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions with answer words", flush=True)


def main():
    import multi
    p = argparse.ArgumentParser()
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    rows = []
    for size in ("100K", "500K", "1M", "10M"):
        run(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", rows)
    run("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", rows)
    run("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", rows)
    run("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))), "multi_convomem.pkl", rows)

    def ci(rs, key):
        g = {}
        for r in rs:
            k = r["conv"] if r["dataset"].startswith(("BEAM", "LoCoMo")) else r["conv"] + str(id(r))
            g.setdefault(k, []).append(key(r))
        return cluster_ci(g)

    out = {}
    print(f"\nanswer words in view at {BUDGET} tokens (evidence recall in brackets)")
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        res = {k: ci(rs, lambda r, k=k: r[k]) for k in ARMS}
        res.update({k + " ev": ci(rs, lambda r, k=k: r[k + " ev"]) for k in ARMS})
        res["K-A"] = ci(rs, lambda r: r["K"] - r["A"])
        res["K-H"] = ci(rs, lambda r: r["K"] - r["H"])
        out[name] = {"all": res}
        print(f"{name:14s} n={len(rs):5d}  " + "  ".join(f"{k} {res[k][0]:5} ({res[k + ' ev'][0]})" for k in ARMS)
              + f"   K-A {res['K-A'][0]} [{res['K-A'][1]}, {res['K-A'][2]}]  K-H {res['K-H'][0]} [{res['K-H'][1]}, {res['K-H'][2]}]")
        for cat in sorted({r["category"] for r in rs}):
            cr = [r for r in rs if r["category"] == cat]
            v = {k: round(100 * float(np.mean([r[k] for r in cr])), 1) for k in ARMS}
            out[name][cat] = v
            print(f"    {cat[:28]:28s} n={len(cr):4d} " + "  ".join(f"{k} {v[k]:5}" for k in ARMS))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
