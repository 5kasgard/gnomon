"""Openers index against hybrid retrieval: protocol in beam_openers.md.

    python bench/beam_openers.py --data BEAM [--json out.json]
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
from beam import batches, cluster_ci, evidence, tokens  # noqa: E402
from beam_dense import MODEL  # noqa: E402

BUDGET, LINE = 2000, 60
WORD = re.compile(r"[a-z0-9][a-z0-9'-]+")
CACHE = Path("/tmp/beam_learn_cache")


def openers(chat):
    """[(message id, text cut to LINE tokens)] for each conversation's first user message, in order."""
    out = []
    for b in batches(chat):
        for turn in b["turns"]:
            us = [x for x in turn if x["role"] == "user" and x["content"].strip()]
            if us:
                out.append((us[0]["id"], us[0]["content"][:4 * LINE]))
    return out


def rrf(*orders):
    s = Counter()
    for o in orders:
        for i, g in enumerate(o):
            s[g] += 1 / (61 + i)
    return [g for g, _ in s.most_common()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(MODEL)
    rows = []
    for size in ("100K", "500K", "1M", "10M"):
        cached = pickle.loads((CACHE / f"{size}.pkl").read_bytes())
        it = iter(cached)
        for d in sorted((Path(a.data) / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
            qf = d / "probing_questions" / "probing_questions.json"
            if not (d / "chat.json").exists() or not qf.exists():
                continue
            chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
            ids = {x["id"] for b in batches(chat) for t in b["turns"] for x in t if x["content"].strip()}
            op = openers(chat)
            otext = [t for _, t in op]
            oid = [g for g, _ in op]
            otok = [tokens(t) + 6 for t in otext]
            E = model.encode(otext)
            E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
            df = Counter(w for t in otext for w in set(WORD.findall(t.lower())))
            for cat, qs in json.loads(qf.read_text(encoding="utf-8")).items():
                for q in qs:
                    ev, _ = evidence(q)
                    ev &= ids
                    if not ev:
                        continue
                    c = next(it)
                    assert c["conv"] == f"{size}/{d.name}" and c["ev"] == ev, (c["conv"], d.name)
                    qv = model.encode([q["question"]])[0]
                    qv /= max(float(np.linalg.norm(qv)), 1e-9)
                    by_mean = list(np.argsort(-(E @ qv)))
                    qw = set(WORD.findall(q["question"].lower()))
                    wscore = [sum(math.log(len(otext) / df[w]) for w in qw & set(WORD.findall(t.lower()))) for t in otext]
                    by_words = [i for i in np.argsort(-np.array(wscore)) if wscore[i] > 0]
                    order = rrf(by_mean, by_words)

                    def take_openers(budget):
                        got, used = [], 0
                        for i in order:
                            if used + otok[i] > budget:
                                break
                            got.append(oid[i])
                            used += otok[i]
                        return got

                    pos = {g: i for i, g in enumerate(c["pool"])}

                    def take_hybrid(budget, skip=()):
                        got, used = [], 0
                        for g in c["hybrid"]:
                            if g in skip or g not in pos:
                                continue
                            t = int(c["tokens"][pos[g]])
                            if used + t > budget:
                                break
                            got.append(g)
                            used += t
                        return got

                    A = set(take_hybrid(BUDGET))
                    B = set(take_openers(BUDGET))
                    half = take_openers(BUDGET // 2)
                    C = set(half) | set(take_hybrid(BUDGET // 2, skip=set(half)))
                    rows.append({"conv": c["conv"], "size": size, "category": cat,
                                 "A": len(ev & A) / len(ev), "B": len(ev & B) / len(ev), "C": len(ev & C) / len(ev)})
        assert next(it, None) is None, size
        print(f"{size}: done, {sum(r['size'] == size for r in rows)} questions", flush=True)
    out = {}
    arms = ("A", "B", "C")
    groups = lambda rs, k: {cv: [r[k] for r in rs if r["conv"] == cv] for cv in {r["conv"] for r in rs}}  # noqa: E731
    prim = [r for r in rows if r["category"] in ("event_ordering", "summarization")]
    print(f"\nprimary: ordering and summaries, all sizes ({len(prim)} questions), evidence recall at {BUDGET} tokens")
    out["primary"] = {k: cluster_ci(groups(prim, k)) for k in arms}
    diffs = {cv: [r["B"] - r["A"] for r in prim if r["conv"] == cv] for cv in {r["conv"] for r in prim}}
    out["primary"]["B-A"] = cluster_ci(diffs)
    for k, v in out["primary"].items():
        print(f"  {k:4s} {v[0]} [{v[1]}, {v[2]}]")
    print(f"\n{'':26s}{'A hybrid':>10s}{'B openers':>11s}{'C both':>9s}")
    for cat in sorted({r["category"] for r in rows}):
        for size in ("100K", "500K", "1M", "10M", "all"):
            rs = [r for r in rows if r["category"] == cat and (size == "all" or r["size"] == size)]
            if not rs:
                continue
            v = {k: round(100 * np.mean([r[k] for r in rs]), 1) for k in arms}
            out.setdefault(cat, {})[size] = v
            if size == "all" or cat in ("event_ordering", "summarization"):
                print(f"{cat[:18] + ' ' + size:26s}{v['A']:>10}{v['B']:>11}{v['C']:>9}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
