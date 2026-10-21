"""Reader files for the view comparison on BEAM 1M: the same 10
conversations and 100 questions as make.py, each question given one view
of the memory at a fixed budget:

- messages: Gnomon's top messages in rank order, the last one cut to fill
  the budget;
- sentences: the sentences of the top 50 hits that share the question's
  rarer words, each tagged [m<id>] (bench/beam_compress.py).

    python bench/beam_qa/make_views.py --data BEAM --out DIR --budgets 1000,2000

Writes views_<view>_<budget>_<a|b>.jsonl (conversations split in two
halves, one reader each) and gold.json.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import beam as B  # noqa: E402
import beam_compress as C  # noqa: E402
from make import CATS, SEED  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--budgets", default="1000,2000")
    a = p.parse_args()
    budgets = [int(x) for x in a.budgets.split(",")]
    root, out = Path(a.data) / "chats" / "1M", Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    convs = sorted((d.name for d in root.iterdir() if d.name.isdigit()), key=int)
    chosen = random.Random(SEED).sample(convs, 10)
    gold, files = [], {}
    for n, c in enumerate(chosen):
        half = "a" if n < 5 else "b"
        d = root / c
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        qs = json.loads((d / "probing_questions" / "probing_questions.json").read_text(encoding="utf-8"))
        picked = [(cat, j, q) for cat in CATS for j, q in enumerate(qs.get(cat, []))]
        picked += [("abstention", j, q) for j, q in enumerate(qs.get("abstention", []))]
        with tempfile.TemporaryDirectory() as t:
            m, ids, order = B.build(chat, Path(t))
            by_id = {x["id"]: x for x in order}
            df, n_sent = Counter(), 0
            for x in order:
                for s in C._SENT.finditer(x["content"]):
                    df.update(set(C.words(s.group(0))))
                    n_sent += 1
            for cat, j, q in picked:
                qid = f"m{c}_{cat[:4]}{j}"
                gold.append({"id": qid, "conv": c, "category": cat, "question": q["question"],
                             "gold": q.get("answer") or q.get("ideal_response", "")})
                r = m.recall("owner", q["question"], k=100)
                got = []
                for h in r["hits"]:
                    g = ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1])))
                    if g is not None and g not in got:
                        got.append(g)
                for b in budgets:
                    views = {"messages": C.message_view(got, by_id, b),
                             "sentences": C.sentence_view(q["question"], got, by_id, df, n_sent, b)}
                    for name, v in views.items():
                        files.setdefault(f"views_{name}_{b}_{half}.jsonl", []).append(
                            {"id": qid, "question": q["question"], "memory": v})
    for name, rows in files.items():
        with open(out / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    (out / "gold.json").write_text(json.dumps(gold, indent=0), encoding="utf-8")
    print(f"conversations {chosen}; {len(gold)} questions; {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
