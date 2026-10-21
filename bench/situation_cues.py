"""Score encoding-time cues: evidence coverage in the top k situation facts
ranked by question-to-cue similarity, against question-to-fact similarity
(and the two fused).

    python bench/situation_cues.py --dir SCRATCH
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    a = p.parse_args()
    import multi
    from model2vec import StaticModel
    m = StaticModel.from_pretrained(multi.MODEL)
    S = Path(a.dir)
    facts = [l.rstrip("\n") for l in open(S / "sitread/readers/situation_0.txt") if l.strip()]
    cues = [""] * len(facts)
    for f in sorted((S / "sit/cues").glob("h0_part*.cues")):
        for line in f.read_text().splitlines():
            mt = re.match(r"\[(\d+)\]\s*(?:requests:)?\s*(.*)", line.strip())
            if mt and int(mt.group(1)) < len(facts):
                cues[int(mt.group(1))] = mt.group(2)
    missing = sum(not c for c in cues)
    norm = lambda X: X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)  # noqa: E731
    F, C = norm(m.encode(facts)), norm(m.encode([c or f for c, f in zip(cues, facts)]))
    gold = [g for g in json.load(open(S / "sitread/readers_gold.json")) if g["id"].startswith("h0_")]
    ks = (5, 10, 20, 50)
    res = {n: {k: [] for k in ks} for n in ("fact", "cue", "fused")}
    for g in gold:
        ev = " ".join(g["evidence"])
        lab = np.array([bool(f) and f in ev for f in facts])
        if not lab.any():
            continue
        q = norm(m.encode([g["question"]]))[0]
        sf, sc = F @ q, C @ q
        rf, rc = np.argsort(-sf), np.argsort(-sc)
        pos_f = np.empty(len(facts)); pos_f[rf] = np.arange(len(facts))
        pos_c = np.empty(len(facts)); pos_c[rc] = np.arange(len(facts))
        fused = np.argsort(1 / (60 + pos_f) * -1 + 1 / (60 + pos_c) * -1)
        for n, order in (("fact", rf), ("cue", rc), ("fused", fused)):
            for k in ks:
                res[n][k].append(bool(lab[order[:k]].any()))
    n = len(res["fact"][5])
    print(f"{n} questions with the evidence in the situation model ({len(facts)} facts, {missing} without a cue)")
    print(f"{'ranked by':12s}" + "".join(f"{f'top {k}':>9s}" for k in ks))
    for name, r in res.items():
        print(f"{name:12s}" + "".join(f"{100 * np.mean(r[k]):>8.0f}%" for k in ks))
    (S / "sit/cues/coverage.json").write_text(json.dumps({k: {str(kk): float(np.mean(v)) for kk, v in r.items()}
                                                          for k, r in res.items()}))


if __name__ == "__main__":
    main()
