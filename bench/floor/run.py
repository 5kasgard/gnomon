"""The floor: many cheap workers against one capable one, on real data.

Two real, labelled workloads (public data, ground truth from people):
  sms   5,574 SMS messages, spam or not (UCI SMS Spam Collection)
  yelp  10,000 Yelp reviews, the star rating the reviewer gave (1-5)

Workers, cheapest first:
  rules    a program written once from general knowledge, never tuned on the data
  voters   naive Bayes: every word a dumb voter, votes added
  stumps   300 one-question decision stumps, boosted (a swarm of dumb nodes)
  linear   logistic regression on word and character n-grams
  llm      Claude Haiku reading each item and judging it (measured on 300 items per task)
and two ways of combining them:
  bottled  linear trained on the LLM's own labels for 300 items (no human labels)
  floor    the cheap worker answers what it is confident about; the rest go to the LLM

Every number is measured on items the worker never saw. Costs are per million items,
from published prices (gnomon.firm.TIERS) and measured CPU time.

  python bench/floor/run.py
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import AdaBoostClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline, make_union
from sklearn.tree import DecisionTreeClassifier

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "python"))
from gnomon.firm import TIERS  # noqa: E402

CPU_USD_PER_HOUR = 0.04  # one cloud vCPU, on demand (assumption, stated)
BATCH = 25  # items per LLM request: batching to 100 cost under 2 points (arXiv 2604.03684)
INSTRUCTION_TOKENS = 250  # task instruction, sent once per request


def load():
    sms = []
    for line in open(HERE / "sms.tsv", encoding="utf-8"):
        y, t = line.rstrip("\n").split("\t", 1)
        sms.append((t, 1 if y == "spam" else 0))
    yelp = [(r["text"], int(r["stars"])) for r in csv.DictReader(open(HERE / "yelp.csv", encoding="utf-8"))]
    return {"sms": sms, "yelp": yelp}


# ------------------------------------------------------------ code workers

SPAM_WORDS = ("free", "win", "won", "winner", "prize", "claim", "urgent", "cash", "award", "guaranteed",
              "ringtone", "voucher", "offer", "subscribe", "unsubscribe", "txt", "text stop", "reply stop",
              "mobile", "bonus", "selected", "congratulations", "credit", "loan", "click", "www", "http",
              "call now", "lottery", "chat", "sexy", "tone", "18+", "per min", "p/min", "ppm", "150p")


def rules_sms(text: str) -> int:
    t = text.lower()
    score = sum(w in t for w in SPAM_WORDS)
    score += 2 * bool(re.search(r"\d{9,}", t))  # a phone number
    score += bool(re.search(r"\b\d{5}\b", t))  # a short code
    score += bool(re.search(r"[£$€]\s?\d", t))  # money
    return int(score >= 2)


POS = ("great", "amazing", "awesome", "excellent", "love", "loved", "best", "delicious", "perfect", "fantastic",
       "wonderful", "favorite", "favourite", "friendly", "fresh", "recommend", "good", "nice", "tasty", "happy",
       "enjoy", "enjoyed", "outstanding", "incredible", "yum", "fun", "beautiful", "solid", "glad")
NEG = ("bad", "terrible", "horrible", "awful", "worst", "rude", "disgusting", "bland", "cold", "slow", "dirty",
       "never", "disappointed", "disappointing", "mediocre", "overpriced", "poor", "gross", "waste", "wrong",
       "sick", "stale", "avoid", "meh", "unfortunately", "nothing special", "not good", "won't be back")


def rules_yelp(text: str) -> int:
    t = text.lower()
    pos = sum(t.count(w) for w in POS)
    neg = sum(t.count(w) for w in NEG)
    s = (pos - 1.5 * neg) / math.sqrt(1 + pos + neg)
    # Cut points fixed in advance, not tuned on the data.
    return 1 if s < -1.2 else 2 if s < -0.3 else 3 if s < 0.5 else 4 if s < 1.6 else 5


RULES = {"sms": rules_sms, "yelp": rules_yelp}


# -------------------------------------------------------------- learners

def features():
    return make_union(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                      TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True))


def learner(kind: str):
    if kind == "voters":
        return make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=1), MultinomialNB(alpha=0.1))
    if kind == "stumps":
        return make_pipeline(TfidfVectorizer(ngram_range=(1, 1), min_df=2),
                             AdaBoostClassifier(DecisionTreeClassifier(max_depth=1), n_estimators=300,
                                                random_state=0))
    return make_pipeline(features(), LogisticRegression(C=8.0, max_iter=2000, class_weight="balanced"))


# ------------------------------------------------------------------ costs

def tokens(text: str) -> float:
    return len(text) / 4.0  # about four characters per token in English


def llm_usd_per_item(texts, tier: str, batch_api: bool = False) -> float:
    p = TIERS[tier].price
    inp = np.mean([tokens(t[:1200]) + 8 for t in texts]) + INSTRUCTION_TOKENS / BATCH
    out = 6.0  # {"id": n, "label": k}
    usd = (inp * p.input + out * p.output) / 1e6
    return usd * (0.5 if batch_api else 1.0)


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """95% interval for a proportion k/n (Wilson)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def cpu_usd(seconds_per_item: float) -> float:
    return seconds_per_item * CPU_USD_PER_HOUR / 3600


