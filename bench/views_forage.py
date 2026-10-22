"""Foraging arms on the four datasets: protocol in views.md ("Next").

    python bench/views_forage.py --beam BEAM --locomo F --lme F --convomem DIR [--json out.json]
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
from boundaries import BUDGET, CACHE, LINE  # noqa: E402
from gnomon.consolidate import _terms, select_sentences, sentences  # noqa: E402
from situation_reader import STOP  # noqa: E402
from views import SENT, kwic, terms  # noqa: E402

ARMS = ("A", "K", "K'", "S")


def kwic_aware(text, qw, idf):
    text = text.strip()
    if tokens(text) <= LINE:
        return text
    best, at = 0.0, 0
    for m in SENT.finditer(text):
        sc = sum(idf.get(w, 0.0) for w in qw & terms(m.group(0)))
        if sc > best:
            best, at = sc, m.start()
    at = max(0, min(at, len(text) - 4 * LINE))
    return text[at:at + 4 * LINE]


def views(question, order, msgs, idf, df, n_sent):
    qw = terms(question)
    out = {}
    for arm in ("A", "K", "K'"):
        text, got, used = [], set(), 0
        for g in order:
            if g not in msgs:
                continue
            body = msgs[g] if arm == "A" else kwic(msgs[g], qw, idf) if arm == "K" else kwic_aware(msgs[g], qw, idf)
            cost = tokens(body) + 6
            if used + cost > BUDGET:
                break
            text.append(body)
            got.add(g)
            used += cost
        out[arm] = (" ".join(text), got)
    hits = [{"pointer": str(g), "text": msgs[g], "start": 0} for g in order[:50] if g in msgs]
    sel = select_sentences(question, hits, df, n_sent, BUDGET)
    out["S"] = (" ".join(s["text"] for s in sel), {type(next(iter(msgs)))(s["pointer"]) for s in sel})
    return out


def record_stats(msgs):
    df_m = Counter(w for t in msgs.values() for w in terms(t))
    idf = {w: math.log(len(msgs) / c) for w, c in df_m.items()}
    df_s, n = Counter(), 0
    for t in msgs.values():
        for _, _, b in sentences(t):
            df_s.update(set(_terms(b)))
            n += 1
    return idf, df_s, max(n, 1)


def run(name, loader, cache_file, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        msgs = None
        for q in qs:
            if msgs is None:
                msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
                idf, df, n_sent = record_stats(msgs)
            ev = set(q["ev"]) & set(msgs)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            gold = {w for w in terms(q.get("answer") or q.get("rubric") or "") if w not in STOP} - terms(q["question"])
            if not gold:
                continue
            row = {"dataset": name, "conv": conv, "category": q["category"]}
            for arm, (text, got) in views(q["question"], c["hybrid"], msgs, idf, df, n_sent).items():
                row[arm] = len(gold & terms(text)) / len(gold)
                row[arm + " ev"] = len(ev & got) / len(ev)
            rows.append(row)
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions", flush=True)


def report(rows, arms, ref="A"):
    def ci(rs, key):
        g = {}
        for r in rs:
            k = r["conv"] if r["dataset"].startswith(("BEAM", "LoCoMo")) else r["conv"] + str(id(r))
            g.setdefault(k, []).append(key(r))
        return cluster_ci(g)
    out = {}
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        res = {k: ci(rs, lambda r, k=k: r[k]) for k in arms}
        res.update({k + " ev": ci(rs, lambda r, k=k: r[k + " ev"]) for k in arms})
        for k in arms:
            if k != ref:
                res[f"{k}-{ref}"] = ci(rs, lambda r, k=k: r[k] - r[ref])
        out[name] = {"all": res}
        print(f"{name:14s} n={len(rs):5d}  " + "  ".join(f"{k} {res[k][0]:5} ({res[k + ' ev'][0]})" for k in arms))
        print(" " * 22 + "  ".join(f"{k}-{ref} {res[f'{k}-{ref}'][0]} [{res[f'{k}-{ref}'][1]}, {res[f'{k}-{ref}'][2]}]"
                                    for k in arms if k != ref))
        for cat in sorted({r["category"] for r in rs}):
            cr = [r for r in rs if r["category"] == cat]
            out[name][cat] = {k: round(100 * float(np.mean([r[k] for r in cr])), 1) for k in arms}
    return out


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
    print(f"\nanswer words in view at {BUDGET} tokens (evidence recall)")
    out = report(rows, ARMS)
    means = {k: float(np.mean([out[n]["all"][k][0] for n in out])) for k in ARMS}
    choice = max(means, key=means.get)
    out["choice"] = {"means": means, "chosen": choice}
    print("mean over the 7 rows:", {k: round(v, 1) for k, v in means.items()}, "-> chosen:", choice)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
