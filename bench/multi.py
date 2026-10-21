"""The same weak signals across independent datasets.

Loaders turn BEAM, LoCoMo and LongMemEval-S into one shape: a record of
messages (id, role, content, time) and questions with the message ids
holding their evidence. Features are computed exactly as in
beam_learn.py, so a weighting learnt on one dataset can be scored on
another.

    python bench/multi.py --beam BEAM --locomo locomo10.json --lme longmemeval_s_cleaned.json
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import batches, build, evidence, tokens  # noqa: E402
from beam_dense import MODEL, fuse  # noqa: E402
from beam_learn import CUES, _NUM, novelty  # noqa: E402
from gnomon.consolidate import directives, standing  # noqa: E402


# ------------------------------------------------------------------ loaders
def load_beam(root: Path, size: str):
    for d in sorted((root / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        qf = d / "probing_questions" / "probing_questions.json"
        if not (d / "chat.json").exists() or not qf.exists():
            continue
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        qs = []
        for cat, ql in json.loads(qf.read_text(encoding="utf-8")).items():
            for q in ql:
                ev, _ = evidence(q)
                qs.append({"question": q["question"], "category": cat, "ev": ev})
        yield f"beam-{size}/{d.name}", batches(chat), qs


def _anchor(dt: str) -> str:
    """LongMemEval '2023/05/20 (Sat) 02:21' or LoCoMo '1:56 pm on 8 May, 2023' -> BEAM 'May-20-2023'."""
    for fmt, pat in (("%Y/%m/%d", r"(\d{4}/\d{2}/\d{2})"), ("%d %B, %Y", r"(\d{1,2} \w+, \d{4})")):
        m = re.search(pat, dt or "")
        if m:
            try:
                return datetime.strptime(m.group(1), fmt).strftime("%B-%d-%Y")
            except ValueError:
                pass
    return ""


def load_lme(path: Path, limit: int = 0):
    data = json.loads(path.read_text(encoding="utf-8"))
    for n, q in enumerate(data[:limit or None]):
        bs, ev, mid = [], set(), 0
        for sid, date, sess in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]):
            turn = []
            for t in sess:
                mid += 1
                msg = {"id": mid, "role": t["role"], "content": t["content"], "time_anchor": _anchor(date)}
                turn.append(msg)
                if t.get("has_answer"):
                    ev.add(mid)
            bs.append({"turns": [turn]})
        if not ev:  # no turn marked: the answer sessions' turns stand in
            ans = set(q["answer_session_ids"])
            k = 0
            for sid, b in zip(q["haystack_session_ids"], bs):
                for m in b["turns"][0]:
                    if sid in ans:
                        ev.add(m["id"])
        question = q["question"] + (f" (asked on {q['question_date']})" if q.get("question_date") else "")
        yield f"lme/{q['question_id']}", bs, [{"question": question, "category": q["question_type"], "ev": ev}]


def load_locomo(path: Path):
    cats = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
    for ci, conv in enumerate(json.loads(path.read_text(encoding="utf-8"))):
        c = conv["conversation"]
        a, bspk = c["speaker_a"], c["speaker_b"]
        ids, bs, mid = {}, [], 0
        k = 1
        while f"session_{k}" in c:
            turn = []
            for t in c[f"session_{k}"]:
                mid += 1
                ids[t["dia_id"]] = mid
                # Two people: neither is "the user". Speaker A is recorded as the
                # user so the signal exists; it is the same rule for every conversation.
                role = "user" if t["speaker"] == a else "assistant"
                turn.append({"id": mid, "role": role, "content": t["text"],
                             "time_anchor": _anchor(c.get(f"session_{k}_date_time", ""))})
            bs.append({"turns": [turn]})
            k += 1
        qs = []
        for q in conv["qa"]:
            if q.get("category") == 5:
                continue  # unanswerable: no evidence to find
            ev = set()
            for e in q.get("evidence", []):
                for part in re.findall(r"D\d+:\d+", str(e)):
                    if part in ids:
                        ev.add(ids[part])
            qs.append({"question": q["question"], "category": cats.get(q.get("category"), "other"), "ev": ev})
        yield f"locomo/{ci}", bs, qs


def load_convomem(paths, min_context: int = 150, per_file: int = 60, seed: int = 20261021):
    """ConvoMem pre-mixed test cases (Salesforce, CC BY-NC 4.0): the
    evidence conversations mixed among filler ones. Only histories of at
    least `min_context` conversations, `per_file` cases sampled per file
    with a fixed seed. Evidence = the messages whose text equals one of
    the case's evidence messages."""
    import random
    import ijson
    for path in paths:
        # Two passes, one case in memory at a time: count the qualifying
        # cases, draw `per_file` of their positions with the seed, then read
        # the file again and yield only those.
        with open(path, "rb") as f:
            sizes = [c for c in ijson.items(f, "item.contextSize")]
        qualifying = [i for i, c in enumerate(sizes) if c >= min_context]
        chosen = set(random.Random(seed).sample(qualifying, min(per_file, len(qualifying))))

        def cases_iter():
            with open(path, "rb") as f:
                for i, case in enumerate(ijson.items(f, "item")):
                    if i in chosen:
                        yield case
        cases = cases_iter()
        kind = Path(path).stem
        for n, case in enumerate(cases):
            bs, mid, by_text = [], 0, {}
            for conv in case["conversations"]:
                turn = []
                for msg in conv["messages"]:
                    mid += 1
                    role = "user" if msg["speaker"] == "user" else "assistant"
                    turn.append({"id": mid, "role": role, "content": msg["text"]})
                    by_text.setdefault(msg["text"].strip(), set()).add(mid)
                bs.append({"turns": [turn]})
            qs = []
            for item in case["evidenceItems"]:
                ev = set()
                for e in item.get("message_evidences", []):
                    ev |= by_text.get(e["text"].strip(), set())
                qs.append({"question": item["question"], "category": kind, "ev": ev})
            yield f"convomem/{kind}/{n}", bs, qs


