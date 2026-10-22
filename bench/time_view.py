"""Time in view: protocol in time_view.md.

    python bench/time_view.py prepare --lme longmemeval_s_cleaned.json --out DIR
    (one Haiku reader per DIR/<arm>_<k>.jsonl, writing DIR/answers_<arm>_<k>.jsonl;
     then a blinded judge over DIR/judge_<k>.jsonl, writing DIR/verdicts_<k>.jsonl)
    python bench/time_view.py judge --out DIR
    python bench/time_view.py score --out DIR
    python bench/time_view.py confirm --lme F --locomo F --first DIR --out DIR2   (then readers and judge, arms P and D)
    python bench/time_view.py score --out DIR2 --json time_view_confirm_results.json
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import sys
from datetime import datetime
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

SEED, BUDGET, PER_FILE = 20261022, 2000, 40
STRATA = {"knowledge-update": None, "temporal-reasoning": 80, "single-session-user": 40}
ARMS = ("P", "D", "T")


def lines(question, order, msgs, idf):
    qw, out, used = terms(question), [], 0
    for g in order:
        if g not in msgs:
            continue
        body = kwic_aware(msgs[g], qw, idf)
        cost = tokens(body) + 6
        if used + cost > BUDGET:
            break
        out.append((g, body))
        used += cost
    return out


def render(arm, sel, date):
    if arm == "T":
        sel = sorted(sel, key=lambda x: x[0])
    if arm == "P":
        return "\n".join(b for _, b in sel)
    return "\n".join(f"{date[g]} {b}" for g, b in sel)


def prepare(a):
    import multi
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = {q["question_id"]: q for q in json.loads(Path(a.lme).read_text(encoding="utf-8"))}
    it = iter(pickle.loads((CACHE / "multi_lme_s.pkl").read_bytes()))
    cand = {s: [] for s in STRATA}
    for conv, bs, qs in multi.load_lme(Path(a.lme)):
        q = qs[0]
        msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
        if not set(q["ev"]) & set(msgs):
            continue
        c = next(it)
        if q["category"] not in STRATA:
            continue
        gold = {w for w in terms(q["answer"]) if w not in STOP} - terms(q["question"])
        if not gold:
            continue
        r = raw[conv.split("/")[1]]
        date, mid = {}, 0
        for d, sess in zip(r["haystack_dates"], r["haystack_sessions"]):
            for _ in sess:
                mid += 1
                date[mid] = datetime.strptime(d[:10], "%Y/%m/%d").strftime("%Y-%m-%d")
        idf, _, _ = record_stats(msgs)
        cand[q["category"]].append((conv, q, sorted(gold), lines(q["question"], c["hybrid"], msgs, idf), date))
    items = []
    for s, n in STRATA.items():
        pool = cand[s]
        pick = range(len(pool)) if n is None else sorted(random.Random(SEED).sample(range(len(pool)), n))
        items += [pool[k] for k in pick]
        print(f"{s}: {len(pool)} eligible, {len(pick)} used")
    ids = random.Random(SEED).sample(range(1000, 10000), len(items))
    gold_all, extra = {}, []
    for (conv, q, gold, sel, date), i in zip(items, ids):
        views = {arm: render(arm, sel, date) for arm in ARMS}
        gold_all[f"q{i}"] = {"conv": conv, "stratum": q["category"], "question": q["question"], "answer": q["answer"],
                             "gold": gold, "views": views}
        extra.append(tokens(views["D"]) - tokens(views["P"]))
    for k_arm, arm in enumerate(ARMS):
        order = sorted(gold_all)
        random.Random(SEED + k_arm).shuffle(order)
        for k in range(0, len(order), PER_FILE):
            with open(out / f"{arm}_{k // PER_FILE}.jsonl", "w", encoding="utf-8") as f:
                for i in order[k:k + PER_FILE]:
                    f.write(json.dumps({"id": i, "question": gold_all[i]["question"],
                                        "memory": gold_all[i]["views"][arm]}) + "\n")
    (out / "gold.json").write_text(json.dumps(gold_all), encoding="utf-8")
    print(f"{len(items)} questions; mean P view {np.mean([tokens(g['views']['P']) for g in gold_all.values()]):.0f} "
          f"tokens; dates add {np.mean(extra):.0f}")


def _date(anchor):
    try:
        return datetime.strptime(anchor, "%B-%d-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return "????-??-??"


def confirm(a):
    """Held-out questions, arms P and D only: protocol in time_view.md ("Confirmation")."""
    import multi
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    used = {g["conv"] for g in json.loads((Path(a.first) / "gold.json").read_text()).values()}
    sets = {"LongMemEval-S": (multi.load_lme(Path(a.lme)), "multi_lme_s.pkl",
                              {"multi-session": 80, "temporal-reasoning": None}),
            "LoCoMo": (multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", {"temporal": 80})}
    items = []
    for name, (loader, cache, want) in sets.items():
        it = iter(pickle.loads((CACHE / cache).read_bytes()))
        cand = {s: [] for s in want}
        for conv, bs, qs in loader:
            msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
            date = {x["id"]: _date(x.get("time_anchor", "")) for b in bs for t in b["turns"] for x in t}
            idf = None
            for q in qs:
                if not set(q["ev"]) & set(msgs):
                    continue
                c = next(it)
                if q["category"] not in want or conv in used:
                    continue
                gold = {w for w in terms(q["answer"]) if w not in STOP} - terms(q["question"])
                if not gold:
                    continue
                if idf is None:
                    idf, _, _ = record_stats(msgs)
                cand[q["category"]].append((conv, name, q, sorted(gold), lines(q["question"], c["hybrid"], msgs, idf),
                                            date))
        for s, n in want.items():
            pool = cand[s]
            pick = range(len(pool)) if n is None else sorted(random.Random(SEED).sample(range(len(pool)), n))
            items += [pool[k] for k in pick]
            print(f"{name} {s}: {len(pool)} eligible, {len(pick)} used")
    ids = random.Random(SEED + 7).sample(range(10000, 100000), len(items))
    gold_all = {}
    for (conv, name, q, gold, sel, date), i in zip(items, ids):
        gold_all[f"q{i}"] = {"conv": conv, "stratum": name, "question": q["question"], "answer": q["answer"],
                             "gold": gold, "views": {arm: render(arm, sel, date) for arm in ("P", "D")}}
    for k_arm, arm in enumerate(("P", "D")):
        order = sorted(gold_all)
        random.Random(SEED + k_arm).shuffle(order)
        for k in range(0, len(order), PER_FILE):
            with open(out / f"{arm}_{k // PER_FILE}.jsonl", "w", encoding="utf-8") as f:
                for i in order[k:k + PER_FILE]:
                    f.write(json.dumps({"id": i, "question": gold_all[i]["question"],
                                        "memory": gold_all[i]["views"][arm]}) + "\n")
    (out / "gold.json").write_text(json.dumps(gold_all), encoding="utf-8")
    print(f"{len(items)} questions")


def arms_of(gold):
    return tuple(a for a in ARMS if a in next(iter(gold.values()))["views"])


def answers(out, arms=ARMS):
    ans = {arm: {} for arm in arms}
    for arm in arms:
        for p in out.glob(f"answers_{arm}_*.jsonl"):
            for l in open(p, encoding="utf-8"):
                if l.strip():
                    x = json.loads(l)
                    ans[arm][x["id"]] = str(x["answer"])
    return ans


def judge(a):
    out = Path(a.out)
    gold = json.loads((out / "gold.json").read_text())
    ans = answers(out, arms_of(gold))
    rng = random.Random(SEED)
    rows, key = [], {}
    for i in sorted(gold):
        arms = list(arms_of(gold))
        rng.shuffle(arms)
        key[i] = arms
        rows.append({"id": i, "question": gold[i]["question"], "gold": gold[i]["answer"],
                     "answers": {str(n + 1): ans[arm].get(i, "") for n, arm in enumerate(arms)}})
    for k in range(0, len(rows), 50):
        with open(out / f"judge_{k // 50}.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows[k:k + 50])
    (out / "judge_key.json").write_text(json.dumps(key), encoding="utf-8")


def score(a):
    out = Path(a.out)
    gold = json.loads((out / "gold.json").read_text())
    arms = arms_of(gold)
    ans = answers(out, arms)
    key = json.loads((out / "judge_key.json").read_text()) if (out / "judge_key.json").exists() else {}
    verdict = {}
    for p in out.glob("verdicts_*.jsonl"):
        for l in open(p, encoding="utf-8"):
            if l.strip():
                x = json.loads(l)
                for n, ok in x["correct"].items():
                    verdict[(x["id"], key[x["id"]][int(n) - 1])] = bool(ok)
    rng = np.random.default_rng(0)
    boot = lambda v: list(np.percentile([rng.choice(v, len(v)).mean() for _ in range(4000)], [2.5, 97.5]))  # noqa: E731
    res = {}
    strata = list(dict.fromkeys(g["stratum"] for g in gold.values())) + ["all"]
    for s in strata:
        ids = [i for i in sorted(gold) if s in ("all", gold[i]["stratum"]) and all(i in ans[arm] for arm in arms)]
        use = {arm: np.array([len(set(gold[i]["gold"]) & terms(ans[arm][i])) / len(gold[i]["gold"]) for i in ids])
               for arm in arms}
        fs = {arm: np.array([f1(ans[arm][i], gold[i]["answer"]) for i in ids]) for arm in arms}
        res[s] = {"n": len(ids)}
        print(f"{s} (n={len(ids)})")
        for arm in arms:
            r = {"answer_words": 100 * use[arm].mean(), "f1": 100 * fs[arm].mean()}
            if all((i, arm) in verdict for i in ids):
                r["judge"] = 100 * np.mean([verdict[(i, arm)] for i in ids])
            if arm != "P":
                d = use[arm] - use["P"]
                r["vs_P"] = [100 * d.mean(), *(100 * x for x in boot(d))]
                if "judge" in r:
                    dj = np.array([verdict[(i, arm)] - verdict[(i, "P")] for i in ids], float)
                    r["judge_vs_P"] = [100 * dj.mean(), *(100 * x for x in boot(dj))]
            res[s][arm] = r
            vs = f"  vs P {r['vs_P'][0]:+.1f} [{r['vs_P'][1]:+.1f}, {r['vs_P'][2]:+.1f}]" if "vs_P" in r else ""
            jv = f"  judge {r['judge']:5.1f}" if "judge" in r else ""
            jd = (f" ({r['judge_vs_P'][0]:+.1f} [{r['judge_vs_P'][1]:+.1f}, {r['judge_vs_P'][2]:+.1f}])"
                  if "judge_vs_P" in r else "")
            print(f"  {arm}: answer words {r['answer_words']:5.1f}  F1 {r['f1']:5.1f}{jv}{jd}{vs}")
    (HERE / a.json).write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare", "confirm", "judge", "score"])
    p.add_argument("--lme")
    p.add_argument("--locomo")
    p.add_argument("--first")
    p.add_argument("--json", default="time_view_results.json")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    {"prepare": prepare, "confirm": confirm, "judge": judge, "score": score}[a.cmd](a)
