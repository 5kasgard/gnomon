"""Compartmentalise: send each question to the mechanism that suits it.

From signals visible when the question is asked (no labels, no dataset
name), predict each arm's recall@10 and pick the best:
  overlap   how many of words' top 10 are also meaning's top 10
  sim_top   meaning's best cosine;  sim_gap  its best minus its 10th
  words_n   how many candidates words found (of 200)
  qlen      words in the question;  cues  the five question cues
Arms: words, meaning, hybrid, own first, invariant (gnomon.rank).
Tested leave-one-dataset-out: trained on three datasets, scored on the
fourth, which it never saw; BEAM is trained from 500K and also scored at
1M and 10M.

    python bench/router.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import cluster_ci  # noqa: E402
from beam_learn import CUES  # noqa: E402
from multi_eval import NAMES, load  # noqa: E402

W = json.loads((HERE / "multi_invariant_results.json").read_text())["invariant_weights"]
w_inv = np.array([W.get(n, 0.0) for n in NAMES])
SIM, WORD = NAMES.index("sim*all"), NAMES.index("word*all")
CUE_COLS = [NAMES.index(f"bias*{c}") for c in CUES]
ARMS = ("words", "meaning", "hybrid", "own first", "invariant")


def rankings(d):
    X, pool = d["X"], d["pool"]
    word_order = [pool[i] for i in np.argsort(-X[:, WORD]) if X[i, WORD] > 0]
    return {"words": d.get("words") or word_order,
            "meaning": [pool[i] for i in np.argsort(-X[:, SIM])],
            "hybrid": d["hybrid"], "own first": d["own"],
            "invariant": [pool[i] for i in np.argsort(-(X @ w_inv))]}


def signals(d, r):
    X = d["X"]
    sims = np.sort(X[:, SIM])[::-1]
    overlap = len(set(r["words"][:10]) & set(r["meaning"][:10])) / 10
    qlen = len(d.get("question", "").split()) if d.get("question") else 0
    cues = X[0, CUE_COLS] if len(X) else np.zeros(len(CUE_COLS))
    return np.concatenate([[1.0, overlap, sims[0], sims[0] - sims[min(9, len(sims) - 1)],
                            min(len(r["words"]), 200) / 200, qlen / 30], cues])


def table(data):
    S, Y, conv = [], [], []
    for d in data:
        r = rankings(d)
        S.append(signals(d, r))
        Y.append([len(d["ev"] & (set(r[a][:10]) | d["load"])) / len(d["ev"]) for a in ARMS])
        conv.append(d["conv"])
    return np.array(S), np.array(Y), conv


def fit(S, Y, l2=1.0):
    # one ridge regression per arm: predicted recall from the signals
    A = S.T @ S + l2 * np.eye(S.shape[1])
    return np.linalg.solve(A, S.T @ Y)


def main():
    envs = {"BEAM": ["500K.pkl"], "LoCoMo": ["multi_locomo.pkl"], "LongMemEval-S": ["multi_lme_s.pkl"],
            "ConvoMem": ["multi_convomem.pkl"]}
    extra = {"BEAM 1M": "1M.pkl", "BEAM 10M": "10M.pkl"}
    tabs = {e: table(sum((load(f) for f in fs), [])) for e, fs in envs.items()}
    out = {}
    print(f"{'test set (unseen)':18s}{'n':>6}" + "".join(f"{a:>11s}" for a in ARMS) + f"{'ROUTED':>11s}{'oracle':>9s}")
    for test in list(envs) + list(extra):
        train_envs = [e for e in envs if e != test and not (test.startswith("BEAM") and e == "BEAM")]
        S = np.vstack([tabs[e][0] for e in train_envs])
        # each training environment counts equally
        Y = np.vstack([tabs[e][1] for e in train_envs])
        wts = np.concatenate([np.full(len(tabs[e][1]), 1 / len(tabs[e][1])) for e in train_envs])
        sw = np.sqrt(wts)[:, None]
        B = fit(S * sw, Y * sw)
        St, Yt, conv = tabs[test] if test in tabs else table(load(extra[test]))
        pick = np.argmax(St @ B, axis=1)
        routed = Yt[np.arange(len(Yt)), pick]
        cols = {a: Yt[:, i] for i, a in enumerate(ARMS)}
        g = lambda v: {c: [x for x, cc in zip(v, conv) if cc == c] for c in set(conv)}  # noqa: E731
        ci = cluster_ci(g(routed))
        chosen = {a: int((pick == i).sum()) for i, a in enumerate(ARMS)}
        print(f"{test:18s}{len(Yt):>6}" + "".join(f"{100 * v.mean():>11.1f}" for v in cols.values())
              + f"{100 * routed.mean():>11.1f}{100 * Yt.max(1).mean():>9.1f}   chose {chosen}")
        out[test] = {"arms": {a: float(100 * v.mean()) for a, v in cols.items()}, "routed": ci,
                     "per_question_oracle": float(100 * Yt.max(1).mean()), "chosen": chosen,
                     "trained_on": train_envs}
    (HERE / "router_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
