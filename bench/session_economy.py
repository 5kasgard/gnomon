"""Session economy: protocol in session_economy.md. Aggregates only.

    python bench/session_economy.py --wildchat SHARD.parquet --beam BEAM --locomo F --lme F --convomem DIR
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import tokens  # noqa: E402
from beam_dense import MODEL  # noqa: E402
from boundaries import CACHE  # noqa: E402
from push_scale import per_message  # noqa: E402
from situation_reader import STOP  # noqa: E402
from views import terms  # noqa: E402
from views_forage import kwic_aware, record_stats  # noqa: E402
from wild import wildchat  # noqa: E402

BUDGET, HISTORIES, FIRST, LAST, PAIRS, SEED = 2000, 100, 5, 25, 2000, 20261023


def unit(E):
    return E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)


def threshold(E, conv_of, idx, rng):
    """95th percentile of similarity between messages of different conversations, among idx."""
    if len(idx) < 2:
        return 1.0
    sims = []
    for _ in range(PAIRS * 3):
        i, j = rng.choice(idx), rng.choice(idx)
        if conv_of[i] != conv_of[j]:
            sims.append(float(E[i] @ E[j]))
        if len(sims) >= PAIRS:
            break
    return float(np.percentile(sims, 95)) if sims else 1.0


def wild(path, model):
    rng = random.Random(SEED)
    pushed = dup = prompts = skipped = 0
    per_hist = []
    for h, convs in enumerate(wildchat(path)):
        if h >= HISTORIES:
            break
        msgs = [(ci, r, t) for ci, c in enumerate(convs) for r, t in c if t.strip()]
        if len(convs) <= FIRST:
            continue
        E = unit(model.encode([t[:4000] for _, _, t in msgs]))
        conv_of = np.array([ci for ci, _, _ in msgs])
        is_user = np.array([r == "user" for _, r, _ in msgs])
        df = Counter(w for _, _, t in msgs for w in terms(t))
        idf = {w: math.log(len(msgs) / c) for w, c in df.items()}
        hp = hd = hq = hs = 0
        for k in range(FIRST, min(LAST, len(convs))):
            mem = np.flatnonzero(conv_of < k)
            thr = threshold(E, conv_of, list(np.flatnonzero((conv_of < k) & is_user)), rng)
            seen = set()
            for i in np.flatnonzero((conv_of == k) & is_user):
                sims = E[mem] @ E[i]
                hq += 1
                if sims.max() < thr:
                    hs += 1
                qw, used = terms(msgs[i][2]), 0
                for j in mem[np.argsort(-sims)[:200]]:
                    body = kwic_aware(msgs[j][2], qw, idf)
                    cost = tokens(body) + 6
                    if used + cost > BUDGET:
                        break
                    used += cost
                    hp += cost
                    if j in seen:
                        hd += cost
                    seen.add(j)
        if hq:
            per_hist.append({"dedupe": hd / max(hp, 1), "skip": hs / hq})
            pushed, dup, prompts, skipped = pushed + hp, dup + hd, prompts + hq, skipped + hs
    res = {"histories": len(per_hist), "prompts": prompts, "dedupe_saving": dup / max(pushed, 1),
           "gate_skip": skipped / max(prompts, 1),
           "median_history_dedupe": float(np.median([x["dedupe"] for x in per_hist])),
           "median_history_skip": float(np.median([x["skip"] for x in per_hist]))}
    print(f"WildChat: {res['histories']} histories, {prompts} prompts; dedupe saves {100 * res['dedupe_saving']:.1f}% "
          f"of pushed tokens (median history {100 * res['median_history_dedupe']:.1f}%); gate skips "
          f"{100 * res['gate_skip']:.1f}% of prompts (median history {100 * res['median_history_skip']:.1f}%)",
          flush=True)
    return res


def bench(name, loader, cache_file, model):
    rng = random.Random(SEED)
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    n = skip = 0
    words, lost = [], []
    for conv, bs, qs in loader:
        msgs = thr = None
        for q in qs:
            if msgs is None:
                order = [(bi, x) for bi, b in enumerate(bs) for t in b["turns"] for x in t if x["content"].strip()]
                msgs = {x["id"]: x["content"] for _, x in order}
                users = [(bi, x) for bi, x in order if x["role"] == "user"]
                E = unit(model.encode([x["content"][:4000] for _, x in users]))
                thr = threshold(E, np.array([bi for bi, _ in users]), list(range(len(users))), rng)
                idf, _, _ = record_stats(msgs)
            ev = set(q["ev"]) & set(msgs)
            if not ev:
                continue
            c = next(it)
            gold = {w for w in terms(q.get("answer") or q.get("rubric") or "") if w not in STOP} - terms(q["question"])
            if not gold:
                continue
            nc = c["X"].shape[1] // 11
            best = float(c["X"][:, nc].max())
            text, _ = per_message(q["question"], c["hybrid"], msgs, idf, BUDGET)
            w = len(gold & terms(text)) / len(gold)
            n += 1
            s = best < thr
            skip += s
            words.append(w)
            lost.append(w if s else 0.0)
    assert next(it, None) is None, name
    res = {"questions": n, "skipped": skip / n, "words": 100 * float(np.mean(words)),
           "words_lost": 100 * float(np.mean(lost))}
    print(f"{name}: {n} questions; gate skips {100 * res['skipped']:.1f}%, losing {res['words_lost']:.2f} of "
          f"{res['words']:.1f} answer words in view", flush=True)
    return res


def main():
    import multi
    from model2vec import StaticModel
    p = argparse.ArgumentParser()
    p.add_argument("--wildchat", required=True)
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--json", default="session_economy_results.json")
    a = p.parse_args()
    model = StaticModel.from_pretrained(MODEL)
    out = {"WildChat": wild(a.wildchat, model)}
    for size in ("100K", "1M"):
        out[f"BEAM {size}"] = bench(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", model)
    out["LoCoMo"] = bench("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", model)
    out["LongMemEval-S"] = bench("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", model)
    out["ConvoMem"] = bench("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))),
                            "multi_convomem.pkl", model)
    (HERE / a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
