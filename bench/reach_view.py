"""Does reach put more answer in view? Protocol in reach.md (last section).

    python bench/reach_view.py --beam BEAM --locomo F --lme F --convomem DIR --qasper F [--json out.json]
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
from beam import tokens  # noqa: E402
from boundaries import BUDGET, CACHE  # noqa: E402
from situation_reader import STOP  # noqa: E402
from views import terms  # noqa: E402
from views_forage import kwic_aware, record_stats, report  # noqa: E402

ARMS = ("W", "W+1", "W+2")
SEEDS = 10


def with_neighbours(order, seq, where, w):
    out, seen = [], set()
    for rank, g in enumerate(order):
        if g in seen:
            continue
        out.append(g)
        seen.add(g)
        if rank < SEEDS and g in where:
            i = where[g]
            for j in [i + d for k in range(1, w + 1) for d in (-k, k)]:
                if 0 <= j < len(seq) and seq[j] not in seen:
                    out.append(seq[j])
                    seen.add(seq[j])
    return out


def view(question, order, msgs, idf):
    qw = terms(question)
    text, got, used = [], set(), 0
    for g in order:
        if g not in msgs:
            continue
        body = kwic_aware(msgs[g], qw, idf)
        cost = tokens(body) + 6
        if used + cost > BUDGET:
            break
        text.append(body)
        got.add(g)
        used += cost
    return " ".join(text), got


def score(name, conv, cat, q, gold, ev, order, msgs, seq, idf, rows):
    where = {g: i for i, g in enumerate(seq)}
    row = {"dataset": name, "conv": conv, "category": cat}
    for arm, w in (("W", 0), ("W+1", 1), ("W+2", 2)):
        text, got = view(q, with_neighbours(order, seq, where, w) if w else order, msgs, idf)
        row[arm] = len(gold & terms(text)) / len(gold)
        row[arm + " ev"] = len(ev & got) / len(ev)
    rows.append(row)


def run(name, loader, cache_file, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        msgs = None
        for q in qs:
            if msgs is None:
                seq = [x["id"] for b in bs for t in b["turns"] for x in t if x["content"].strip()]
                msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
                idf, _, _ = record_stats(msgs)
            ev = set(q["ev"]) & set(msgs)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            gold = {w for w in terms(q.get("answer") or q.get("rubric") or "") if w not in STOP} - terms(q["question"])
            if gold:
                score(name, conv, q["category"], q["question"], gold, ev, c["hybrid"], msgs, seq, idf, rows)
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions", flush=True)


def qasper(path, rows):
    import pyarrow.parquet as pq
    from beam_dense import MODEL, fuse
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(MODEL)
    for paper in pq.read_table(path).to_pylist():
        paras = [x.strip() for sec in paper["full_text"]["paragraphs"] for x in sec if x and x.strip()]
        if not paras:
            continue
        msgs = dict(enumerate(paras))
        seq = list(range(len(paras)))
        idf, _, _ = record_stats(msgs)
        E = model.encode(paras)
        E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
        dfp = Counter(w for t in paras for w in terms(t))
        for q, ans in zip(paper["qas"]["question"], paper["qas"]["answers"]):
            first = next((x for x in ans["answer"] if not x["unanswerable"]
                          and (x["extractive_spans"] or x["free_form_answer"])), None)
            if first is None:
                continue
            gold = {w for w in terms(" ".join(first["extractive_spans"]) or first["free_form_answer"])
                    if w not in STOP} - terms(q)
            ev = {i for i, t in msgs.items() if any(t == e.strip() for e in first["evidence"])}
            if not gold or not ev:
                continue
            qv = model.encode([q])[0]
            qv = qv / max(float(np.linalg.norm(qv)), 1e-9)
            sc = {i: sum(math.log(len(paras) / dfp[w]) for w in terms(q) & terms(t)) for i, t in msgs.items()}
            order = fuse([i for i in sorted(sc, key=lambda i: -sc[i]) if sc[i] > 0],
                         [int(i) for i in np.argsort(-(E @ qv))])
            score("QASPER test", paper["id"], "paper", q, gold, ev, order, msgs, seq, idf, rows)
    print(f"QASPER test: {sum(r['dataset'] == 'QASPER test' for r in rows)} questions", flush=True)


def main():
    import multi
    p = argparse.ArgumentParser()
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--qasper", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    rows = []
    for size in ("100K", "500K", "1M", "10M"):
        run(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", rows)
    run("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", rows)
    run("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", rows)
    run("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))), "multi_convomem.pkl", rows)
    qasper(a.qasper, rows)
    print(f"\nanswer words in view at {BUDGET} tokens (evidence recall)")
    out = report(rows, ARMS, ref="W")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
        Path(a.json).with_suffix(".rows.json").write_text(json.dumps(rows), encoding="utf-8")


if __name__ == "__main__":
    main()