# ----------------------------------------------------------------- features
def features(name, bs, qs, model, pool_n: int = 200) -> list:
    out = []
    with tempfile.TemporaryDirectory() as t:
        m, ids, order = build(bs, Path(t))
        by_id = {x["id"]: x for x in order}
        mids = [x["id"] for x in order]
        where = {g: i for i, g in enumerate(mids)}
        emb = model.encode([x["content"][:4000] for x in order])
        emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
        nov = novelty(emb)
        found = directives(((x["id"], x["role"], x["content"]) for x in order), {"user"})
        for q in qs:
            ev = q["ev"] & set(by_id)
            if not ev:
                continue
            r = m.recall("owner", q["question"], k=pool_n)
            words = []
            for h in r["hits"]:
                g = ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1])))
                if g is not None and g not in words:
                    words.append(g)
            qv = model.encode([q["question"]])[0]
            qv = qv / max(np.linalg.norm(qv), 1e-9)
            sims = emb @ qv
            dense = [mids[i] for i in np.argsort(-sims)[:pool_n]]
            pool = sorted(set(words) | set(dense), key=lambda g: where[g])
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
                np.array([wr.get(g, 0) * 60 for g in pool]), sims[P], np.array([dr.get(g, 0) * 60 for g in pool]),
                np.array([by_id[g]["role"] == "user" for g in pool], float),
                -np.log1p(np.array([len(by_id[g]["content"]) for g in pool])) / 8,
                np.array([bool(_NUM.search(by_id[g]["content"])) for g in pool], float),
                nov[P], tnov, 1 - Ep @ cen, P / max(len(mids) - 1, 1), np.ones(len(P))], axis=1)
            cues = np.array([1.0] + [bool(c.search(q["question"])) for c in CUES.values()])
            X = (feats[:, :, None] * cues[None, None, :]).reshape(len(P), -1).astype(np.float32)
            hyb = fuse(words, dense)
            out.append({"conv": name, "category": q["category"], "X": X, "y": np.array([g in ev for g in pool]),
                        "pool": pool, "ev": ev, "tokens": np.array([tokens(by_id[g]["content"]) for g in pool]),
                        "words": words, "hybrid": hyb,
                        "own": fuse(words, dense, sorted(hyb[:200], key=lambda g: by_id[g]["role"] != "user")),
                        "load": {x["source"] for x in standing(found, q["question"])}})
    return out


def dataset(name: str, loader, model, cache: Path) -> list:
    f = cache / f"multi_{name}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    t0, out = time.time(), []
    for conv, bs, qs in loader:
        out += features(conv, bs, qs, model)
    f.write_bytes(pickle.dumps(out))
    print(f"features for {name}: {len(out)} questions in {time.time() - t0:.0f} s", flush=True)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--beam")
    p.add_argument("--locomo")
    p.add_argument("--lme")
    p.add_argument("--convomem", help="folder with the ConvoMem pre-mixed files (one per evidence kind)")
    p.add_argument("--cache", default="/tmp/beam_learn_cache")
    a = p.parse_args()
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(MODEL)
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    if a.locomo:
        d = dataset("locomo", load_locomo(Path(a.locomo)), model, cache)
        print("locomo questions", len(d), "reach", round(100 * np.mean([x["y"].sum() / len(x["ev"]) for x in d]), 1))
    if a.convomem:
        files = sorted(Path(a.convomem).glob("*_evidence.json"))
        d = dataset("convomem", load_convomem(files), model, cache)
        print("convomem questions", len(d), "reach", round(100 * np.mean([x["y"].sum() / len(x["ev"]) for x in d]), 1))
    if a.lme:
        d = dataset("lme_s", load_lme(Path(a.lme)), model, cache)
        print("lme questions", len(d), "reach", round(100 * np.mean([x["y"].sum() / len(x["ev"]) for x in d]), 1))
