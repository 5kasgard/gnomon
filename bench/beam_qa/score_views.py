"""Score the view comparison (make_views.py): the same scorer as score.py,
one row per view and budget, and paired differences per question.

    python bench/beam_qa/score_views.py DIR GOLD [OUT]
"""
import json
import random
import sys
from pathlib import Path

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("beam_qa_score", Path(__file__).resolve().parent / "score.py")
_S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_S)
f1, load, numbers, score = _S.f1, _S.load, _S.numbers, _S.score


def main():
    d, gold = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    conds = {}
    for f in sorted(d.glob("answers_*_*_*.jsonl")):
        _, view, budget, _ = f.stem.split("_")
        conds.setdefault((view, int(budget)), {}).update(load(f))
    out = {}
    for (view, budget), ans in sorted(conds.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        s = score(ans, gold)
        out[f"{view}@{budget}"] = {"answers": len(ans), **{k: v for k, v in s.items()}}
        print(f"\n{view} at {budget} tokens ({len(ans)} answers)")
        for k, ((m, lo, hi), n) in s.items():
            print(f"  {k:<26}{m:>6}  [{lo}, {hi}]  n={n}")
    answerable = [q for q in gold if q["category"] != "abstention"]
    for b in sorted({b for _, b in conds}):
        m, s = conds.get(("messages", b), {}), conds.get(("sentences", b), {})
        diffs = [f1(s[q["id"]], q["gold"]) - f1(m[q["id"]], q["gold"]) for q in answerable if q["id"] in m and q["id"] in s]
        nums = [(float(numbers(q["gold"]) <= numbers(s[q["id"]])), float(numbers(q["gold"]) <= numbers(m[q["id"]])))
                for q in answerable if q["id"] in m and q["id"] in s and numbers(q["gold"])]
        rng = random.Random(0)
        boots = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(2000))
        mean = sum(diffs) / len(diffs)
        print(f"\nat {b}: sentences - messages, F1 {100 * mean:+.1f} [{100 * boots[50]:+.1f}, {100 * boots[1949]:+.1f}] "
              f"over {len(diffs)} questions; better on {sum(x > 0 for x in diffs)}, worse on {sum(x < 0 for x in diffs)}; "
              f"numbers right {sum(a for a, _ in nums):.0f} vs {sum(b_ for _, b_ in nums):.0f} of {len(nums)}")
        out[f"paired@{b}"] = {"f1_diff": mean, "ci": [boots[50], boots[1949]], "n": len(diffs)}
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
