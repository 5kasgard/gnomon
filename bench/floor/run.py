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
    # Sentiment: the same reviews, 4-5 stars positive, 1-2 negative, 3 dropped
    # (kept as None so item ids, and so the probe, stay the same).
    sentiment = [(t, None if y == 3 else int(y >= 4)) for t, y in yelp]
    return {"sms": sms, "yelp": yelp, "sentiment": sentiment}


def to_sentiment(stars: int):
    return None if stars == 3 else int(stars >= 4)


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


def rules_sentiment(text: str) -> int:
    return int(rules_yelp(text) >= 4) if rules_yelp(text) != 3 else int(sum(text.lower().count(w) for w in POS)
                                                                      >= sum(text.lower().count(w) for w in NEG))


RULES = {"sms": rules_sms, "yelp": rules_yelp, "sentiment": rules_sentiment}


class Mycel:
    """Mycel-D (github.com/5kasgard/mycel) on a 64-dimensional SVD of word
    TF-IDF. Two configurations, both reported (PROTOCOL.md): the one first
    fixed here ("first", which an independent review found barely learnt
    its own training data), and the one Mycel's own round-5 protocol froze
    (frozen/r5_frozen.json: shared pool of 512 nuclei, B=24, M=16, T=512,
    s=3.0, growth)."""

    def __init__(self, path, variant="r5"):
        sys.path.insert(0, str(path))
        from dmycel import MycelCo, MycelD  # noqa: E402
        from sklearn.decomposition import TruncatedSVD
        self.MycelD, self.MycelCo, self.SVD, self.variant = MycelD, MycelCo, TruncatedSVD, variant

    def fit(self, texts, y):
        self.vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        Xs = self.vec.fit_transform(texts)
        self.svd = self.SVD(64, random_state=0).fit(Xs)
        self.classes_ = np.unique(y)
        yi = np.searchsorted(self.classes_, y)
        if self.variant == "first":
            self.m = self.MycelD(K=len(self.classes_), C=64, B=8, M=16, grow=1, epochs=30, seed=0)
        else:
            self.m = self.MycelCo(len(self.classes_), P=512, B=24, M=16, T=512, s=3.0, grow=1, epochs=30, seed=0)
        self.m.fit(self.svd.transform(Xs), yi)
        return self

    def predict(self, texts):
        return self.classes_[self.m.predict(self.svd.transform(self.vec.transform(texts)))]


MYCEL_PATH = Path("/home/claude/5kasgard/mycel")


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
    out = 12.0  # {"id": n, "label": k}
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
    if task in ("sms", "sentiment"):
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
    keep = [i for i in range(len(rows)) if rows[i][1] is not None]
    keep_set = set(keep)
    probe = [i for i in split["probe"] if i in keep_set]
    pool = [i for i in split["pool"] if i in keep_set]
    half = len(pool) // 2
    train_ids, eval_ids = pool[:half], pool[half:]
    X = [r[0] for r in rows]
    Y = np.array([-1 if r[1] is None else r[1] for r in rows])
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
    if (MYCEL_PATH / "libdcore.so").exists():
        for variant in ("first", "r5"):
            for n in (1000, len(train_ids)):
                ids = train_ids[:n]
                m = Mycel(MYCEL_PATH, variant).fit([X[i] for i in ids], Y[ids])
                p, sec = timed_predict(m, ev_x)
                add("mycel" if variant == "r5" else "mycel-1st", f"{n} human", p, sec,
                    note=f"Mycel on SVD-64 of TF-IDF, {variant} configuration")

    llm_usd = llm_usd_per_item(X, "haiku")
    if llm:
        if task == "sentiment":
            stars = dict(llm)
            llm = {i: to_sentiment(v) for i, v in stars.items()}
            res["llm_said_3"] = sum(1 for i in probe if stars[i] == 3)
        ly = np.array([-1 if llm[i] is None else llm[i] for i in probe])
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
        if task == "sentiment":
            # The LLM was asked for stars, and said 3 on some items that are
            # positive or negative: scored as wrong above. How much that
            # matters, both ways (independent review, 2026-10-21):
            res["sensitivity"] = {}
            for name, three in (("llm 3 read as positive", 1), ("llm 3 read as negative", 0)):
                alt = np.array([three if stars[i] == 3 else llm[i] for i in probe])
                fl = floor(task, X, Y, probe, alt, train_ids, llm_usd)
                res["sensitivity"][name] = {"llm_acc": fl["llm_alone"]["acc"],
                                            **{k: v["held_out"] for k, v in fl.items() if k != "llm_alone"}}
    return res


