"""Recall on BEAM ("Beyond a Million Tokens", ICLR 2026): the benchmark whose
conversations do not fit in a model's context, so memory has to beat
truncation rather than the whole transcript.

    python bench/beam.py --data path/to/BEAM [--sizes 100K,500K,1M,10M] [--ks 5,10,20]

BEAM keeps its conversations in git (github.com/mohammadtavakoli78/BEAM,
chats/<size>/<id>/); fetch only what this needs with

    git clone --depth 1 --filter=blob:none --sparse https://github.com/mohammadtavakoli78/BEAM
    cd BEAM && git sparse-checkout set --no-cone '/chats/*/*/chat.json' '/chats/*/*/probing_questions/*'

(~770 MB; do not run `git ls-tree -l` on the blob-filtered clone, it fetches
every blob). Results below were measured at BEAM commit b2da22e.

Judge-free. Every probing question except abstention (and four summaries)
names the messages that hold its evidence (`source_chat_ids`), so this
scores retrieval directly and needs no model and no judge:

- recall@k: share of a question's evidence messages in Gnomon's top k;
- any@k / all@k: at least one / every evidence message in the top k;
- updated@k (knowledge updates): the message with the new value in the top k.

Two baselines, both what a reader without memory would see:

- window W: the last W tokens of the conversation, which is what a model
  given "the full conversation" actually reads once it exceeds its context
  (128K and 200K tokens shown; tokens are estimated at four characters);
- recent, same budget: the most recent messages, cut at the same number of
  tokens as Gnomon's top k, so the two are compared at equal cost.

Intervals are 95% from resampling whole conversations. Nothing was tuned on
BEAM: Gnomon runs with its defaults, chosen on LoCoMo conversations 0-4.

Ingestion: each BEAM batch is one recorded conversation dated by its time
anchor; a batch longer than an episode's limit (100,000 characters) is
split into consecutive episodes of whole messages with the same date.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from gnomon import Memory  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402

CHUNK = 90_000  # characters per episode; the core's limit is 100,000
WINDOWS = (128_000, 200_000)


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


def anchor(s: str | None) -> str | None:
    try:
        return datetime.strptime(s or "", "%B-%d-%Y").strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        return None


def batches(chat: list) -> list:
    """BEAM's batches in order. The 10M conversations nest ten plans of ten
    batches each; message ids run on across plans."""
    if chat and "turns" not in chat[0]:
        return [b for plan in chat for (_, bs) in plan.items() for b in bs]
    return chat


def build(chat: list, workdir: Path) -> tuple[Memory, dict, list]:
    """A memory holding one conversation. Returns it, a map from (episode,
    turn index) to BEAM message id, and the messages in order."""
    m = Memory(workdir / "beam.jsonl")
    m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"], compartments=["chat"],
               record_time="2000-01-01T00:00:00Z")
    ids, order, last = {}, [], "2000-01-02T00:00:00Z"
    for b in batches(chat):
        msgs = [x for turn in b["turns"] for x in turn if x["content"].strip()]
        when = next((anchor(x.get("time_anchor")) for x in msgs if anchor(x.get("time_anchor"))), None)
        last = max(last, when or last)
        # A few messages are longer than an episode may be: they are cut into
        # consecutive pieces by the same speaker, each mapped to the message.
        pieces = [(x["id"], x["role"], part) for x in msgs for part in split(x["content"])]
        chunks, size = [[]], 0
        for pc in pieces:
            n = len(pc[2]) + 16
            if chunks[-1] and size + n > CHUNK:
                chunks.append([])
                size = 0
            chunks[-1].append(pc)
            size += n
        for ch in chunks:
            if not ch:
                continue
            ep = record_messages(m, "owner", [(role, text) for _, role, text in ch], compartment="chat",
                                 record_time=last)
            for i, (mid, _, _) in enumerate(ch):
                ids[(ep, i)] = mid
        order += msgs
    return m, ids, order


def split(text: str, limit: int = CHUNK - 1_000) -> list:
    """Cut text into pieces of at most `limit` characters, at line breaks
    where possible."""
    text = text.strip()
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    out.append(text)
    return [t for t in out if t]


def evidence(q: dict) -> tuple[set, set]:
    """All evidence ids, and the ids holding an updated value (knowledge
    updates only)."""
    def flat(v):
        if isinstance(v, dict):
            return {i for x in v.values() for i in flat(x)}
        if isinstance(v, list):
            return {i for x in v for i in flat(x)}
        return {v} if isinstance(v, int) else set()
    s = q.get("source_chat_ids")
    upd = flat(s.get("updated_info", [])) if isinstance(s, dict) else set()
    return flat(s), upd


def cluster_ci(groups: dict, reps: int = 2000) -> tuple:
    rng = random.Random(0)
    keys = sorted(groups)
    allx = [x for k in keys for x in groups[k]]
    if not allx:
        return (None, None, None)
    means = []
    for _ in range(reps):
        xs = [x for k in (rng.choice(keys) for _ in keys) for x in groups[k]]
        if xs:
            means.append(sum(xs) / len(xs))
    means.sort()
    return (round(100 * sum(allx) / len(allx), 1), round(100 * means[int(0.025 * len(means))], 1),
            round(100 * means[int(0.975 * len(means)) - 1], 1))


def evaluate(root: Path, size: str, ks: list) -> dict:
    """Per-question rows for every conversation of one size."""
    rows, timing, build_s, conv_tokens = [], [], [], []
    for d in sorted((root / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        qf = d / "probing_questions" / "probing_questions.json"
        if not (d / "chat.json").exists() or not qf.exists():
            continue
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as t:
            t0 = time.perf_counter()
            m, ids, order = build(chat, Path(t))
            build_s.append(time.perf_counter() - t0)
            # tokens from each message to the end of the conversation
            suffix, acc = {}, 0
            for x in reversed(order):
                acc += tokens(f"{x['role']}: {x['content']}\n")
                suffix[x["id"]] = acc
            conv_tokens.append(acc)
            by_id = {x["id"]: x for x in order}
            recent = [x["id"] for x in reversed(order)]
            for cat, qs in json.loads(qf.read_text(encoding="utf-8")).items():
                for q in qs:
                    ev, upd = evidence(q)
                    ev &= set(by_id)
                    upd &= ev
                    if not ev:
                        continue
                    t0 = time.perf_counter()
                    r = m.recall("owner", q["question"], k=min(200, 3 * max(ks)))
                    timing.append(time.perf_counter() - t0)
                    got = []
                    for h in r["hits"]:
                        g = ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1])))
                        if g is not None and g not in got:
                            got.append(g)
                    row = {"conv": f"{size}/{d.name}", "category": cat}
                    for k in ks:
                        top = set(got[:k])
                        budget = sum(tokens(f"{by_id[g]['role']}: {by_id[g]['content']}\n") for g in got[:k] if g in by_id)
                        same, spent = set(), 0
                        for i in recent:
                            spent += tokens(f"{by_id[i]['role']}: {by_id[i]['content']}\n")
                            if spent > budget:
                                break
                            same.add(i)
                        row[f"recall@{k}"] = len(ev & top) / len(ev)
                        row[f"any@{k}"] = float(bool(ev & top))
                        row[f"all@{k}"] = float(ev <= top)
                        row[f"recent_recall@{k}"] = len(ev & same) / len(ev)
                        row[f"tokens@{k}"] = budget
                        if upd:
                            row[f"updated@{k}"] = float(bool(upd & top))
                    for w in WINDOWS:
                        inside = {i for i in ev if suffix[i] <= w}
                        row[f"window_recall@{w // 1000}K"] = len(inside) / len(ev)
                        row[f"window_all@{w // 1000}K"] = float(inside == ev)
                    rows.append(row)
    return {"rows": rows, "latency": timing, "build_s": build_s, "conv_tokens": conv_tokens}


def summarise(rows: list, key: str) -> tuple:
    groups = defaultdict(list)
    for r in rows:
        if key in r:
            groups[r["conv"]].append(r[key])
    return cluster_ci(groups)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="path to a clone of github.com/mohammadtavakoli78/BEAM")
    p.add_argument("--sizes", default="100K,500K,1M,10M")
    p.add_argument("--ks", default="5,10,20")
    p.add_argument("--json", help="also write per-question rows and summaries to this file")
    a = p.parse_args()
    ks = [int(x) for x in a.ks.split(",")]
    out = {}
    for size in a.sizes.split(","):
        res = evaluate(Path(a.data), size, ks)
        rows = res["rows"]
        if not rows:
            print(f"{size}: no conversations found")
            continue
        convs = len({r["conv"] for r in rows})
        ct = sorted(res["conv_tokens"])
        lat = sorted(res["latency"])
        print(f"\n== BEAM {size}: {convs} conversations, {len(rows)} questions with evidence, "
              f"median conversation {ct[len(ct) // 2] / 1000:.0f}K tokens ==")
        print(f"ingest median {sorted(res['build_s'])[len(ct) // 2]:.1f} s per conversation; "
              f"recall median {1000 * lat[len(lat) // 2]:.1f} ms, p95 {1000 * lat[int(0.95 * len(lat))]:.1f} ms")
        summary = {}
        cols = ([f"recall@{k}" for k in ks] + [f"all@{k}" for k in ks]
                + [f"recent_recall@{k}" for k in ks]
                + [f"window_recall@{w // 1000}K" for w in WINDOWS] + [f"window_all@{w // 1000}K" for w in WINDOWS])
        for key in cols + [f"updated@{k}" for k in ks]:
            summary[key] = summarise(rows, key)
        for k in ks:
            tk = sorted(r[f"tokens@{k}"] for r in rows)
            summary[f"tokens@{k}"] = tk[len(tk) // 2]
        print(f"{'measure':<22}{'all questions':>22}")
        for key in cols + [f"updated@{k}" for k in ks]:
            m_, lo, hi = summary[key]
            if m_ is not None:
                print(f"{key:<22}{m_:>10}  [{lo}, {hi}]")
        print("median tokens in Gnomon's top k: " + ", ".join(f"k={k}: {summary[f'tokens@{k}']}" for k in ks))
        cats = sorted({r["category"] for r in rows})
        head = [f"recall@{k}" for k in ks] + [f"window_recall@{WINDOWS[-1] // 1000}K"]
        print(f"\n{'category':<26}{'n':>5}" + "".join(f"{h:>20}" for h in head))
        for c in cats:
            sub = [r for r in rows if r["category"] == c]
            vals = [summarise(sub, h)[0] for h in head]
            print(f"{c:<26}{len(sub):>5}" + "".join(f"{v:>20}" for v in vals))
        by_cat = {c: {"questions": sum(r["category"] == c for r in rows),
                      **{h: summarise([r for r in rows if r["category"] == c], h)[0]
                         for h in [f"recall@{k}" for k in ks] + [f"window_recall@{w // 1000}K" for w in WINDOWS]}}
                  for c in cats}
        out[size] = {"summary": summary, "conversations": convs, "questions": len(rows),
                     "median_conversation_tokens": ct[len(ct) // 2], "by_category": by_cat}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
