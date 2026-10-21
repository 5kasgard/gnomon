"""Build the end-to-end QA files from LoCoMo (not redistributed: CC BY-NC).

    python bench/qa/make.py [path/to/locomo10.json]

Writes gold.json, conv5.txt..conv9.txt (whole transcripts), and
memory_questions.jsonl / memory30_questions.jsonl (each question with
Gnomon's top 10 / 30 recalled turns): 40 random answerable and 10 random
adversarial questions per conversation 5-9, seed 20261021.
"""
import json
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "python"))
import locomo as L  # noqa: E402


def main():
    data = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/locomo/data/locomo10.json"))
    rng = random.Random(20261021)
    gold, mem = [], {10: [], 30: []}
    with tempfile.TemporaryDirectory() as d:
        for si in range(5, 10):
            s = data[si]
            m, reader, _ = L.build(s, Path(d))
            conv = s["conversation"]
            keys = sorted([k for k in conv if k.startswith("session_") and not k.endswith("date_time") and conv[k]],
                          key=lambda k: int(k.split("_")[1]))
            lines = []
            for k in keys:
                lines.append(f"\n=== {k.replace('_', ' ')} ({conv[k + '_date_time']}) ===")
                lines += [f"{t['speaker']}: {L.turn_text(t)}" for t in conv[k] if L.turn_text(t)]
            (HERE / f"conv{si}.txt").write_text("\n".join(lines), encoding="utf-8")
            main_q = [q for q in s["qa"] if q["category"] in (1, 2, 3, 4) and q.get("answer") is not None]
            adv = [q for q in s["qa"] if q["category"] == 5]
            for j, q in enumerate(rng.sample(main_q, 40) + rng.sample(adv, 10)):
                qid = f"c{si}q{j}"
                gold.append({"id": qid, "conv": si, "category": q["category"], "question": q["question"],
                             "gold": str(q.get("answer", q.get("adversarial_answer", "")))})
                for k in (10, 30):
                    r = m.recall(reader, q["question"], k=k)
                    mem[k].append({"id": qid, "question": q["question"],
                                   "memory": [f"[{h['time'][:10]}] {h['speaker']}: {h['text']}" for h in r["hits"]]})
    json.dump(gold, open(HERE / "gold.json", "w"), indent=0)
    for k, name in ((10, "memory_questions.jsonl"), (30, "memory30_questions.jsonl")):
        with open(HERE / name, "w") as f:
            for r in mem[k]:
                f.write(json.dumps(r) + "\n")
    with open(HERE / "full_questions.jsonl", "w") as f:
        for q in gold:
            f.write(json.dumps({"id": q["id"], "conv": q["conv"], "question": q["question"]}) + "\n")


if __name__ == "__main__":
    main()