SHARES = (0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0)


def floor(task, X, Y, probe, ly, train_ids, llm_usd):
    """The cheap worker answers; its least confident items go to the LLM.
    Measured on the probe, where the LLM's real answers are known. The
    escalation share is chosen on one half of the probe and scored on the
    other, both ways (PROTOCOL.md). Two cheap workers: one trained on 1,000
    human labels from the pool, one trained only on LLM labels (cross-fitted
    over the same two halves, so no item is scored by a model that saw it)."""
    probe = np.array(probe)
    py = Y[probe]
    halves = [np.arange(len(probe)) % 2 == k for k in (0, 1)]
    lin = learner("linear").fit([X[i] for i in train_ids[:1000]], Y[train_ids[:1000]])
    pr = lin.predict_proba([X[i] for i in probe])
    workers = {"1000 human labels": (lin.classes_[pr.argmax(1)], pr.max(1))}
    pred_b = np.empty(len(probe), dtype=int)
    conf_b = np.zeros(len(probe))
    for k in (0, 1):
        tr, te = ~halves[k], halves[k]
        ok = tr & (ly >= 0)
        m = learner("linear").fit([X[i] for i in probe[ok]], ly[ok])
        q = m.predict_proba([X[i] for i in probe[te]])
        pred_b[te] = m.classes_[q.argmax(1)]
        conf_b[te] = q.max(1)
    workers["bottled from the llm"] = (pred_b, conf_b)

    def acc_at(pred, conf, mask, share):
        idx = np.where(mask)[0]
        k = int(round(share * len(idx)))
        esc = np.zeros(len(probe), bool)
        esc[idx[np.argsort(conf[idx])[:k]]] = True
        final = np.where(esc, ly, pred)
        return float((final[mask] == py[mask]).mean())

    out = {"llm_alone": {"acc": round(float((ly == py).mean()), 4), "acc_95": wilson(int((ly == py).sum()), len(py)),
                         "usd_per_million": round(llm_usd * 1e6, 2)}}
    for name, (pred, conf) in workers.items():
        everything = np.ones(len(probe), bool)
        curve = [{"to_llm": sh, "acc": round(acc_at(pred, conf, everything, sh), 4),
                  "usd_per_million": round(sh * llm_usd * 1e6, 2)} for sh in SHARES]
        held = []
        for k in (0, 1):
            choose, score = halves[k], halves[1 - k]
            target = float((ly[choose] == py[choose]).mean()) - 0.01
            share = next((sh for sh in SHARES if acc_at(pred, conf, choose, sh) >= target), 1.0)
            held.append((share, acc_at(pred, conf, score, share)))
        share = float(np.mean([h[0] for h in held]))
        acc = float(np.mean([h[1] for h in held]))
        n_right = int(round(acc * len(probe)))
        out[name] = {"in_sample_curve": curve,
                     "held_out": {"shares_chosen": [h[0] for h in held], "acc": round(acc, 4),
                                  "acc_95": wilson(n_right, len(probe)),
                                  "usd_per_million": round(share * llm_usd * 1e6, 2)}}
    return out


def main():
    data = load()
    report = {}
    for task in ("sms", "yelp", "sentiment"):
        base = "yelp" if task == "sentiment" else task
        split = json.load(open(HERE / f"split_{base}.json"))
        f = HERE / f"llm_{base}.jsonl"
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
        if "sensitivity" in r:
            print(f"\n{task}: the LLM said 3 stars on {r['llm_said_3']} probe items; if those are read as:")
            for k, v in r["sensitivity"].items():
                print(f"  {k}: llm {v['llm_acc']:.3f}; " + "; ".join(
                    f"floor ({w}) {h['acc']:.3f} at ${h['usd_per_million']}/M" for w, h in v.items() if w != "llm_acc"))
        print(f"\n== {task}: {r['items']} items, scored on {r['eval_items']} unseen ==")
        key = {"sms": "spam_recall", "yelp": "within_one"}.get(task, "acc")
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
                h = fl["held_out"]
                print(f"floor, {name}: held out {h['acc']:.3f} {h['acc_95']} at ${h['usd_per_million']}/M "
                      f"(shares {h['shares_chosen']}); in sample: "
                      + ", ".join(f"{int(x['to_llm'] * 100)}%→{x['acc']:.3f}" for x in fl["in_sample_curve"]))


if __name__ == "__main__":
    main()
