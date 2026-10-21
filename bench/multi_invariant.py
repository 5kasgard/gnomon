"""Runs the plan in multi_invariant.md on ConvoMem.

    python bench/multi_invariant.py --convomem DIR [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam_learn import CUES, SIGNALS  # noqa: E402
from multi_eval import NAMES, learned, load, recall_rows, table, train  # noqa: E402

INVARIANT = ("word", "sim", "dense_rank", "surprise", "topic_surprise", "atypical", "number", "own")
KEEP = np.array([n in {f"{s}*all" for s in INVARIANT} for n in NAMES])


def masked(data):
    out = []
    for d in data:
        e = dict(d)
        e["X"] = d["X"] * KEEP
        out.append(e)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json")
    a = p.parse_args()
    envs = [load("500K.pkl"), load("multi_locomo.pkl"), load("multi_lme_s.pkl")]
    test = load("multi_convomem.pkl")
    menvs = [masked(e) for e in envs]
    w_inv = train(menvs, drop=0.4) * KEEP
    w_inv0 = train(menvs, drop=0.0) * KEEP
    w_full = train(envs, drop=0.4)
    arms = {"words": lambda d: d["words"], "hybrid": lambda d: d["hybrid"], "own first": lambda d: d["own"],
            "full, noise": learned(w_full), "invariant": learned(w_inv), "invariant, no noise": learned(w_inv0)}
    out = {"convomem": table(recall_rows(test, arms), "ConvoMem 150-300 conversations (test, never tuned on)",
                             list(arms), by_question=True)}
    out["invariant_weights"] = {n: float(v) for n, v in zip(NAMES, w_inv) if v}
    print("\ninvariant model weights:", {n.split("*")[0]: round(v, 2) for n, v in out["invariant_weights"].items()})
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
