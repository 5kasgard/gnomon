"""Depth: protocol in depth.md.

    python bench/depth_reader.py prepare --beam BEAM --locomo F --lme F --out DIR
    (one Haiku reader per DIR/<dataset>_<budget>.jsonl, writing DIR/answers_<dataset>_<budget>.jsonl)
    python bench/depth_reader.py score --out DIR
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import tokens  # noqa: E402
from boundaries import CACHE  # noqa: E402
from situation_reader import STOP  # noqa: E402
from views import terms  # noqa: E402
from views_forage import kwic_aware, record_stats  # noqa: E402
from views_reader import f1  # noqa: E402

N, SEED, BUDGETS = 40, 20261022, (2000, 4000, 8000)


def view(question, order, msgs, idf, budget):
    qw = terms(question)
    text, used = [], 0
    for g in order:
        if g not in msgs:
            continue
        body = kwic_aware(msgs[g], qw, idf)
        cost = tokens(body) + 6
        if used + cost > budget:
            break
        text.append(body)
        used += cost
    return "\n".join(text)


def prepare(a):
    import multi
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sets = {"beam1m": (multi.load_beam(Path(a.beam), "1M"), "1M.pkl"),
            "lme": (multi.load_lme(Path(a.lme)), "multi_lme_s.pkl"),
            "locomo": (multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl")}
    gold_all = {}
    for name, (loader, cache) in sets.items():
        it = iter(pickle.loads((CACHE / cache).read_bytes()))
        cand = []
        for conv, bs, qs in loader:
            msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
            idf = None
            for q in qs:
                ev = set(q["ev"]) & set(msgs)
                if not ev:
                    continue
                c = next(it)
                gold = {w for w in terms(q.get("answer") or "") if w not in STOP} - terms(q["question"])
                if not gold:
                    continue
                if idf is None:
                    idf, _, _ = record_stats(msgs)
                cand.append((q, gold, [view(q["question"], c["hybrid"], msgs, idf, b) for b in BUDGETS]))
        pick = sorted(random.Random(SEED).sample(range(len(cand)), N))
        for b_i, b in enumerate(BUDGETS):
            with open(out / f"{name}_{b // 1000}k.jsonl", "w", encoding="utf-8") as f:
                for k in pick:
                    q, gold, views = cand[k]
                    f.write(json.dumps({"id": f"{name}_{k}", "question": q["question"], "memory": views[b_i]}) + "\n")
        for k in pick:
            q, gold, views = cand[k]
            gold_all[f"{name}_{k}"] = {"answer": q["answer"], "gold": sorted(gold), "question": q["question"],
                                       "presence": [len(gold & terms(v)) / len(gold) for v in views]}
        print(name, len(cand), "eligible;", N, "drawn; mean view tokens",
              [int(np.mean([tokens(cand[k][2][i]) for k in pick])) for i in range(len(BUDGETS))])
    (out / "gold.json").write_text(json.dumps(gold_all), encoding="utf-8")


def score(a):
    out = Path(a.out)
    gold = json.loads((out / "gold.json").read_text())
    rng = np.random.default_rng(0)
    boot = lambda v: np.percentile([rng.choice(v, len(v)).mean() for _ in range(2000)], [2.5, 97.5])  # noqa: E731
    res = {}
    for name in ("beam1m", "lme", "locomo"):
        ans = {}
        for b in BUDGETS:
            p = out / f"answers_{name}_{b // 1000}k.jsonl"
            ans[b] = {json.loads(l)["id"]: str(json.loads(l)["answer"]) for l in open(p) if l.strip()}
        ids = sorted(set.intersection(*(set(v) for v in ans.values())))
        use = {b: np.array([len(set(gold[i]["gold"]) & terms(ans[b][i])) / len(gold[i]["gold"]) for i in ids])
               for b in BUDGETS}
        fs = {b: np.array([f1(ans[b][i], gold[i]["answer"]) for i in ids]) for b in BUDGETS}
        pres = {b: np.array([gold[i]["presence"][k] for i in ids]) for k, b in enumerate(BUDGETS)}
        res[name] = {}
        print(f"{name} (n={len(ids)})")
        for b in BUDGETS:
            d = use[b] - use[2000]
            r = {"answer_words": 100 * use[b].mean(), "f1": 100 * fs[b].mean(), "presence": 100 * pres[b].mean(),
                 "vs_2k": [100 * d.mean(), *(100 * boot(d))] if b != 2000 else None}
            res[name][b] = r
            vs = f"   vs 2K {r['vs_2k'][0]:+.1f} [{r['vs_2k'][1]:+.1f}, {r['vs_2k'][2]:+.1f}]" if r["vs_2k"] else ""
            print(f"  {b // 1000}K: answer words {r['answer_words']:5.1f}  F1 {r['f1']:5.1f}  "
                  f"in view {r['presence']:5.1f}  use/presence {r['answer_words'] / max(r['presence'], 1e-9):.2f}" + vs)
    (HERE / "depth_results.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare", "score"])
    p.add_argument("--beam")
    p.add_argument("--locomo")
    p.add_argument("--lme")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    {"prepare": prepare, "score": score}[a.cmd](a)