def scores(task, y, p):
    y, p = np.asarray(y), np.asarray(p)
    out = {"acc": float((y == p).mean())}
    if task == "sms":
        tp = ((p == 1) & (y == 1)).sum()
        out["spam_recall"] = float(tp / max((y == 1).sum(), 1))
        out["spam_precision"] = float(tp / max((p == 1).sum(), 1))
    else:
        out["within_one"] = float((np.abs(y - p) <= 1).mean())
    return out


def timed_predict(model, texts):
    t0 = time.perf_counter()
    pred = model.predict(texts)
    return pred, (time.perf_counter() - t0) / len(texts)


def run_task(task, rows, split, llm):
    probe = split["probe"]
    pool = split["pool"]
    half = len(pool) // 2
    train_ids, eval_ids = pool[:half], pool[half:]
    X = [r[0] for r in rows]
    Y = np.array([r[1] for r in rows])
    ev_x, ev_y = [X[i] for i in eval_ids], Y[eval_ids]
    res = {"task": task, "items": len(rows), "eval_items": len(eval_ids), "workers": []}

    def add(name, labels, pred, sec, extra_usd=0.0, note=""):
        res["workers"].append({"worker": name, "labels": labels, **scores(task, ev_y, pred),
                               "usd_per_million": round(cpu_usd(sec) * 1e6 + extra_usd, 2),
                               "items_per_cpu_second": round(1 / sec) if sec > 0 else None, "note": note})

    maj = np.bincount(Y[train_ids]).argmax()
    add("majority", "-", np.full(len(ev_y), maj), 1e-9, note="always the commonest answer")
    t0 = time.perf_counter()
    rp = [RULES[task](t) for t in ev_x]
    add("rules", "0", rp, (time.perf_counter() - t0) / len(ev_x), note="program from general knowledge")

    for kind in ("voters", "stumps", "linear"):
        for n in (50, 200, 1000, len(train_ids)):
            ids = train_ids[:n]
            if len(set(Y[ids])) < 2:
                continue
            m = learner(kind).fit([X[i] for i in ids], Y[ids])
            p, sec = timed_predict(m, ev_x)
            add(kind, f"{n} human", p, sec)

    llm_usd = llm_usd_per_item(X, "haiku")
    if llm:
        ly = np.array([llm[i] for i in probe])
        py = Y[probe]
        res["llm"] = {"worker": "llm (haiku)", **scores(task, py, ly), "measured_on": len(probe),
                      "acc_95": wilson(int((ly == py).sum()), len(probe)),
                      "usd_per_million": round(llm_usd * 1e6, 2),
                      "usd_per_million_batch_api": round(llm_usd_per_item(X, "haiku", True) * 1e6, 2),
                      "sonnet_usd_per_million": round(llm_usd_per_item(X, "sonnet") * 1e6, 2),
                      "opus_usd_per_million": round(llm_usd_per_item(X, "opus") * 1e6, 2)}
        # Bottled: the linear learner trained only on the LLM's labels.
        m = learner("linear").fit([X[i] for i in probe], ly)
        p, sec = timed_predict(m, ev_x)
        add("bottled", f"{len(probe)} llm", p, sec, extra_usd=0.0,
            note=f"one-off labelling cost ${len(probe) * llm_usd:.4f}")
        res["floor"] = floor(task, X, Y, probe, ly, train_ids, llm_usd)
    return res


