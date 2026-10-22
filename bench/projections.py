"""Where the difficulty sits: protocol in projections.md.

    python bench/projections.py [--json out.json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from multi_eval import load  # noqa: E402
from router import ARMS as RARMS, rankings  # noqa: E402

VARMS = ("A", "K", "K'", "S")
RENVS = {"BEAM 100K": "100K.pkl", "BEAM 500K": "500K.pkl", "BEAM 1M": "1M.pkl", "BEAM 10M": "10M.pkl",
         "LoCoMo": "multi_locomo.pkl", "LongMemEval-S": "multi_lme_s.pkl", "ConvoMem": "multi_convomem.pkl"}


def auc(score, label):
    label = np.asarray(label, bool)
    if label.all() or not label.any():
        return float("nan")
    r = np.argsort(np.argsort(score)) + 1
    return float((r[label].sum() - label.sum() * (label.sum() + 1) / 2) / (label.sum() * (~label).sum()))


def account(family, envs, arms):
    """envs: name -> (Y questions x arms, in percent; optional agreement per question)."""
    fixed = int(np.argmax(np.mean([Y.mean(0) for Y, _ in envs.values()], axis=0)))
    out = {"fixed_arm": arms[fixed]}
    print(f"\n== {family}: one fixed arm = {arms[fixed]}")
    print(f"{'':16s}{'fixed':>7s}{'per env':>9s}{'per q':>7s}   {'env gap':>8s}{'q gap':>7s}{'unreach':>8s}"
          f"   {'none':>5s}{'tie':>5s}   {'difficulty var':>14s}{'PC1 of contrasts':>17s}   agreement AUC (gain, hard)")
    for name, (Y, agree) in envs.items():
        f, e = Y[:, fixed].mean(), Y.mean(0).max()
        best_env = int(Y.mean(0).argmax())
        q = Y.max(1).mean()
        none = float((Y.max(1) == 0).mean())
        tie = float((Y.max(1) == Y.min(1)).mean())
        level = Y.mean(1, keepdims=True)
        C = Y - level
        tot = ((Y - Y.mean()) ** 2).sum()
        diff_share = float(((level - Y.mean()) ** 2).sum() * Y.shape[1] / tot) if tot else float("nan")
        C0 = C - C.mean(0)
        sv = np.linalg.svd(C0, compute_uv=False) ** 2
        pc1 = float(sv[0] / sv.sum()) if sv.sum() else float("nan")
        row = {"fixed": f, "per_env": e, "per_question": q, "env_gap": e - f, "question_gap": q - e,
               "unreachable": 100 - q, "none": none, "tie": tie, "difficulty_share": diff_share,
               "contrast_pc1": pc1, "best_env_arm": arms[best_env]}
        tail = ""
        if agree is not None:
            gain = Y.max(1) > Y[:, best_env]
            hard = level[:, 0] < np.median(level)
            row["agreement_auc_gain"] = auc(-agree, gain)
            row["agreement_auc_hard"] = auc(-agree, hard)
            tail = f"   {row['agreement_auc_gain']:.2f}, {row['agreement_auc_hard']:.2f}"
        out[name] = row
        print(f"{name:16s}{f:7.1f}{e:9.1f}{q:7.1f}   {e - f:8.1f}{q - e:7.1f}{100 - q:8.1f}   "
              f"{100 * none:4.0f}%{100 * tie:4.0f}%   {100 * diff_share:13.0f}%{100 * pc1:16.0f}%" + tail)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json")
    a = p.parse_args()
    renvs = {}
    for name, f in RENVS.items():
        Y, agree = [], []
        for d in load(f):
            r = rankings(d)
            Y.append([100 * len(d["ev"] & (set(r[x][:10]) | d["load"])) / len(d["ev"]) for x in RARMS])
            tops = [set(r[x][:10]) for x in RARMS]
            agree.append(np.mean([len(s & t) / 10 for s, t in itertools.combinations(tops, 2)]))
        renvs[name] = (np.array(Y), np.array(agree))
    rows = json.loads((HERE / "views_forage_results.rows.json").read_text())
    rows += json.loads((HERE / "views_qasper_results.rows.json").read_text())
    venvs = {n: (np.array([[100 * r[x] for x in VARMS] for r in rows if r["dataset"] == n]), None)
             for n in dict.fromkeys(r["dataset"] for r in rows)}
    out = {"ranking": account("Ranking (recall@10)", renvs, RARMS),
           "views": account("Views (answer words in 2,000 tokens)", venvs, VARMS)}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
