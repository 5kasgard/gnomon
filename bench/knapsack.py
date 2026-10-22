"""Separate ranking from cost: protocol in boundaries.md ("Next").

    python bench/knapsack.py --beam BEAM --locomo locomo10.json --lme longmemeval_s_cleaned.json \
        --convomem DIR [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import cluster_ci, tokens  # noqa: E402
from beam_dense import MODEL  # noqa: E402
from boundaries import BUDGET, CACHE, LINE, WORD, rrf  # noqa: E402

ARMS = ("A", "H", "G", "F")


class Lines:
    """Every message cut to LINE tokens; `who` picks whose messages (None: everyone's)."""

    def __init__(self, msgs, model, who=None):
        keep = [x for x in msgs if who is None or x["role"] == who]
        self.ids = [x["id"] for x in keep]
        text = [x["content"].strip()[:4 * LINE] for x in keep]
        L = model.encode(text) if text else np.zeros((0, 1))
        self.L = L / np.maximum(np.linalg.norm(L, axis=1, keepdims=True), 1e-9) if len(L) else L
        self.words = [set(WORD.findall(t.lower())) for t in text]
        self.tok = [tokens(t) + 6 for t in text]
        self.df = Counter(w for ws in self.words for w in ws)

    def take(self, qv, qw):
        if not self.ids:
            return []
        n = len(self.ids)
        by_mean = list(np.argsort(-(self.L @ qv)))
        sc = [sum(math.log(n / self.df[w]) for w in qw & ws) for ws in self.words]
        by_words = [k for k in sorted(range(n), key=lambda k: -sc[k]) if sc[k] > 0]
        got, used = [], 0
        for k in rrf(by_mean, by_words):
            if used + self.tok[k] > BUDGET:
                break
            got.append(self.ids[k])
            used += self.tok[k]
        return got


def by_budget(order, cost):
    got, used = [], 0
    for g in order:
        if g not in cost:
            continue
        if used + cost[g] > BUDGET:
            break
        got.append(g)
        used += cost[g]
    return got


def run(name, loader, cache_file, model, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        recs = None
        for q in qs:
            if recs is None:
                msgs = [x for b in bs for t in b["turns"] for x in t if x["content"].strip()]
                full = {x["id"]: tokens(x["content"]) for x in msgs}
                recs = (Lines(msgs, model), Lines(msgs, model, "user"), full)
            G, F, full = recs
            ev = set(q["ev"]) & set(full)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            qv = model.encode([q["question"]])[0]
            qv = qv / max(float(np.linalg.norm(qv)), 1e-9)
            qw = set(WORD.findall(q["question"].lower()))
            views = {
                "A": (by_budget(c["hybrid"], full), False),
                "H": (by_budget(c["hybrid"], {g: min(t, LINE) + 6 for g, t in full.items()}), True),
                "G": (G.take(qv, qw), True),
                "F": (F.take(qv, qw), True),
            }
            row = {"dataset": name, "conv": conv, "category": q["category"]}
            for arm, (got, cut) in views.items():
                got = set(got)
                row[arm] = len(ev & got) / len(ev)
                row[arm + " strict"] = len({g for g in ev & got if not cut or full[g] <= LINE}) / len(ev)
                row[arm + " n"] = len(got)
            rows.append(row)
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions", flush=True)


def main():
    import multi
    from model2vec import StaticModel
    p = argparse.ArgumentParser()
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    model = StaticModel.from_pretrained(MODEL)
    rows = []
    for size in ("100K", "500K", "1M", "10M"):
        run(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", model, rows)
    run("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", model, rows)
    run("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", model, rows)
    run("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))), "multi_convomem.pkl",
        model, rows)

    def ci(rs, key):
        g = {}
        for r in rs:
            k = r["conv"] if r["dataset"].startswith(("BEAM", "LoCoMo")) else r["conv"] + str(id(r))
            g.setdefault(k, []).append(key(r))
        return cluster_ci(g)

    out = {}
    print(f"\nevidence recall at {BUDGET} tokens, lenient / strict; items in view")
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        res = {}
        for k in ARMS:
            res[k] = ci(rs, lambda r, k=k: r[k])
            res[k + " strict"] = ci(rs, lambda r, k=k: r[k + " strict"])
            res[k + " n"] = float(np.median([r[k + " n"] for r in rs]))
        res["H-A"] = ci(rs, lambda r: r["H"] - r["A"])
        res["H-A strict"] = ci(rs, lambda r: r["H strict"] - r["A"])
        out[name] = {"all": res}
        print(f"{name:14s} n={len(rs):5d}  " + "  ".join(
            f"{k} {res[k][0]:5}/{res[k + ' strict'][0]:5} ({res[k + ' n']:.0f})" for k in ARMS)
            + f"   H-A {res['H-A'][0]} [{res['H-A'][1]}, {res['H-A'][2]}]  strict {res['H-A strict'][0]}")
        for cat in sorted({r["category"] for r in rs}):
            cr = [r for r in rs if r["category"] == cat]
            v = {k: round(100 * float(np.mean([r[k] for r in cr])), 1) for k in ARMS}
            v.update({k + " strict": round(100 * float(np.mean([r[k + " strict"] for r in cr])), 1) for k in ARMS})
            out[name][cat] = v
            print(f"    {cat[:28]:28s} " + "  ".join(f"{k} {v[k]:5}/{v[k + ' strict']:5}" for k in ARMS))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
