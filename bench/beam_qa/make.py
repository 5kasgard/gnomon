"""Build the reader files for end-to-end answers on BEAM 1M (not
redistributed here: BEAM's text stays in its own repository).

    python bench/beam_qa/make.py --data path/to/BEAM --out DIR

Picks 10 of the 35 1M conversations (seed 20261021). From each, every
question BEAM gives a short gold answer for (information extraction,
knowledge update, temporal and multi-session reasoning: 2 each) and both
abstention questions, 10 per conversation. Writes:

- gold.json: id, conversation, category, question, gold answer;
- memory_<conv>.jsonl: each question with Gnomon's top 10 messages;
- window_<conv>.txt: the last 128,000 tokens of the transcript (what a
  reader with that context sees of "the whole conversation"), for the first
  3 chosen conversations only (the expensive condition);
- window_<conv>_questions.jsonl: those conversations' questions.

128K, not 200K: the reader (Claude Haiku as a sub-agent) has to hold the
window and its instructions in its own context.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import beam as B  # noqa: E402

CATS = ("information_extraction", "knowledge_update", "temporal_reasoning", "multi_session_reasoning")
WINDOW = 128_000
SEED = 20261021


def line(x: dict) -> str:
    return f"[{x['id']}] {x['role']}: {x['content'].strip()}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--convs", type=int, default=10)
    p.add_argument("--window-convs", type=int, default=3)
    a = p.parse_args()
    root, out = Path(a.data) / "chats" / "1M", Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    convs = sorted((d.name for d in root.iterdir() if d.name.isdigit()), key=int)
    chosen = random.Random(SEED).sample(convs, a.convs)
    gold = []
    for n, c in enumerate(chosen):
        d = root / c
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        qs = json.loads((d / "probing_questions" / "probing_questions.json").read_text(encoding="utf-8"))
        picked = [(cat, j, q) for cat in CATS for j, q in enumerate(qs.get(cat, []))]
        picked += [("abstention", j, q) for j, q in enumerate(qs.get("abstention", []))]
        with tempfile.TemporaryDirectory() as t:
            m, ids, order = B.build(chat, Path(t))
            by_id = {x["id"]: x for x in order}
            with open(out / f"memory_{c}.jsonl", "w", encoding="utf-8") as f:
                for cat, j, q in picked:
                    qid = f"m{c}_{cat[:4]}{j}"
                    gold.append({"id": qid, "conv": c, "category": cat, "question": q["question"],
                                 "gold": q.get("answer") or q.get("ideal_response", "")})
                    r = m.recall("owner", q["question"], k=30)
                    got = []
                    for h in r["hits"]:
                        g = ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1])))
                        if g is not None and g not in got:
                            got.append(g)
                    f.write(json.dumps({"id": qid, "question": q["question"],
                                        "memory": [line(by_id[g]) for g in got[:10]]}) + "\n")
            if n < a.window_convs:
                kept, spent = [], 0
                for x in reversed(order):
                    spent += B.tokens(line(x) + "\n")
                    if spent > WINDOW:
                        break
                    kept.append(line(x))
                (out / f"window_{c}.txt").write_text("\n".join(reversed(kept)), encoding="utf-8")
                with open(out / f"window_{c}_questions.jsonl", "w", encoding="utf-8") as f:
                    for g in gold:
                        if g["conv"] == c:
                            f.write(json.dumps({"id": g["id"], "question": g["question"]}) + "\n")
    (out / "gold.json").write_text(json.dumps(gold, indent=0), encoding="utf-8")
    print(f"conversations {chosen}; window on {chosen[:a.window_convs]}; {len(gold)} questions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
