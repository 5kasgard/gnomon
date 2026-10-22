"""Held-out confirmation on QASPER's test split: protocol in views.md.

    python bench/views_qasper.py --qasper qasper/test/0000.parquet --chosen ARM [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam_dense import MODEL, fuse  # noqa: E402
from situation_reader import STOP  # noqa: E402
from views import terms  # noqa: E402
from views_forage import ARMS, record_stats, report, views  # noqa: E402


def main():
    import pyarrow.parquet as pq
    from model2vec import StaticModel
    p = argparse.ArgumentParser()
    p.add_argument("--qasper", required=True)
    p.add_argument("--chosen", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    model = StaticModel.from_pretrained(MODEL)
    rows = []
    for paper in pq.read_table(a.qasper).to_pylist():
        paras = [x.strip() for sec in paper["full_text"]["paragraphs"] for x in sec if x and x.strip()]
        if not paras:
            continue
        msgs = dict(enumerate(paras))
        idf, df, n_sent = record_stats(msgs)
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
            dense = [int(i) for i in np.argsort(-(E @ qv))]
            qw = terms(q)
            sc = {i: sum(math.log(len(paras) / dfp[w]) for w in qw & terms(t)) for i, t in msgs.items()}
            words = [i for i in sorted(sc, key=lambda i: -sc[i]) if sc[i] > 0]
            order = fuse(words, dense)
            row = {"dataset": "QASPER test", "conv": paper["id"], "category": "paper"}
            for arm, (text, got) in views(q, order, msgs, idf, df, n_sent).items():
                row[arm] = len(gold & terms(text)) / len(gold)
                row[arm + " ev"] = len(ev & got) / len(ev)
            rows.append(row)
    print(f"QASPER test: {len(rows)} questions from {len({r['conv'] for r in rows})} papers")
    out = report(rows, ARMS)
    res = out["QASPER test"]["all"]
    key = f"{a.chosen}-A"
    print(f"\npre-registered: {a.chosen} against A: {res[key]}")
    out["preregistered"] = {"chosen": a.chosen, "difference": res[key]}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
        Path(a.json).with_suffix(".rows.json").write_text(json.dumps(rows), encoding="utf-8")


if __name__ == "__main__":
    main()
