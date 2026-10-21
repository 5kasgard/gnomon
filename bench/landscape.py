"""Which mechanism suits which kind of question? Recall@10 (with directives
loaded) of every ranking arm, for every category of every dataset, from the
cached features of multi.py / beam_learn.py. No model calls.

Arms: words (Gnomon recall), meaning (static embeddings), hybrid (both,
rank-fused), own words first, invariant (the 8-weight model in gnomon.rank).
Then: how much a perfect router by category would gain over the best
single arm (the ceiling for compartmentalising), per dataset.

    python bench/landscape.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from multi_eval import NAMES, load  # noqa: E402

W = json.loads((HERE / "multi_invariant_results.json").read_text())["invariant_weights"]
w_inv = np.array([W.get(n, 0.0) for n in NAMES])
SIM = NAMES.index("sim*all")


def arms(d):
    pool = d["pool"]
    out = {"meaning": [pool[i] for i in np.argsort(-d["X"][:, SIM])],
           "hybrid": d["hybrid"], "own first": d["own"],
           "invariant": [pool[i] for i in np.argsort(-(d["X"] @ w_inv))]}
    if "words" in d:
        out["words"] = d["words"]
    return out


def main():
    sets = {"BEAM 500K": "500K.pkl", "BEAM 1M": "1M.pkl", "BEAM 10M": "10M.pkl", "LoCoMo": "multi_locomo.pkl",
            "LongMemEval-S": "multi_lme_s.pkl", "ConvoMem": "multi_convomem.pkl"}
    table, out = defaultdict(dict), {}
    for name, f in sets.items():
        data = load(f)
        per = defaultdict(lambda: defaultdict(list))
        for d in data:
            for arm, got in arms(d).items():
                per[d["category"]][arm].append(len(d["ev"] & (set(got[:10]) | d["load"])) / len(d["ev"]))
        print(f"\n== {name}")
        names = ["words", "meaning", "hybrid", "own first", "invariant"]
        print(f"{'category':34s}{'n':>5}" + "".join(f"{a:>11s}" for a in names) + "   best")
        tot = defaultdict(list)
        oracle = []
        for cat in sorted(per):
            vals = {a: 100 * np.mean(v) for a, v in per[cat].items()}
            best = max(vals, key=vals.get)
            n = len(next(iter(per[cat].values())))
            print(f"{cat[:34]:34s}{n:>5}" + "".join(f"{vals.get(a, float('nan')):>11.1f}" for a in names) + f"   {best}")
            for a, v in per[cat].items():
                tot[a] += v
            oracle += per[cat][best]
            table[name][cat] = {"n": n, **{a: round(v, 1) for a, v in vals.items()}, "best": best}
        overall = {a: 100 * np.mean(v) for a, v in tot.items()}
        single = max(overall, key=overall.get)
        print(f"{'ALL':34s}{len(tot[single]):>5}" + "".join(f"{overall.get(a, float('nan')):>11.1f}" for a in names)
              + f"   best single: {single}; perfect router by category: {100 * np.mean(oracle):.1f}")
        out[name] = {"categories": table[name], "all": overall, "router_ceiling": 100 * np.mean(oracle)}
    (HERE / "landscape_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
