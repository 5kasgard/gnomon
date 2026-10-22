"""Own-history labels against another history's: protocol in situation_product.md.

    python bench/situation_own.py --convomem FILE --test DIR_test --dev DIR_dev2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from situation_distil import chosen, featurise, train  # noqa: E402

N = 1000


def main():
    import multi
    from model2vec import StaticModel
    p = argparse.ArgumentParser()
    p.add_argument("--convomem", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--dev", required=True)
    a = p.parse_args()
    model = StaticModel.from_pretrained(multi.MODEL)
    dev = json.loads((Path(a.dev) / "history_0.map.json").read_text())
    ydev = chosen(Path(a.dev), 0, len(dev["text"]))
    Xdev = featurise(dev["text"], model)
    cases = list(multi.load_convomem([Path(a.convomem)], per_file=4, q_per_case=10 ** 6))
    tests = []
    for i, (conv, bs, qs) in enumerate(cases):
        mp = json.loads((Path(a.test) / f"history_{i}.map.json").read_text())
        assert mp["conv"] == conv
        tests.append((mp, chosen(Path(a.test), i, len(mp["text"])), featurise(mp["text"], model), qs))
    res = {}
    for arm in ("other", "own", "own + labels kept"):
        for seed in range(3):
            hit = q = hit_u = q_u = tok = 0
            if arm == "other":
                idx = np.random.default_rng(seed).choice(len(ydev), N, replace=False)
                w, share = train(Xdev[idx], ydev[idx].astype(np.float32)), float(ydev[idx].mean())
            for mp, y, X, qs in tests:
                n = len(mp["text"])
                lab = np.random.default_rng(seed).choice(n, N, replace=False)
                if arm != "other":
                    w, share = train(X[lab], y[lab].astype(np.float32)), float(y[lab].mean())
                order = np.argsort(-(X @ w))
                k = int(np.ceil(share * n))
                if arm == "own + labels kept":
                    labelled = set(lab.tolist())
                    keep_i = [j for j in lab if y[j]] + [j for j in order if j not in labelled][:max(0, k - int(y[lab].sum()))]
                else:
                    keep_i = list(order[:k])
                keep = {mp["map"][j] for j in keep_i}
                tok += sum(len(mp["text"][j]) + 8 for j in keep_i) // 4
                seen = {mp["map"][j] for j in lab}
                for x in qs:
                    if not x["ev"]:
                        continue
                    q += 1
                    hit += bool(keep & x["ev"])
                    if not (x["ev"] & seen):
                        q_u += 1
                        hit_u += bool(keep & x["ev"])
            res.setdefault(arm, []).append({"all": hit / q, "unseen": hit_u / max(q_u, 1), "n_unseen": q_u,
                                            "mean_tokens": tok // len(tests)})
            r = res[arm][-1]
            print(f"{arm:20s} seed {seed}: evidence kept {100 * r['all']:5.1f}%  on questions with unlabelled "
                  f"evidence ({q_u}) {100 * r['unseen']:5.1f}%  mean {r['mean_tokens']:,} tokens", flush=True)
    (HERE / "situation_own_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
