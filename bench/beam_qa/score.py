"""Score end-to-end answers on BEAM 1M: Claude Haiku reading Gnomon's top
10 messages versus the last 128,000 tokens of the conversation.

    python bench/beam_qa/score.py DIR GOLD

Judge-free. For questions with a gold answer: token F1 and token recall of
the gold answer (SQuAD normalisation, as bench/qa), and, where the gold
answer holds numbers, whether every one of them appears in the answer
("numbers right": BEAM's answers are mostly versions, counts, dates and
percentages, so this is the sharpest exact check). For abstention
questions (the thing asked about is not in the conversation): the share
answering "Not mentioned". Also the share of answerable questions wrongly
answered "Not mentioned". Intervals: 95%, resampling whole conversations.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "qa"))
from score import cluster_ci, f1, norm  # noqa: E402


def recall(pred, gold):
    p, g = set(norm(pred)), norm(gold)
    return sum(t in p for t in g) / len(g) if g else 0.0


def numbers(s):
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,.]*", str(s))}


def abstained(a):
    return "not mentioned" in str(a).lower()


def score(answers: dict, gold: list) -> dict:
    g = defaultdict(lambda: defaultdict(list))
    for q in gold:
        if q["id"] not in answers:
            continue
        a = answers[q["id"]]
        c = q["conv"]
        if q["category"] == "abstention":
            g["abstains when absent"][c].append(float(abstained(a)))
            continue
        g["F1"][c].append(f1(a, q["gold"]))
        g["gold recall"][c].append(recall(a, q["gold"]))
        g["wrongly 'not mentioned'"][c].append(float(abstained(a)))
        if numbers(q["gold"]):
            g["numbers right"][c].append(float(numbers(q["gold"]) <= numbers(a)))
    return {k: (cluster_ci(v), sum(len(x) for x in v.values())) for k, v in g.items()}


def load(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            r = json.loads(line)
            out[r["id"]] = r["answer"]
    return out


def main():
    d, gold = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    conds = {}
    for name in ("memory", "window"):
        ans = {}
        for f in sorted(d.glob(f"answers_{name}_*.jsonl")):
            ans.update(load(f))
        conds[name] = ans
    window_convs = {q["conv"] for q in gold if q["id"] in conds["window"]}
    rows = [("Gnomon top 10, all conversations", conds["memory"], gold),
            ("Gnomon top 10, window conversations", conds["memory"], [q for q in gold if q["conv"] in window_convs]),
            ("Last 128K tokens, window conversations", conds["window"], gold)]
    out = {}
    for label, ans, gl in rows:
        s = score(ans, gl)
        out[label] = s
        print(f"\n{label}")
        for k, ((m, lo, hi), n) in s.items():
            print(f"  {k:<26}{m:>6}  [{lo}, {hi}]  n={n}")
    by_cat = defaultdict(list)
    for q in gold:
        if q["category"] != "abstention" and q["id"] in conds["memory"]:
            by_cat[q["category"]].append(f1(conds["memory"][q["id"]], q["gold"]))
    print("\nGnomon top 10, F1 by category")
    for c, xs in sorted(by_cat.items()):
        print(f"  {c:<26}{100 * sum(xs) / len(xs):>6.1f}  n={len(xs)}")
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
