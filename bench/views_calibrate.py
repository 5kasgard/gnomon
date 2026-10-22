"""Calibrate the view per memory: protocol in views.md ("calibrate the view").

    python bench/views_calibrate.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ARMS = ("A", "K", "K'", "S")
DEFAULT, NS, DRAWS, MIN_OBS, Z = "A", (10, 20, 50), 200, 10, 2.0


def choose(Y, obs):
    if len(obs) < MIN_OBS:
        return ARMS.index(DEFAULT)
    d0 = ARMS.index(DEFAULT)
    best, best_z = d0, Z
    for a in range(len(ARMS)):
        if a == d0:
            continue
        diff = Y[obs, a] - Y[obs, d0]
        se = diff.std(ddof=1) / np.sqrt(len(diff))
        z = diff.mean() / se if se > 0 else (np.inf if diff.mean() > 0 else 0.0)
        if z > best_z:
            best, best_z = a, z
    return best


def main():
    rows = json.loads((HERE / "views_forage_results.rows.json").read_text())
    rows += json.loads((HERE / "views_qasper_results.rows.json").read_text())
    out = {}
    print(f"{'environment':16s}{'default A':>10s}{'best':>12s}" + "".join(f"{f'n={n}':>9s}" for n in NS))
    for name in dict.fromkeys(r["dataset"] for r in rows):
        Y = np.array([[r[a] for a in ARMS] for r in rows if r["dataset"] == name]) * 100
        means = Y.mean(0)
        rng = random.Random(0)
        res = {}
        for n in NS:
            vals, switched = [], 0
            for _ in range(DRAWS):
                idx = list(range(len(Y)))
                rng.shuffle(idx)
                obs, rest = idx[:n], idx[n:]
                a = choose(Y, obs)
                switched += a != ARMS.index(DEFAULT)
                vals.append(Y[rest, a].mean())
            res[n] = {"score": float(np.mean(vals)), "switched": switched / DRAWS}
        best = int(np.argmax(means))
        out[name] = {"means": dict(zip(ARMS, map(float, means))), "best": ARMS[best], "calibrated": res}
        print(f"{name:16s}{means[0]:10.1f}{ARMS[best] + f' {means[best]:.1f}':>12s}"
              + "".join(f"{res[n]['score']:9.1f}" for n in NS)
              + "   switched " + ", ".join(f"{100 * res[n]['switched']:.0f}%" for n in NS))
    (HERE / "views_calibrate_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