def floor(task, X, Y, probe, ly, train_ids, llm_usd):
    """The cheap worker answers when confident; the rest go to the LLM.
    Measured on the probe items, where the LLM's real answers are known.
    Two cheap workers: one trained on 1,000 human labels, and one trained
    only on LLM labels (two folds over the probe, so no item is scored by a
    model that saw its LLM label)."""
    probe = np.array(probe)
    py = Y[probe]
    out = {}
    lin = learner("linear").fit([X[i] for i in train_ids[:1000]], Y[train_ids[:1000]])
    conf_h = lin.predict_proba([X[i] for i in probe])
    pred_h = lin.classes_[conf_h.argmax(1)]
    conf_h = conf_h.max(1)
    # Cross-fitted bottled worker.
    pred_b = np.empty(len(probe), dtype=int)
    conf_b = np.empty(len(probe))
    folds = [np.arange(len(probe)) % 2 == k for k in (0, 1)]
    for k in (0, 1):
        tr, te = ~folds[k], folds[k]
        if len(set(ly[tr])) < 2:
            continue
        m = learner("linear").fit([X[i] for i in probe[tr]], ly[tr])
        pr = m.predict_proba([X[i] for i in probe[te]])
        pred_b[te] = m.classes_[pr.argmax(1)]
        conf_b[te] = pr.max(1)
    llm_acc = float((ly == py).mean())
    for name, pred, conf in (("1000 human labels", pred_h, conf_h), ("bottled from the llm", pred_b, conf_b)):
        rows = []
        for share in (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0):
            k = int(round(share * len(probe)))
            order = np.argsort(conf)  # least confident first
            esc = np.zeros(len(probe), bool)
            esc[order[:k]] = True
            final = np.where(esc, ly, pred)
            rows.append({"to_llm": share, "acc": round(float((final == py).mean()), 4),
                         "usd_per_million": round(share * llm_usd * 1e6, 2)})
        best = max(rows, key=lambda r: (r["acc"], -r["to_llm"]))
        match = next((r for r in rows if r["acc"] >= llm_acc), None)
        out[name] = {"curve": rows, "best": best, "cheapest_matching_llm": match}
    out["llm_alone"] = {"acc": round(llm_acc, 4), "usd_per_million": round(llm_usd * 1e6, 2)}
    return out


def main():
    data = load()
    report = {}
    for task in ("sms", "yelp"):
        split = json.load(open(HERE / f"split_{task}.json"))
        f = HERE / f"llm_{task}.jsonl"
        llm = None
        if f.exists():
            llm = {}
            for line in open(f):
                if line.strip():
                    r = json.loads(line)
                    llm[r["id"]] = int(r["label"])
            missing = [i for i in split["probe"] if i not in llm]
            if missing:
                print(f"{task}: {len(missing)} probe items lack an LLM label; skipping the LLM rows")
                llm = None
        report[task] = run_task(task, data[task], split, llm)
    json.dump(report, open(HERE / "results.json", "w"), indent=1)
    for task, r in report.items():
        print(f"\n== {task}: {r['items']} items, scored on {r['eval_items']} unseen ==")
        key = "spam_recall" if task == "sms" else "within_one"
        print(f"{'worker':<10} {'labels':<11} {'acc':>6} {key:>12} {'$/1M items':>11} {'items/cpu-s':>11}")
        for w in r["workers"]:
            print(f"{w['worker']:<10} {w['labels']:<11} {w['acc']:>6.3f} {w.get(key, 0):>12.3f} "
                  f"{w['usd_per_million']:>11.2f} {w['items_per_cpu_second'] or 0:>11}")
        if "llm" in r:
            L = r["llm"]
            print(f"{'llm':<10} {'-':<11} {L['acc']:>6.3f} {L.get(key, 0):>12.3f} {L['usd_per_million']:>11.2f}"
                  f"   (measured on {L['measured_on']}, 95% {L['acc_95']}; sonnet ${L['sonnet_usd_per_million']}, "
                  f"opus ${L['opus_usd_per_million']})")
            for name, fl in r["floor"].items():
                if name == "llm_alone":
                    continue
                print(f"floor, {name}: " + ", ".join(f"{int(x['to_llm'] * 100)}%→{x['acc']:.3f}" for x in fl["curve"]))


if __name__ == "__main__":
    main()
