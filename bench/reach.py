"""Reach: protocol in reach.md.

    python bench/reach.py --beam BEAM --locomo F --lme F --convomem DIR [--json out.json]
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
from beam import cluster_ci  # noqa: E402
from beam_dense import MODEL  # noqa: E402
from boundaries import CACHE, rrf  # noqa: E402
from views import terms  # noqa: E402

M = 100
ARMS = ("deeper", "time", "terms", "seed meaning", "all three")


def run(name, loader, cache_file, model, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        rec = None
        for q in qs:
            if rec is None:
                msgs = [x for b in bs for t in b["turns"] for x in t if x["content"].strip()]
                ids = [x["id"] for x in msgs]
                where = {g: i for i, g in enumerate(ids)}
                E = model.encode([x["content"][:4000] for x in msgs])
                E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
                T = [terms(x["content"]) for x in msgs]
                df = Counter(w for t in T for w in t)
                idf = {w: math.log(len(msgs) / c) for w, c in df.items()}
                inv = {}
                for i, t in enumerate(T):
                    for w in t:
                        inv.setdefault(w, []).append(i)
                rec = (ids, where, E, T, idf, inv)
            ids, where, E, T, idf, inv = rec
            ev = set(q["ev"]) & set(where)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            pool = set(c["pool"])
            out_ev = ev - pool
            if not out_ev:
                continue
            outside = [i for i, g in enumerate(ids) if g not in pool]
            out_set = set(outside)
            seeds = [where[g] for g in c["hybrid"][:10] if g in where]
            qv = model.encode([q["question"]])[0]
            qv = qv / max(float(np.linalg.norm(qv)), 1e-9)
            qw = terms(q["question"])
            orders = {}
            sims = E[outside] @ qv
            orders["deeper"] = [outside[j] for j in np.argsort(-sims)]
            near = []
            for w in range(1, len(ids)):
                for s in seeds:
                    for i in (s - w, s + w):
                        if i in out_set and i not in near:
                            near.append(i)
                if len(near) >= M:
                    break
            orders["time"] = near
            sc = Counter()
            for s in seeds:
                for w in T[s] - qw:
                    if idf.get(w, 0) > math.log(50):  # a term in under 2% of messages
                        for i in inv.get(w, ()):
                            if i in out_set:
                                sc[i] += idf[w]
            orders["terms"] = [i for i, _ in sc.most_common()]
            cen = E[seeds].mean(0) if seeds else qv
            cen = cen / max(float(np.linalg.norm(cen)), 1e-9)
            orders["seed meaning"] = [outside[j] for j in np.argsort(-(E[outside] @ cen))]
            orders["all three"] = rrf(orders["time"][:300], orders["terms"][:300], orders["seed meaning"][:300])
            row = {"dataset": name, "conv": conv, "category": q["category"], "n_out": len(out_ev)}
            for arm in ARMS:
                got = {ids[i] for i in orders[arm][:M]}
                row[arm] = len(out_ev & got) / len(out_ev)
            rows.append(row)
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions with evidence outside the pool", flush=True)


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
    print(f"\nshare of out-of-pool evidence reached by {M} added messages")
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        res = {k: ci(rs, lambda r, k=k: r[k]) for k in ARMS}
        for k in ARMS[1:]:
            res[f"{k} - deeper"] = ci(rs, lambda r, k=k: r[k] - r["deeper"])
        out[name] = {"all": res, "n": len(rs)}
        print(f"{name:14s} n={len(rs):4d}  " + "  ".join(f"{k} {res[k][0]:5}" for k in ARMS))
        print(" " * 21 + "  ".join(f"{k}−deeper {res[f'{k} - deeper'][0]} [{res[f'{k} - deeper'][1]}, "
                                   f"{res[f'{k} - deeper'][2]}]" for k in ARMS[1:]))
        for cat in sorted({r["category"] for r in rs}):
            cr = [r for r in rs if r["category"] == cat]
            out[name][cat] = {k: round(100 * float(np.mean([r[k] for r in cr])), 1) for k in ARMS}
            out[name][cat]["n"] = len(cr)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
        Path(a.json).with_suffix(".rows.json").write_text(json.dumps(rows), encoding="utf-8")


if __name__ == "__main__":
    main()
