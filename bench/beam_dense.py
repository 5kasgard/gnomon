"""Does a meaning layer help where words fail? BEAM, judge-free recall.

Arms, all with the standing directives loaded (as `beam.py --consolidate`),
all scored by the same evidence recall@k:
- words: Gnomon's recall (BM25 and its boosts), as measured before;
- dense: cosine similarity of static embeddings (model2vec,
  minishlab/potion-retrieval-32M: a lookup table, no network at query
  time; about 45,000 messages a second on two CPUs);
- hybrid: reciprocal rank fusion of the two (1 / (60 + rank), summed);
- hybrid-binary: the same, the dense side searched by the signs of the
  embeddings only (1 bit per dimension, 32x smaller), its top 100
  rescored with the full vectors.

    python bench/beam_dense.py --data BEAM --sizes 500K --ks 10
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import build, cluster_ci, evidence, tokens  # noqa: E402
from gnomon.consolidate import directives, standing  # noqa: E402

MODEL = "minishlab/potion-retrieval-32M"
RRF = 60
WINDOW = 2000
CONTROLS = os.environ.get("BEAM_CONTROLS") == "1"


def ranks_to_scores(order: list) -> dict:
    return {g: 1.0 / (RRF + i + 1) for i, g in enumerate(order)}


def fuse(*orders: list) -> list:
    s = defaultdict(float)
    for o in orders:
        for g, v in ranks_to_scores(o).items():
            s[g] += v
    return sorted(s, key=lambda g: -s[g])


def run(root: Path, size: str, ks: list, model) -> list:
    rows = []
    for d in sorted((root / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        qf = d / "probing_questions" / "probing_questions.json"
        if not (d / "chat.json").exists() or not qf.exists():
            continue
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as t:
            m, ids, order = build(chat, Path(t))
            by_id = {x["id"]: x for x in order}
            mids = [x["id"] for x in order]
            emb = model.encode([x["content"][:4000] for x in order])
            emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
            bits = np.packbits(emb > 0, axis=1)
            # Surprise when written: 1 - the closest earlier message (within
            # the previous WINDOW), computed once at ingest, no query needed.
            nov = np.ones(len(mids))
            for s0 in range(1, len(mids), 1024):
                blk = emb[s0:s0 + 1024]
                lo = max(0, s0 - WINDOW)
                sims_prev = blk @ emb[lo:s0 + len(blk)].T
                for i in range(len(blk)):
                    upto = s0 + i - lo  # earlier messages only
                    if upto > 0:
                        nov[s0 + i] = 1 - sims_prev[i, max(0, upto - WINDOW):upto].max()
            novelty = dict(zip(mids, nov))
            found = directives(((x["id"], x["role"], x["content"]) for x in order), {"user"})
            for cat, qs in json.loads(qf.read_text(encoding="utf-8")).items():
                for q in qs:
                    ev, _ = evidence(q)
                    ev &= set(by_id)
                    if not ev:
                        continue
                    r = m.recall("owner", q["question"], k=200)
                    words = []
                    for h in r["hits"]:
                        g = ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1])))
                        if g is not None and g not in words:
                            words.append(g)
                    qv = model.encode([q["question"]])[0]
                    qv = qv / max(np.linalg.norm(qv), 1e-9)
                    sims = emb @ qv
                    dense = [mids[i] for i in np.argsort(-sims)[:200]]
                    qb = np.packbits(qv > 0)
                    ham = np.unpackbits(bits ^ qb, axis=1).sum(axis=1)
                    near = np.argsort(ham)[:100]
                    dense_b = [mids[i] for i in near[np.argsort(-sims[near])]]
                    hyb = fuse(words, dense)
                    salient = sorted(hyb[:200], key=lambda g: -novelty[g])
                    arms = {"words": words, "dense": dense, "hybrid": hyb,
                            "hybrid-binary": fuse(words, dense_b),
                            "hybrid+novelty": fuse(words, dense, salient)}
                    if CONTROLS:
                        rnd = list(hyb[:200]); random.Random(q["question"]).shuffle(rnd)
                        arms["ctl-short"] = fuse(words, dense, sorted(hyb[:200], key=lambda g: len(by_id[g]["content"])))
                        arms["ctl-user"] = fuse(words, dense, sorted(hyb[:200], key=lambda g: by_id[g]["role"] != "user"))
                        arms["ctl-random"] = fuse(words, dense, rnd)
                        arms["source+novelty"] = fuse(words, dense, sorted(
                            hyb[:200], key=lambda g: (by_id[g]["role"] != "user", -novelty[g])))
                    load = {x["source"] for x in standing(found, q["question"])}
                    row = {"conv": f"{size}/{d.name}", "category": cat}
                    for name, got in arms.items():
                        for k in ks:
                            top = set(got[:k]) | load
                            row[f"{name}@{k}"] = len(ev & top) / len(ev)
                            row[f"{name}_tokens@{k}"] = sum(tokens(by_id[g]["content"]) for g in got[:k])
                    rows.append(row)
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--sizes", default="500K")
    p.add_argument("--ks", default="10")
    p.add_argument("--json")
    a = p.parse_args()
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(MODEL)
    ks = [int(x) for x in a.ks.split(",")]
    arms = ("words", "dense", "hybrid", "hybrid-binary", "hybrid+novelty")
    if CONTROLS:
        arms = ("hybrid", "hybrid+novelty", "ctl-user", "ctl-random", "source+novelty")
    out = {}
    for size in a.sizes.split(","):
        t0 = time.time()
        rows = run(Path(a.data), size, ks, model)
        groups = lambda key, rs: {c: [r[key] for r in rs if r["conv"] == c] for c in {r["conv"] for r in rs}}  # noqa: E731
        summ = {}
        print(f"== {size}: {len(rows)} questions, {time.time() - t0:.0f} s")
        for k in ks:
            print(f"recall@{k}" + "".join(f"{n:>22}" for n in arms))
            ci = {n: cluster_ci(groups(f"{n}@{k}", rows)) for n in arms}
            summ[f"all@{k}"] = ci
            print(f"{'all':<26}" + "".join(f"{ci[n][0]:>6} [{ci[n][1]}, {ci[n][2]}]".rjust(22) for n in arms))
            for cat in sorted({r["category"] for r in rows}):
                rs = [r for r in rows if r["category"] == cat]
                vals = {n: round(100 * sum(r[f"{n}@{k}"] for r in rs) / len(rs), 1) for n in arms}
                summ[f"{cat}@{k}"] = vals
                print(f"{cat:<26}" + "".join(f"{vals[n]:>22}" for n in arms))
            med = {n: sorted(r[f"{n}_tokens@{k}"] for r in rows)[len(rows) // 2] for n in arms}
            summ[f"median_tokens@{k}"] = med
            print(f"{'median tokens (no directives)':<26}" + "".join(f"{med[n]:>22}" for n in arms))
        out[size] = summ
    if a.json:
        Path(a.json).write_text(json.dumps({"model": MODEL, **out}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
