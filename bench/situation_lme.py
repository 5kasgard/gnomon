"""A situation selector Gnomon can ship: trained on permissively licensed
data (LongMemEval, MIT), tested on ConvoMem (whose licence, CC BY-NC 4.0,
rules out shipping weights learnt from it). Protocol: situation_product.md.

    python bench/situation_lme.py prepare --lme longmemeval_s_cleaned.json --out DIR
    (Haiku labels DIR/lme_chunk*.txt with situation.PROMPT, writing lme_chunk*.sel)
    python bench/situation_lme.py evaluate --out DIR --convomem FILE --test DIR_test --dev DIR_dev2
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from situation import REQUEST, SELF, SENT  # noqa: E402

N, CHUNKS, SEED = 2000, 2, 20261022


def prepare(a):
    data = json.loads(Path(a.lme).read_text(encoding="utf-8"))
    seen, pool = set(), []
    for q in data:
        for sess in q["haystack_sessions"]:
            for t in sess:
                if t["role"] != "user":
                    continue
                for x in SENT.finditer(t["content"]):
                    s = x.group(0).strip()
                    if s and SELF.search(s) and not REQUEST.search(s) and s not in seen:
                        seen.add(s)
                        pool.append(s)
    sample = random.Random(SEED).sample(pool, N)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "lme_sample.json").write_text(json.dumps(sample), encoding="utf-8")
    size = (N + CHUNKS - 1) // CHUNKS
    for c in range(CHUNKS):
        lines = [f"[{k}] {sample[k]}" for k in range(c * size, min(N, (c + 1) * size))]
        (out / f"lme_chunk{c}.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(pool):,} candidate sentences in LongMemEval-S; sampled {N}, {sum(len(s) + 8 for s in sample) // 4:,} tokens")


def labels(out: Path) -> np.ndarray:
    import re
    y = np.zeros(N, bool)
    for f in out.glob("lme_chunk*.sel"):
        for x in re.findall(r"\d+", f.read_text()):
            if int(x) < N:
                y[int(x)] = True
    return y


def evaluate(a):
    import multi
    from model2vec import StaticModel
    from situation_distil import chosen, featurise, train
    out = Path(a.out)
    model = StaticModel.from_pretrained(multi.MODEL)
    y = labels(out)
    sample = json.loads((out / "lme_sample.json").read_text())
    w = train(featurise(sample, model), y.astype(np.float32))
    share = float(y.mean())
    dev = json.loads((Path(a.dev) / "history_0.map.json").read_text())
    ydev = chosen(Path(a.dev), 0, len(dev["text"]))
    w_cm, share_cm = train(featurise(dev["text"], model), ydev.astype(np.float32)), float(ydev.mean())
    print(f"LongMemEval labels: kept {int(y.sum())} of {N} ({100 * share:.1f}%); ConvoMem dev kept {100 * share_cm:.1f}%")
    cases = list(multi.load_convomem([Path(a.convomem)], per_file=4, q_per_case=10 ** 6))
    arms = {"LME-trained, LME share (primary)": (w, share), "LME-trained, ConvoMem share": (w, share_cm),
            "ConvoMem-trained (reference)": (w_cm, share_cm)}
    hit = {k: 0 for k in arms}
    tok = {k: 0 for k in arms}
    q = 0
    for i, (conv, bs, qs) in enumerate(cases):
        mp = json.loads((Path(a.test) / f"history_{i}.map.json").read_text())
        assert mp["conv"] == conv
        X = featurise(mp["text"], model)
        for name, (wt, sh) in arms.items():
            top = np.argsort(-(X @ wt))[:int(round(sh * len(mp["text"])))]
            keep = {mp["map"][j] for j in top}
            tok[name] += sum(len(mp["text"][j]) + 8 for j in top) // 4
            hit[name] += sum(bool(keep & x["ev"]) for x in qs if x["ev"])
        q += sum(1 for x in qs if x["ev"])
    res = {"questions": q, "lme_share": share}
    for name in arms:
        res[name] = {"evidence_kept": hit[name] / q, "mean_tokens": tok[name] // len(cases)}
        print(f"  {name:36s} evidence kept {100 * hit[name] / q:5.1f}%   mean {tok[name] // len(cases):,} tokens")
    (HERE / "situation_lme_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    np.save(out / "lme_weights.npy", w)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare", "evaluate"])
    p.add_argument("--lme")
    p.add_argument("--out", required=True)
    p.add_argument("--convomem")
    p.add_argument("--test")
    p.add_argument("--dev")
    a = p.parse_args()
    {"prepare": prepare, "evaluate": evaluate}[a.cmd](a)
