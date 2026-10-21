"""Distil the situation selector: train a cheap classifier (logistic
regression on static embeddings plus a few word flags) on Haiku's keep/skip
labels from one development history, then run it on the test histories
for free and compare with Haiku's own selections and with the evidence.

    python bench/situation_distil.py --dev DIR_dev2 --test DIR_test --convomem FILE
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FLAGS = [re.compile(p, re.I) for p in (
    r"\b(every|recurring|weekly|daily|for the next|until|this (week|month|quarter)|next (week|month))\b",
    r"\b(budget|deadline|frozen|approval|policy|rule|must|can't|cannot|won't)\b",
    r"\b(prefer|hate|love|dislike|enjoy|struggle|nervous|worried|bitter|frustrat\w*|stress\w*)\b",
    r"\b(my (team|manager|boss|director|wife|husband|partner|kids?|family|doctor)|I'm|I've been|I have)\b",
    r"\b(goal|plan|planning|trying to|training|learning|aiming)\b",
    r"\d")]


def featurise(texts, model):
    E = model.encode(list(texts)).astype(np.float32)
    E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    F = np.array([[bool(f.search(t)) for f in FLAGS] + [min(len(t), 400) / 400] for t in texts], np.float32)
    return np.hstack([E, F, np.ones((len(texts), 1), np.float32)])


def train(X, y, steps=500, lr=0.1, l2=1e-3):
    w = np.zeros(X.shape[1], np.float32)
    pos = y.mean()
    sw = np.where(y, 0.5 / pos, 0.5 / (1 - pos))  # balance the classes
    m_, v_ = np.zeros_like(w), np.zeros_like(w)
    for t in range(1, steps + 1):
        p = 1 / (1 + np.exp(-(X @ w)))
        g = X.T @ (sw * (p - y)) / len(y) + l2 * w
        m_ = 0.9 * m_ + 0.1 * g
        v_ = 0.999 * v_ + 0.001 * g * g
        w -= lr * (m_ / (1 - 0.9 ** t)) / (np.sqrt(v_ / (1 - 0.999 ** t)) + 1e-8)
    return w


def auc(s, lab):
    r = s.argsort().argsort() + 1
    lab = lab.astype(bool)
    return (r[lab].sum() - lab.sum() * (lab.sum() + 1) / 2) / (lab.sum() * (~lab).sum())


def chosen(folder: Path, i: int, n: int) -> np.ndarray:
    y = np.zeros(n, bool)
    for f in folder.glob(f"history_{i}_chunk*.sel"):
        for x in re.findall(r"\d+", f.read_text()):
            if int(x) < n:
                y[int(x)] = True
    if not y.any() and (folder / f"history_{i}.sel").exists():
        for x in re.findall(r"\d+", (folder / f"history_{i}.sel").read_text()):
            if int(x) < n:
                y[int(x)] = True
    return y


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dev", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--convomem", required=True)
    a = p.parse_args()
    from model2vec import StaticModel
    import multi
    model = StaticModel.from_pretrained(multi.MODEL)
    dev = json.loads((Path(a.dev) / "history_0.map.json").read_text())
    ydev = chosen(Path(a.dev), 0, len(dev["text"]))
    w = train(featurise(dev["text"], model), ydev.astype(np.float32))
    share = float(ydev.mean())  # how much Haiku kept on the dev history: the budget, fixed before any test
    print(f"trained on 1 dev history: {len(ydev)} sentences, Haiku kept {ydev.sum()}")
    test = Path(a.test)
    cases = list(multi.load_convomem([Path(a.convomem)], per_file=4, q_per_case=10 ** 6))
    tot = {"q": 0, "haiku": 0, "distil": 0, "distil_2x": 0, "random": 0}
    for i, (conv, bs, qs) in enumerate(cases):
        mp = json.loads((test / f"history_{i}.map.json").read_text())
        assert mp["conv"] == conv
        yh = chosen(test, i, len(mp["text"]))
        s = featurise(mp["text"], model) @ w
        k = int(round(share * len(mp["text"])))  # never Haiku's test count
        top = np.argsort(-s)
        keep_d = {mp["map"][j] for j in top[:k]}
        keep_d2 = {mp["map"][j] for j in top[:2 * k]}
        keep_h = {mp["map"][j] for j in np.flatnonzero(yh)}
        rng = np.random.default_rng(i)
        keep_r = {mp["map"][j] for j in rng.choice(len(mp["map"]), k, replace=False)}
        tok_d = sum(len(mp["text"][j]) + 8 for j in top[:k]) // 4
        print(f"history {i}: agreement with Haiku AUC {auc(s, yh):.3f}; Haiku kept {int(yh.sum())} "
              f"({sum(len(mp['text'][j]) + 8 for j in np.flatnonzero(yh)) // 4:,} tokens); "
              f"distilled keeps {k} ({tok_d:,} tokens)")
        for q in qs:
            if not q["ev"]:
                continue
            tot["q"] += 1
            for name, keep in (("haiku", keep_h), ("distil", keep_d), ("distil_2x", keep_d2), ("random", keep_r)):
                tot[name] += bool(keep & q["ev"])
    q = tot.pop("q")
    print(f"{q} test questions, evidence among the kept sentences:")
    for name, v in tot.items():
        print(f"  {name:10s} {100 * v / q:5.1f}%")


if __name__ == "__main__":
    main()
