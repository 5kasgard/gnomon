"""Runs the plan in multi.md: transfer, variety, noise, then topology.

    python bench/multi_eval.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import cluster_ci  # noqa: E402
from beam_learn import CUES, SIGNALS  # noqa: E402

CACHE = Path("/tmp/beam_learn_cache")
NAMES = [f"{s}*{c}" for s in SIGNALS for c in ("all",) + tuple(CUES)]


def load(name):
    return pickle.loads((CACHE / name).read_bytes())


def train(envs: list, steps=400, lr=0.03, l2=1e-3, drop=0.0, seed=0):
    """Listwise softmax as in beam_learn.train; with several environments,
    each environment's mean loss counts equally; `drop` masks feature
    values during training (inverted dropout), redrawn every step."""
    rng = np.random.default_rng(seed)
    dim = envs[0][0]["X"].shape[1]
    w = rng.normal(0, 0.01, dim)
    m_, v_ = np.zeros(dim), np.zeros(dim)
    envs = [[d for d in e if d["y"].any()] for e in envs]
    for t in range(1, steps + 1):
        g = l2 * w
        for e in envs:
            ge = np.zeros(dim)
            for d in e:
                X = d["X"]
                if drop:
                    X = X * (rng.random(X.shape) >= drop) / (1 - drop)
                s = X @ w
                s -= s.max()
                p = np.exp(s)
                p /= p.sum()
                ge += X.T @ (p - d["y"] / d["y"].sum())
            g += ge / len(e) / len(envs)
        m_ = 0.9 * m_ + 0.1 * g
        v_ = 0.999 * v_ + 0.001 * g * g
        w -= lr * (m_ / (1 - 0.9 ** t)) / (np.sqrt(v_ / (1 - 0.999 ** t)) + 1e-8)
    return w


def recall_rows(data, arms: dict, k=10):
    rows = []
    for d in data:
        pos = {g: i for i, g in enumerate(d["pool"])}
        row = {"conv": d["conv"], "category": d["category"]}
        for name, how in arms.items():
            got = how(d)
            row[name] = len(d["ev"] & (set(got[:k]) | d["load"])) / len(d["ev"])
            row[name + "~tok"] = int(sum(d["tokens"][pos[g]] for g in got[:k] if g in pos))
        rows.append(row)
    return rows


def learned(w):
    return lambda d: [d["pool"][i] for i in np.argsort(-(d["X"] @ w))]


def table(rows, label, arms, by_question=False):
    key = (lambda r: r["conv"] + r["category"] + str(id(r))) if by_question else (lambda r: r["conv"])
    out = {}
    print(f"\n== {label}: {len(rows)} questions, recall@10 with directives")
    print(f"{'':28s}" + "".join(f"{a:>20s}" for a in arms))
    groups = lambda a: {c: [r[a] for r in rows if key(r) == c] for c in {key(r) for r in rows}}  # noqa: E731
    ci = {a: cluster_ci(groups(a)) for a in arms}
    out["all"] = ci
    print(f"{'all':28s}" + "".join(f"{ci[a][0]:>6} [{ci[a][1]},{ci[a][2]}]".rjust(20) for a in arms))
    for cat in sorted({r["category"] for r in rows}):
        rs = [r for r in rows if r["category"] == cat]
        vals = {a: round(100 * np.mean([r[a] for r in rs]), 1) for a in arms}
        out[cat] = vals
        print(f"{cat[:28]:28s}" + "".join(f"{vals[a]:>20}" for a in arms))
    med = {a: int(np.median([r[a + "~tok"] for r in rows])) for a in arms}
    out["median_tokens"] = med
    print(f"{'median tokens':28s}" + "".join(f"{med[a]:>20}" for a in arms))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json")
    a = p.parse_args()
    beam, locomo, lme = load("500K.pkl"), load("multi_locomo.pkl"), load("multi_lme_s.pkl")
    w_beam = np.array([json.loads((HERE / "beam_learn_results.json").read_text())["weights"][n] for n in NAMES])
    out = {}
    base = {"hybrid": lambda d: d["hybrid"], "own first": lambda d: d["own"]}

    # 1. transfer, unchanged weights
    arms = {**base, "BEAM-learned": learned(w_beam)}
    out["transfer_locomo"] = table(recall_rows(locomo, arms), "LoCoMo, BEAM weights unchanged", list(arms))

    # 3. noise level chosen by BEAM -> LoCoMo
    choice = {}
    for drop in (0.0, 0.1, 0.2, 0.4):
        w = train([beam], drop=drop)
        rs = recall_rows(locomo, {"x": learned(w)})
        choice[drop] = float(np.mean([r["x"] for r in rs]))
        print(f"noise {drop}: BEAM-trained, LoCoMo recall@10 {100 * choice[drop]:.1f}", flush=True)
    best = max(choice, key=choice.get)
    out["noise_choice"] = {"by_level": choice, "chosen": best}
    print(f"chosen noise level: {best}")

    # the test set, once: every model fixed above
    models = {
        "BEAM": w_beam,
        f"BEAM noise{best}": train([beam], drop=best) if best else w_beam,
        "BEAM+LoCoMo": train([beam, locomo]),
        f"BEAM+LoCoMo noise{best}": train([beam, locomo], drop=best),
    }
    arms = {**base, **{k: learned(v) for k, v in models.items()}}
    out["lme_s"] = table(recall_rows(lme, arms), "LongMemEval-S (test, never tuned on)", list(arms), by_question=True)

    # 4. topology: per-environment fits (descriptive)
    fits = {"BEAM": train([beam]), "LoCoMo": train([locomo]), "LongMemEval-S": train([lme])}
    print("\nweights fitted per environment (base signals, 'all' column):")
    print(f"{'signal':16s}" + "".join(f"{e:>15s}" for e in fits))
    topo = {}
    for s in SIGNALS:
        i = NAMES.index(f"{s}*all")
        vals = {e: float(w[i]) for e, w in fits.items()}
        topo[s] = vals
        print(f"{s:16s}" + "".join(f"{v:>+15.2f}" for v in vals.values()))
    cos = {f"{a}~{b}": float(fits[a] @ fits[b] / np.linalg.norm(fits[a]) / np.linalg.norm(fits[b]))
           for a in fits for b in fits if a < b}
    print("cosine between whole weight vectors:", {k: round(v, 2) for k, v in cos.items()})
    out["topology"] = {"base_weights": topo, "cosine": cos, "all_weights": {e: dict(zip(NAMES, map(float, w))) for e, w in fits.items()}}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
