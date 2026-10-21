"""Many weak signals, weighed by small steps.

Each candidate message for a question (the union of the top 200 by words
and the top 200 by meaning) gets a few cheap signals, each meaningless
alone:
  word-rank score, meaning similarity and its rank score, the person's own
  words or not, length, holds a number, surprise when written (against the
  previous 2,000 messages), surprise within the question's topic (against
  earlier candidates), how atypical it is of the topic, where it sits in
  time.
The question contributes a few cues (asks about order, a summary, when,
how many, a change). Every signal is crossed with every cue, so one weight
can say "surprise matters for facts, not for orderings". A listwise
softmax over each question's candidates is trained by gradient steps
(Adam) on BEAM 500K only, then run once on 100K, 1M and 10M against the
measured arms (hybrid, own words first).

    python bench/beam_learn.py --data BEAM --train 500K --test 100K,1M,10M
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import build, cluster_ci, evidence, tokens  # noqa: E402
from beam_dense import MODEL, WINDOW, fuse  # noqa: E402
from gnomon.consolidate import directives, standing  # noqa: E402

CUES = {
    "order": re.compile(r"\b(order|sequence|first|before|after|earlier|later)\b", re.I),
    "summary": re.compile(r"\b(summar\w*|overview|evolv\w*|progress\w*|over time|throughout)\b", re.I),
    "when": re.compile(r"\b(when|date|day|week|month|how long|deadline)\b", re.I),
    "count": re.compile(r"\b(how many|how much|number of|total)\b", re.I),
    "change": re.compile(r"\b(chang\w*|updat\w*|now|current\w*|latest|switch\w*)\b", re.I),
}
SIGNALS = ("word", "sim", "dense_rank", "own", "short", "number", "surprise", "topic_surprise", "atypical",
           "position", "bias")
_NUM = re.compile(r"\d")


def novelty(emb: np.ndarray) -> np.ndarray:
    nov = np.ones(len(emb))
    for s0 in range(1, len(emb), 1024):
        blk = emb[s0:s0 + 1024]
        lo = max(0, s0 - WINDOW)
        sims = blk @ emb[lo:s0 + len(blk)].T
        for i in range(len(blk)):
            upto = s0 + i - lo
            if upto > 0:
                nov[s0 + i] = 1 - sims[i, max(0, upto - WINDOW):upto].max()
    return nov


def extract(root: Path, size: str, model) -> list:
    out = []
    for d in sorted((root / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        qf = d / "probing_questions" / "probing_questions.json"
        if not (d / "chat.json").exists() or not qf.exists():
            continue
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as t:
            m, ids, order = build(chat, Path(t))
            by_id = {x["id"]: x for x in order}
            mids = [x["id"] for x in order]
            where = {g: i for i, g in enumerate(mids)}
            emb = model.encode([x["content"][:4000] for x in order])
            emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
            nov = novelty(emb)
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
                    pool = sorted(set(words) | set(dense), key=lambda g: where[g])  # in time order
                    P = np.array([where[g] for g in pool])
                    wr = {g: 1 / (60 + i + 1) for i, g in enumerate(words)}
                    dr = {g: 1 / (60 + i + 1) for i, g in enumerate(dense)}
                    Ep = emb[P]
                    Sp = Ep @ Ep.T
                    tnov = np.ones(len(P))
                    for i in range(1, len(P)):
                        tnov[i] = 1 - Sp[i, :i].max()
                    cen = Ep.mean(0)
                    cen = cen / max(np.linalg.norm(cen), 1e-9)
                    feats = np.stack([
                        np.array([wr.get(g, 0) * 60 for g in pool]),
                        sims[P],
                        np.array([dr.get(g, 0) * 60 for g in pool]),
                        np.array([by_id[g]["role"] == "user" for g in pool], float),
                        -np.log1p(np.array([len(by_id[g]["content"]) for g in pool])) / 8,
                        np.array([bool(_NUM.search(by_id[g]["content"])) for g in pool], float),
                        nov[P],
                        tnov,
                        1 - Ep @ cen,
                        P / max(len(mids) - 1, 1),
                        np.ones(len(P)),
                    ], axis=1)
                    cues = np.array([1.0] + [bool(c.search(q["question"])) for c in CUES.values()])
                    X = (feats[:, :, None] * cues[None, None, :]).reshape(len(P), -1).astype(np.float32)
                    out.append({"conv": f"{size}/{d.name}", "category": cat, "X": X,
                                "y": np.array([g in ev for g in pool]), "pool": pool, "ev": ev,
                                "tokens": np.array([tokens(by_id[g]["content"]) for g in pool]),
                                "hybrid": fuse(words, dense),
                                "own": fuse(words, dense, sorted(fuse(words, dense)[:200],
                                                                 key=lambda g: by_id[g]["role"] != "user")),
                                "load": {x["source"] for x in standing(found, q["question"])}})
    return out


def train(data: list, steps: int = 400, lr: float = 0.03, l2: float = 1e-3, seed: int = 0) -> np.ndarray:
    """Listwise softmax: each question's candidates compete; the target puts
    equal mass on its evidence. Adam, full batch."""
    rng = np.random.default_rng(seed)
    dim = data[0]["X"].shape[1]
    w = rng.normal(0, 0.01, dim)
    m_, v_ = np.zeros(dim), np.zeros(dim)
    qs = [d for d in data if d["y"].any()]
    for t in range(1, steps + 1):
        g = l2 * w
        loss = 0.0
        for d in qs:
            s = d["X"] @ w
            s -= s.max()
            p = np.exp(s)
            p /= p.sum()
            tgt = d["y"] / d["y"].sum()
            loss -= float(tgt @ np.log(p + 1e-12))
            g += d["X"].T @ (p - tgt) / len(qs)
        m_ = 0.9 * m_ + 0.1 * g
        v_ = 0.999 * v_ + 0.001 * g * g
        w -= lr * (m_ / (1 - 0.9 ** t)) / (np.sqrt(v_ / (1 - 0.999 ** t)) + 1e-8)
        if t % 100 == 0:
            print(f"  step {t}: loss {loss / len(qs):.3f}", flush=True)
    return w


def evaluate(data: list, w: np.ndarray, k: int = 10) -> list:
    rows = []
    for d in data:
        learned = [d["pool"][i] for i in np.argsort(-(d["X"] @ w))]
        row = {"conv": d["conv"], "category": d["category"]}
        for name, got in (("hybrid", d["hybrid"]), ("own words first", d["own"]), ("learned", learned)):
            top = set(got[:k])
            row[name] = len(d["ev"] & (top | d["load"])) / len(d["ev"])
            pos = {g: i for i, g in enumerate(d["pool"])}
            row[name + " tokens"] = int(sum(d["tokens"][pos[g]] for g in got[:k] if g in pos))
        rows.append(row)
    return rows


def report(rows: list, label: str) -> dict:
    arms = ("hybrid", "own words first", "learned")
    groups = lambda key: {c: [r[key] for r in rows if r["conv"] == c] for c in {r["conv"] for r in rows}}  # noqa: E731
    out = {a: cluster_ci(groups(a)) for a in arms}
    print(f"\n== {label}: {len(rows)} questions, recall@10 with directives")
    print(f"{'':26s}" + "".join(f"{a:>24s}" for a in arms))
    print(f"{'all':26s}" + "".join(f"{out[a][0]:>6} [{out[a][1]}, {out[a][2]}]".rjust(24) for a in arms))
    for cat in sorted({r["category"] for r in rows}):
        rs = [r for r in rows if r["category"] == cat]
        vals = {a: round(100 * sum(r[a] for r in rs) / len(rs), 1) for a in arms}
        out[cat] = vals
        print(f"{cat:26s}" + "".join(f"{vals[a]:>24}" for a in arms))
    med = {a: int(np.median([r[a + " tokens"] for r in rows])) for a in arms}
    out["median_tokens"] = med
    print(f"{'median tokens':26s}" + "".join(f"{med[a]:>24}" for a in arms))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--train", default="500K")
    p.add_argument("--test", default="")
    p.add_argument("--cache", default="/tmp/beam_learn_cache")
    p.add_argument("--json")
    a = p.parse_args()
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(MODEL)
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)

    def get(size):
        f = cache / f"{size}.pkl"
        if f.exists():
            return pickle.loads(f.read_bytes())
        t0 = time.time()
        d = extract(Path(a.data), size, model)
        f.write_bytes(pickle.dumps(d))
        print(f"features for {size}: {len(d)} questions in {time.time() - t0:.0f} s", flush=True)
        return d

    tr = get(a.train)
    pool_reach = np.mean([d["y"].sum() / len(d["ev"]) for d in tr])
    print(f"evidence inside the candidate pool ({a.train}): {100 * pool_reach:.1f}%")
    w = train(tr)
    names = [f"{s}*{c}" for s in SIGNALS for c in ("all",) + tuple(CUES)]
    top = sorted(zip(names, w), key=lambda x: -abs(x[1]))[:15]
    print("largest weights:", ", ".join(f"{n} {v:+.2f}" for n, v in top))
    out = {"train": a.train, "weights": dict(zip(names, map(float, w))),
           a.train + " (training, in-sample)": report(evaluate(tr, w), a.train + " (in-sample)")}
    for size in [s for s in a.test.split(",") if s]:
        out[size] = report(evaluate(get(size), w), size + " (held out)")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
