"""Do views' answer words predict a reader's answers? Protocol in views.md (last section).

    python bench/views_reader.py prepare --locomo F --qasper F --out DIR
    (one Haiku reader per DIR/<cond>.jsonl, writing DIR/answers_<cond>.jsonl)
    python bench/views_reader.py score --out DIR
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from situation_reader import STOP  # noqa: E402
from views import terms  # noqa: E402

N, SEED = 60, 20261022
CONDS = {"locomo_window": ("LoCoMo", "window"), "locomo_sentences": ("LoCoMo", "sentences"),
         "qasper_whole": ("QASPER", "A"), "qasper_window": ("QASPER", "K'")}


def prepare(a):
    import math
    import multi
    import pyarrow.parquet as pq
    from beam import build
    from beam_dense import MODEL, fuse
    from gnomon.consolidate import Tiers
    from gnomon.rank import Ranker
    from model2vec import StaticModel
    from views_forage import record_stats, views
    model = StaticModel.from_pretrained(MODEL)
    embed = lambda t: model.encode(list(t))  # noqa: E731
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = {c: [] for c in CONDS}
    gold = {}
    # LoCoMo: every eligible question, then a seeded sample
    cand = []
    for conv, bs, qs in multi.load_locomo(Path(a.locomo)):
        for q in qs:
            if q["ev"] and {w for w in terms(q["answer"]) if w not in STOP} - terms(q["question"]):
                cand.append((conv, q))
    pick = random.Random(SEED).sample(range(len(cand)), N)
    by_conv = {}
    for i in pick:
        by_conv.setdefault(cand[i][0], []).append((i, cand[i][1]))
    for conv, bs, qs in multi.load_locomo(Path(a.locomo)):
        if conv not in by_conv:
            continue
        with tempfile.TemporaryDirectory() as t:
            m, _, _ = build(bs, Path(t))
            tiers = Tiers(m, "owner", ranker=Ranker(m, "owner", embed=embed))
            for i, q in by_conv[conv]:
                qid = f"locomo_{i}"
                gold[qid] = q["answer"]
                for c, (ds, v) in CONDS.items():
                    if ds == "LoCoMo":
                        ctx = tiers.context(q["question"], budget=2000, directive_budget=0, view=v)
                        rows[c].append({"id": qid, "question": q["question"],
                                        "memory": "\n".join(x["text"] for x in ctx["view"])})
    # QASPER
    papers = pq.read_table(a.qasper).to_pylist()
    cand = []
    for pi, paper in enumerate(papers):
        for qi, (q, ans) in enumerate(zip(paper["qas"]["question"], paper["qas"]["answers"])):
            first = next((x for x in ans["answer"] if not x["unanswerable"]
                          and (x["extractive_spans"] or x["free_form_answer"])), None)
            if first is None:
                continue
            text = " ".join(first["extractive_spans"]) or first["free_form_answer"]
            if {w for w in terms(text) if w not in STOP} - terms(q):
                cand.append((pi, qi, q, text))
    for k in random.Random(SEED).sample(range(len(cand)), N):
        pi, qi, q, text = cand[k]
        paper = papers[pi]
        paras = [x.strip() for sec in paper["full_text"]["paragraphs"] for x in sec if x and x.strip()]
        msgs = dict(enumerate(paras))
        idf, df, n_sent = record_stats(msgs)
        E = model.encode(paras)
        E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
        qv = model.encode([q])[0]
        qv = qv / max(float(np.linalg.norm(qv)), 1e-9)
        dfp = Counter(w for t in paras for w in terms(t))
        sc = {i: sum(math.log(len(paras) / dfp[w]) for w in terms(q) & terms(t)) for i, t in msgs.items()}
        order = fuse([i for i in sorted(sc, key=lambda i: -sc[i]) if sc[i] > 0],
                     [int(i) for i in np.argsort(-(E @ qv))])
        vs = views(q, order, msgs, idf, df, n_sent)
        qid = f"qasper_{k}"
        gold[qid] = text
        for c, (ds, v) in CONDS.items():
            if ds == "QASPER":
                rows[c].append({"id": qid, "question": q, "memory": vs[v][0]})
    for c, rs in rows.items():
        with open(out / f"{c}.jsonl", "w", encoding="utf-8") as f:
            for r in rs:
                f.write(json.dumps(r) + "\n")
        print(c, len(rs), "questions,", sum(len(r["memory"]) for r in rs) // 4 // len(rs), "tokens of memory each")
    (out / "gold.json").write_text(json.dumps(gold), encoding="utf-8")


def f1(pred, gold):
    p = [w for w in re.findall(r"[a-z0-9]+", pred.lower()) if w not in STOP]
    g = [w for w in re.findall(r"[a-z0-9]+", gold.lower()) if w not in STOP]
    common = sum((Counter(p) & Counter(g)).values())
    if not common:
        return 0.0
    pr, rc = common / len(p), common / len(g)
    return 2 * pr * rc / (pr + rc)


def score(a):
    out = Path(a.out)
    gold = json.loads((out / "gold.json").read_text())
    qs = {c: {json.loads(l)["id"]: json.loads(l)["question"] for l in open(out / f"{c}.jsonl")} for c in CONDS}
    ans = {}
    for c in CONDS:
        ans[c] = {}
        for line in open(out / f"answers_{c}.jsonl", encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                ans[c][r["id"]] = str(r["answer"])
    res = {}
    rng = np.random.default_rng(0)
    for x, y in (("locomo_window", "locomo_sentences"), ("qasper_whole", "qasper_window")):
        ids = sorted(set(ans[x]) & set(ans[y]))
        def words(c, i):
            g = {w for w in terms(gold[i]) if w not in STOP} - terms(qs[c][i])
            return len(g & terms(ans[c][i])) / len(g)
        d = np.array([words(x, i) - words(y, i) for i in ids])
        df1 = np.array([f1(ans[x][i], gold[i]) - f1(ans[y][i], gold[i]) for i in ids])
        boot = lambda v: np.percentile([rng.choice(v, len(v)).mean() for _ in range(2000)], [2.5, 97.5])  # noqa: E731
        res[f"{x} - {y}"] = {"n": len(ids), "answer_words": [100 * d.mean(), *(100 * boot(d))],
                             "f1": [100 * df1.mean(), *(100 * boot(df1))],
                             "means": {x: 100 * np.mean([words(x, i) for i in ids]),
                                       y: 100 * np.mean([words(y, i) for i in ids])}}
        r = res[f"{x} - {y}"]
        print(f"{x} - {y} (n={len(ids)}): answer words {r['answer_words'][0]:+.1f} "
              f"[{r['answer_words'][1]:+.1f}, {r['answer_words'][2]:+.1f}]  F1 {r['f1'][0]:+.1f} "
              f"[{r['f1'][1]:+.1f}, {r['f1'][2]:+.1f}]  means {', '.join(f'{k} {v:.1f}' for k, v in r['means'].items())}")
    (HERE / "views_reader_results.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare", "score"])
    p.add_argument("--locomo")
    p.add_argument("--qasper")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    {"prepare": prepare, "score": score}[a.cmd](a)
