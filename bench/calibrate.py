"""Compartmentalise per memory, from use: with n questions of feedback from
one environment (did the chosen evidence get used?), pick the arm that did
best on them and score it on that environment's other questions. 200
random draws per n. No model calls.

    python bench/calibrate.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from multi_eval import load  # noqa: E402
from router import ARMS, table  # noqa: E402


GATED = True


def main():
    sets = {"BEAM 500K": "500K.pkl", "BEAM 1M": "1M.pkl", "LoCoMo": "multi_locomo.pkl",
            "LongMemEval-S": "multi_lme_s.pkl", "ConvoMem": "multi_convomem.pkl"}
    ns = (5, 10, 20, 50)
    out = {}
    print(f"{'environment':16s}{'invariant':>10s}{'best arm':>10s}" + "".join(f"{f'n={n}':>9s}" for n in ns))
    for name, f in sets.items():
        _, Y, _ = table(load(f))
        inv = Y[:, ARMS.index("invariant")].mean()
        best = Y.mean(0).max()
        rng = random.Random(0)
        res = {}
        for n in ns:
            vals = []
            for _ in range(200):
                idx = list(range(len(Y)))
                rng.shuffle(idx)
                fb, rest = idx[:n], idx[n:]
                # ties go to the invariant model, the default
                d0 = ARMS.index("invariant")
                arm = d0
                if GATED and len(fb) < 10:
                    pass  # too few observations: keep the default, as gnomon.rank.Calibration does
                elif GATED:
                    # switch only on a clear paired win: mean difference over
                    # the feedback questions more than 2 standard errors above 0
                    best_z = 2.0
                    for a in range(len(ARMS)):
                        diff = Y[fb, a] - Y[fb, d0]
                        se = diff.std(ddof=1) / np.sqrt(len(fb)) if len(fb) > 1 else np.inf
                        z = diff.mean() / se if se > 0 else (np.inf if diff.mean() > 0 else 0)
                        if z > best_z:
                            best_z, arm = z, a
                else:
                    means = Y[fb].mean(0)
                    arm = d0 if means[d0] >= means.max() - 1e-9 else int(means.argmax())
                vals.append(Y[rest, arm].mean())
            res[n] = (float(100 * np.mean(vals)), float(100 * np.percentile(vals, 5)))
        out[name] = {"invariant": float(100 * inv), "best_arm": float(100 * best), "with_feedback": res}
        print(f"{name:16s}{100 * inv:>10.1f}{100 * best:>10.1f}" + "".join(f"{res[n][0]:>9.1f}" for n in ns)
              + "   (5th percentile at n=20: %.1f)" % res[20][1])
    (HERE / ("calibrate_results.json" if GATED else "calibrate_ungated.json")).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
