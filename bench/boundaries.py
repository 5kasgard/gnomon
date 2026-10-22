"""Boundaries: protocol in boundaries.md.

    python bench/boundaries.py --beam BEAM --locomo locomo10.json --lme longmemeval_s_cleaned.json \
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
from beam_dense import MODEL  # noqa: E402

BUDGET, LINE, WIN, TOP = 2000, 60, 5, 0.10
WORD = re.compile(r"[a-z0-9][a-z0-9'-]+")
CACHE = Path("/tmp/beam_learn_cache")
ARMS = ("A", "F", "B", "D")


def rrf(*orders):
    s = Counter()
    for o in orders:
        for i, g in enumerate(o):
            s[g] += 1 / (61 + i)
    return [g for g, _ in s.most_common()]


class Record:
    """One record's messages, the person's lines and the three line indexes."""

    def __init__(self, bs, model):
        msgs, opener = [], set()
        for b in bs:
            for turn in b["turns"]:
                first = True
                for x in turn:
                    if not x["content"].strip():
                        continue
                    msgs.append(x)
                    if first and x["role"] == "user":
                        opener.add(x["id"])
                        first = False
        self.ids = {x["id"] for x in msgs}
        E = model.encode([x["content"][:4000] for x in msgs])
        E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
        shift = np.ones(len(msgs))
        for i in range(1, len(msgs)):
            c = E[max(0, i - WIN):i].mean(0)
            shift[i] = 1 - float(E[i] @ (c / max(np.linalg.norm(c), 1e-9)))
        user = [i for i, x in enumerate(msgs) if x["role"] == "user"]
        cut = np.quantile(shift[user], 1 - TOP) if user else 1
        boundary = {msgs[i]["id"] for i in user if shift[i] >= cut}
        self.lines = [(msgs[i]["id"], msgs[i]["content"].strip()[:4 * LINE]) for i in user]
        L = model.encode([t for _, t in self.lines]) if self.lines else np.zeros((0, 1))
        self.L = L / np.maximum(np.linalg.norm(L, axis=1, keepdims=True), 1e-9) if len(L) else L
        self.words = [set(WORD.findall(t.lower())) for _, t in self.lines]
        self.tok = [tokens(t) + 6 for _, t in self.lines]
        self.members = {"F": list(range(len(self.lines))),
                        "B": [k for k, (g, _) in enumerate(self.lines) if g in opener],
                        "D": [k for k, (g, _) in enumerate(self.lines) if g in boundary]}
        self.df = {a: Counter(w for k in m for w in self.words[k]) for a, m in self.members.items()}
        self.opener_share_of_D = len(boundary & opener) / max(len(boundary), 1)
        self.opener_share_of_user = len(opener) / max(len(user), 1)

    def index(self, arm, qv, qwords):
        m = self.members[arm]
        if not m:
            return set()
        by_mean = [m[j] for j in np.argsort(-(self.L[m] @ qv))]
        df, n = self.df[arm], len(m)
        sc = {k: sum(math.log(n / df[w]) for w in qwords & self.words[k]) for k in m}
        by_words = [k for k in sorted(m, key=lambda k: -sc[k]) if sc[k] > 0]
        got, used = set(), 0
        for k in rrf(by_mean, by_words):
            if used + self.tok[k] > BUDGET:
                break
            got.add(self.lines[k][0])
            used += self.tok[k]
        return got


def hybrid(c):
    pos = {g: i for i, g in enumerate(c["pool"])}
    got, used = set(), 0
    for g in c["hybrid"]:
        if g not in pos:
            continue
        t = int(c["tokens"][pos[g]])
        if used + t > BUDGET:
            break
        got.add(g)
        used += t
    return got


def run(name, loader, cache_file, model, rows, diag):
    cached = pickle.loads((CACHE / cache_file).read_bytes())
    it = iter(cached)
    for conv, bs, qs in loader:
        rec = None
        for q in qs:
            ev = set(q["ev"])
            if rec is None:
                rec = Record(bs, model)
                diag.setdefault(name, []).append((rec.opener_share_of_D, rec.opener_share_of_user))
            ev &= rec.ids
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            qv = model.encode([q["question"]])[0]
            qv = qv / max(float(np.linalg.norm(qv)), 1e-9)
            qw = set(WORD.findall(q["question"].lower()))
            row = {"dataset": name, "conv": conv, "category": q["category"], "A": len(ev & hybrid(c)) / len(ev)}
            for arm in ("F", "B", "D"):
                row[arm] = len(ev & rec.index(arm, qv, qw)) / len(ev)
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
    rows, diag = [], {}
    for size in ("100K", "500K", "1M", "10M"):
        run(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", model, rows, diag)
    run("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", model, rows, diag)
    run("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", model, rows, diag)
    run("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))), "multi_convomem.pkl",
        model, rows, diag)
    out = {"diagnostic_openers_among_D": {k: [float(np.mean([d[0] for d in v])), float(np.mean([d[1] for d in v]))]
                                          for k, v in diag.items()}}
    print("\nQ3: share of D's lines that are openers, against openers' share of the person's messages")
    for k, (d_, base) in out["diagnostic_openers_among_D"].items():
        print(f"  {k:14s} {100 * d_:5.1f}%  (base {100 * base:5.1f}%)")

    def ci(rs, key):
        by = lambda r: r["conv"] if r["dataset"].startswith(("BEAM", "LoCoMo")) else r["conv"] + str(id(r))  # noqa: E731
        g = {}
        for r in rs:
            g.setdefault(by(r), []).append(key(r))
        return cluster_ci(g)

    print(f"\nevidence recall at {BUDGET} tokens: A hybrid | F all lines | B openers | D boundaries   (B-F, D-F)")
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        res = {k: ci(rs, lambda r, k=k: r[k]) for k in ARMS}
        res["B-F"] = ci(rs, lambda r: r["B"] - r["F"])
        res["D-F"] = ci(rs, lambda r: r["D"] - r["F"])
        out[name] = {"all": res}
        print(f"{name:14s} n={len(rs):5d}  " + "  ".join(f"{k} {res[k][0]:5}" for k in ARMS)
              + f"   B-F {res['B-F'][0]} [{res['B-F'][1]}, {res['B-F'][2]}]  D-F {res['D-F'][0]} [{res['D-F'][1]}, {res['D-F'][2]}]")
        for cat in sorted({r["category"] for r in rs}):
            cr = [r for r in rs if r["category"] == cat]
            v = {k: round(100 * float(np.mean([r[k] for r in cr])), 1) for k in ARMS}
            out[name][cat] = v
            print(f"    {cat[:30]:30s} " + "  ".join(f"{k} {v[k]:5}" for k in ARMS))
    eo = [r for r in rows if r["dataset"].startswith("BEAM") and r["category"] == "event_ordering"]
    out["Q1_BEAM_event_ordering"] = {"B": ci(eo, lambda r: r["B"]), "F": ci(eo, lambda r: r["F"]),
                                     "B-F": ci(eo, lambda r: r["B"] - r["F"])}
    print("\nQ1, BEAM event ordering, all sizes:", out["Q1_BEAM_event_ordering"])
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
