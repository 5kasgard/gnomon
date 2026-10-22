"""Dynamic budget: protocol in dynamic_budget.md.

    python bench/dynamic_budget.py --beam BEAM --locomo F --lme F --convomem DIR
"""
from __future__ import annotations

import argparse
import hashlib
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

FIXED, CAP, TOP = (250, 500, 1000, 1500, 2000, 3000), 2000, 50
ALPHAS = [round(0.5 + 0.05 * i, 2) for i in range(10)]
REPS = 1000


def split(conv: str) -> str:
    return "dev" if hashlib.sha1(conv.encode()).digest()[0] % 2 == 0 else "test"


def collect(name, loader, cache_file, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        msgs = None
        for q in qs:
            if msgs is None:
                msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
                idf, _, _ = record_stats(msgs)
            ev = set(q["ev"]) & set(msgs)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            gold = {w for w in terms(q.get("answer") or q.get("rubric") or "") if w not in STOP} - terms(q["question"])
            if not gold:
                continue
            nc = c["X"].shape[1] // 11
            sim = {g: float(c["X"][i, nc]) for i, g in enumerate(c["pool"])}
            qw, lines, used = terms(q["question"]), [], 0
            for g in c["hybrid"]:
                if g not in msgs:
                    continue
                body = kwic_aware(msgs[g], qw, idf)
                cost = tokens(body) + 6
                if used + cost > max(FIXED):
                    break
                lines.append((cost, sim.get(g, 0.0), terms(body) & gold))
                used += cost
            rows.append({"dataset": name, "conv": conv, "split": split(conv), "n_gold": len(gold), "lines": lines})
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions", flush=True)


def outcome(r, keep):
    """(tokens, answer words in view) for the kept line indices, in order, within CAP unless fixed."""
    got, used = set(), 0
    for i in keep:
        got |= r["lines"][i][2]
        used += r["lines"][i][0]
    return used, len(got) / r["n_gold"]


def fixed(r, b):
    keep, used = [], 0
    for i, (cost, _, _) in enumerate(r["lines"]):
        if used + cost > b:
            break
        keep.append(i)
        used += cost
    return outcome(r, keep)


def capped(r, ok):
    keep, used = [], 0
    for i, (cost, s, _) in enumerate(r["lines"]):
        if used + cost > CAP:
            break
        if ok(i, s):
            keep.append(i)
            used += cost
    return outcome(r, keep)


def knee_threshold(r):
    s = np.sort([x[1] for x in r["lines"][:TOP]])[::-1]
    if len(s) < 3:
        return -1.0
    x = np.arange(len(s))
    chord = s[0] + (s[-1] - s[0]) * x / (len(s) - 1)
    return float(s[int(np.argmax(chord - s))])


def rule(r, kind, alpha=None):
    if kind == "knee":
        t = knee_threshold(r)
        return capped(r, lambda i, s: s >= t)
    best = max([x[1] for x in r["lines"][:TOP]] or [0.0])
    return capped(r, lambda i, s: s >= alpha * best)


def evaluate(rows, f):
    """{record: (gain, rule tokens, rule words)} for rule f, against the fixed frontier."""
    out = {}
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        pts = np.array([[np.mean([r["fx"][b][k] for r in rs]) for k in (0, 1)] for b in FIXED])
        t, w = np.mean([f(r)[0] for r in rs]), np.mean([f(r)[1] for r in rs])
        out[name] = (100 * (w - np.interp(t, pts[:, 0], pts[:, 1])), t, 100 * w)
    return out


def boot(rows, f, reps=REPS):
    rng = random.Random(0)
    by = {}
    for r in rows:
        key = r["conv"] if r["dataset"].startswith(("BEAM", "LoCoMo")) else r["conv"] + str(id(r))
        by.setdefault(r["dataset"], {}).setdefault(key, []).append(r)
    pooled, per = [], {n: [] for n in by}
    for _ in range(reps):
        sample = []
        for n, groups in by.items():
            keys = list(groups)
            sample += [r for k in (rng.choice(keys) for _ in keys) for r in groups[k]]
        e = evaluate(sample, f)
        pooled.append(np.mean([v[0] for v in e.values()]))
        for n in e:
            per[n].append(e[n][0])
    ci = lambda v: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]  # noqa: E731
    return ci(pooled), {n: ci(v) for n, v in per.items()}


def main():
    import multi
    p = argparse.ArgumentParser()
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--json", default="dynamic_budget_results.json")
    a = p.parse_args()
    rows = []
    for size in ("100K", "1M", "10M"):
        collect(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", rows)
    collect("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", rows)
    collect("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", rows)
    collect("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))), "multi_convomem.pkl",
            rows)
    for r in rows:
        r["fx"] = {b: fixed(r, b) for b in FIXED}
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]
    tune = {al: float(np.mean([v[0] for v in evaluate(dev, lambda r, al=al: rule(r, "rel", al)).values()]))
            for al in ALPHAS}
    alpha = max(tune, key=tune.get)
    print("dev pooled gain by alpha:", {k: round(v, 2) for k, v in tune.items()}, "-> alpha", alpha)
    res = {"alpha": alpha, "dev_tuning": tune, "fixed": {}, "rules": {}}
    for name in dict.fromkeys(r["dataset"] for r in test):
        rs = [r for r in test if r["dataset"] == name]
        res["fixed"][name] = {b: [float(np.mean([r["fx"][b][0] for r in rs])),
                                  100 * float(np.mean([r["fx"][b][1] for r in rs]))] for b in FIXED}
    for kind, f in (("knee", lambda r: rule(r, "knee")), (f"relative {alpha}", lambda r: rule(r, "rel", alpha))):
        e = evaluate(test, f)
        pooled_ci, per_ci = boot(test, f)
        pooled = float(np.mean([v[0] for v in e.values()]))
        res["rules"][kind] = {"pooled_gain": pooled, "pooled_ci": pooled_ci,
                              "records": {n: {"gain": e[n][0], "ci": per_ci[n], "tokens": e[n][1], "words": e[n][2]}
                                          for n in e}}
        print(f"\n{kind}: pooled gain {pooled:+.2f} [{pooled_ci[0]:+.2f}, {pooled_ci[1]:+.2f}]")
        for n, (g, t, w) in e.items():
            fx2 = res["fixed"][n][2000]
            print(f"  {n:14s} n={sum(r['dataset'] == n for r in test):5d}  tokens {t:6.0f} (fixed 2K {fx2[0]:6.0f})  "
                  f"words {w:5.1f} (fixed 2K {fx2[1]:5.1f})  gain {g:+.2f} [{per_ci[n][0]:+.2f}, {per_ci[n][1]:+.2f}]")
    (HERE / a.json).write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
