"""Where real use sits: protocol in wild.md. Aggregates only.

    python bench/wild.py --wildchat SHARD.parquet --beam BEAM --locomo F --lme F --convomem FILE [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import tokens  # noqa: E402
from beam_dense import MODEL  # noqa: E402


def measure(convs, model, rng):
    """convs: list of conversations in order, each [(role, text)] with role 'user' or 'other'."""
    user_len = [tokens(t) for c in convs for r, t in c if r == "user" and t.strip()]
    other_len = [tokens(t) for c in convs for r, t in c if r != "user" and t.strip()]
    msgs = [(ci, t) for ci, c in enumerate(convs) for r, t in c if r == "user" and t.strip()]
    if len(msgs) < 20 or len(convs) < 5:
        return None
    E = model.encode([t[:4000] for _, t in msgs])
    E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    conv_of = np.array([ci for ci, _ in msgs])
    S = E @ E.T

    def nearest(order):
        rank = {c: i for i, c in enumerate(order)}
        pos = np.array([rank[c] for c in conv_of])
        back, best = [], []
        for i in range(len(msgs)):
            earlier = pos < pos[i]
            if pos[i] < 2 or not earlier.any():
                continue
            j = np.flatnonzero(earlier)[np.argmax(S[i, earlier])]
            back.append(pos[i] - pos[j])
            best.append(S[i, j])
        return np.array(back), np.array(best)

    back, best = nearest(list(range(len(convs))))
    if not len(back):
        return None
    shuffled = []
    for _ in range(10):
        order = list(range(len(convs)))
        rng.shuffle(order)
        b, _ = nearest(order)
        shuffled.append(np.mean(b <= 2))
    pairs = [(i, j) for i, j in (rng.sample(range(len(msgs)), 2) for _ in range(2000)) if conv_of[i] != conv_of[j]]
    null95 = np.percentile([S[i, j] for i, j in pairs], 95) if pairs else 1.0
    return {"user_len": float(np.median(user_len)), "other_len": float(np.median(other_len)) if other_len else 0.0,
            "other_over_60": float(np.mean(np.array(other_len) > 60)) if other_len else 0.0,
            "within2": float(np.mean(back <= 2)), "within2_shuffled": float(np.mean(shuffled)),
            "return_cos": float(np.median(best)), "echo": float(np.mean(best > null95)),
            "conversations": len(convs), "messages": len(msgs)}


def wildchat(path):
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["hashed_ip", "timestamp", "language", "conversation"]).to_pylist()
    by = defaultdict(list)
    for r in t:
        if r["language"] == "English":
            by[r["hashed_ip"]].append(r)
    hist = [v for v in by.values() if 10 <= len(v) <= 200]
    hist.sort(key=len, reverse=True)
    for v in hist[:300]:
        v.sort(key=lambda r: r["timestamp"])
        yield [[("user" if m["role"] == "user" else "other", m["content"] or "") for m in r["conversation"]] for r in v]


def from_loader(loader, limit=None):
    for n, (conv, bs, qs) in enumerate(loader):
        if limit and n >= limit:
            break
        yield [[("user" if x["role"] == "user" else "other", x["content"]) for x in turn]
               for b in bs for turn in b["turns"]]


def main():
    import multi
    from model2vec import StaticModel
    p = argparse.ArgumentParser()
    p.add_argument("--wildchat", required=True)
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    model = StaticModel.from_pretrained(MODEL)
    rng = random.Random(20261022)
    sources = {
        "WildChat (real)": wildchat(a.wildchat),
        "BEAM 1M": from_loader(multi.load_beam(Path(a.beam), "1M")),
        "LoCoMo": from_loader(multi.load_locomo(Path(a.locomo))),
        "LongMemEval-S": from_loader(multi.load_lme(Path(a.lme)), 100),
        "ConvoMem": from_loader(multi.load_convomem([Path(a.convomem)], per_file=20)),
    }
    out = {}
    keys = ("user_len", "other_len", "other_over_60", "within2", "within2_shuffled", "return_cos", "echo",
            "conversations")
    print(f"{'':16s}{'n':>5s}{'user len':>9s}{'other len':>10s}{'>60':>6s}{'within 2':>9s}{'shuffled':>9s}"
          f"{'locality':>9s}{'return cos':>11s}{'echo':>6s}{'convs':>7s}")
    for name, src in sources.items():
        ms = [m for m in (measure(c, model, rng) for c in src) if m]
        med = {k: float(np.median([m[k] for m in ms])) for k in keys}
        med["locality"] = float(np.median([m["within2"] / max(m["within2_shuffled"], 1e-9) for m in ms]))
        med["n"] = len(ms)
        out[name] = med
        print(f"{name:16s}{len(ms):5d}{med['user_len']:9.0f}{med['other_len']:10.0f}{100 * med['other_over_60']:5.0f}%"
              f"{100 * med['within2']:8.0f}%{100 * med['within2_shuffled']:8.0f}%{med['locality']:9.2f}"
              f"{med['return_cos']:11.2f}{100 * med['echo']:5.0f}%{med['conversations']:7.0f}", flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
