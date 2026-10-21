"""Changing the projection: a low-rank adjustment of the meaning space,
learnt from which messages answered which questions.

q' = q + (q U) V^T (identity plus rank r), trained with a contrastive loss:
each question's evidence messages against every other message of its
conversation (temperature 0.05), Adam, on BEAM 500K. Scored by dense
recall@10 and in the hybrid (words + meaning, rank fusion), against the
untouched space.

    python bench/beam_project.py --data BEAM --train 500K --test 100K,1M,10M
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import batches, cluster_ci, evidence  # noqa: E402
from beam_dense import MODEL  # noqa: E402

TAU, RANK = 0.05, 16


def embed(root: Path, size: str, model, cache: Path) -> list:
    f = cache / f"proj_{size}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    out = []
    for d in sorted((root / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        qf = d / "probing_questions" / "probing_questions.json"
        if not (d / "chat.json").exists() or not qf.exists():
            continue
        msgs = [x for b in batches(json.loads((d / "chat.json").read_text(encoding="utf-8")))
                for t in b["turns"] for x in t if x["content"].strip()]
        ids = [x["id"] for x in msgs]
        E = model.encode([x["content"][:4000] for x in msgs])
        E = (E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)).astype(np.float32)
        qs = []
        for cat, ql in json.loads(qf.read_text(encoding="utf-8")).items():
            for q in ql:
                ev, _ = evidence(q)
                lab = np.isin(ids, list(ev))
                if lab.any():
                    v = model.encode([q["question"]])[0]
                    qs.append({"cat": cat, "q": (v / max(np.linalg.norm(v), 1e-9)).astype(np.float32), "lab": lab})
        out.append({"conv": f"{size}/{d.name}", "E": E, "qs": qs})
    f.write_bytes(pickle.dumps(out))
    return out


def adapt(q, U, V):
    return q + (q @ U) @ V.T


def train(convs: list, steps=300, lr=0.01, l2=1e-2, seed=0):
    rng = np.random.default_rng(seed)
    dim = convs[0]["E"].shape[1]
    U = rng.normal(0, 0.01, (dim, RANK)).astype(np.float32)
    V = rng.normal(0, 0.01, (dim, RANK)).astype(np.float32)
    opt = {k: [np.zeros_like(x), np.zeros_like(x)] for k, x in (("U", U), ("V", V))}
    n = sum(len(c["qs"]) for c in convs)
    for t in range(1, steps + 1):
        gU, gV, loss = l2 * U, l2 * V, 0.0
        for c in convs:
            E = c["E"]
            for item in c["qs"]:
                q = item["q"]
                a = q @ U                      # (r,)
                qp = q + a @ V.T               # adapted question
                s = (E @ qp) / TAU
                s -= s.max()
                p = np.exp(s); p /= p.sum()
                tgt = item["lab"] / item["lab"].sum()
                loss -= float(tgt @ np.log(p + 1e-12))
                gq = (E.T @ (p - tgt)) / TAU / n          # d loss / d qp
                gV += np.outer(gq, a)
                gU += np.outer(q, gq @ V)
        for k, (x, g) in (("U", (U, gU)), ("V", (V, gV))):
            m_, v_ = opt[k]
            m_[:] = 0.9 * m_ + 0.1 * g
            v_[:] = 0.999 * v_ + 0.001 * g * g
            x -= lr * (m_ / (1 - 0.9 ** t)) / (np.sqrt(v_ / (1 - 0.999 ** t)) + 1e-8)
        if t % 100 == 0:
            print(f"  step {t}: loss {loss / n:.3f}", flush=True)
    return U, V


def score(convs, U=None, V=None, k=10):
    rows = []
    for c in convs:
        for item in c["qs"]:
            q = item["q"] if U is None else adapt(item["q"], U, V)
            top = np.argsort(-(c["E"] @ q))[:k]
            rows.append({"conv": c["conv"], "cat": item["cat"], "r": item["lab"][top].sum() / item["lab"].sum()})
    return rows


def show(label, base, new):
    g = lambda rows: {c: [r["r"] for r in rows if r["conv"] == c] for c in {r["conv"] for r in rows}}  # noqa: E731
    b, n = cluster_ci(g(base)), cluster_ci(g(new))
    print(f"{label}: dense recall@10 {b[0]} [{b[1]}, {b[2]}] -> adapted {n[0]} [{n[1]}, {n[2]}]")
    for cat in sorted({r["cat"] for r in base}):
        bb = np.mean([r["r"] for r in base if r["cat"] == cat]); nn = np.mean([r["r"] for r in new if r["cat"] == cat])
        print(f"   {cat:26s} {100 * bb:5.1f} -> {100 * nn:5.1f}")
    return {"base": b, "adapted": n}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--train", default="500K")
    p.add_argument("--test", default="")
    p.add_argument("--validate", action="store_true", help="train on 25 of the training conversations, score the other 10")
    p.add_argument("--json")
    a = p.parse_args()
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(MODEL)
    cache = Path("/tmp/beam_learn_cache"); cache.mkdir(exist_ok=True)
    tr = embed(Path(a.data), a.train, model, cache)
    out = {}
    if a.validate:
        U, V = train(tr[:25])
        out["validation"] = show("500K validation (10 conversations not trained on)", score(tr[25:]), score(tr[25:], U, V))
    else:
        U, V = train(tr)
        for size in [s for s in a.test.split(",") if s]:
            te = embed(Path(a.data), size, model, cache)
            out[size] = show(f"{size} (held out)", score(te), score(te, U, V))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
