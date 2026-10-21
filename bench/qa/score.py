"""Score end-to-end answers on LoCoMo: Haiku answering from Gnomon's top 10
recalled turns versus from the whole conversation.

Judge-free metrics, as in the original LoCoMo paper: token F1 against the
gold answer (SQuAD normalisation), for categories 1-4; for category 5
(adversarial: the thing asked about never happened) the right answer is to
abstain, scored as the share answering "Not mentioned". A strict LLM judge
is deliberately not used (LoCoMo's usual judge accepted 63% of deliberately
wrong answers in an audit).
"""
import json, math, re, string, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def norm(s):
    s = str(s).lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    return s.split()


def f1(pred, gold):
    p, g = norm(pred), norm(gold)
    common = Counter(p) & Counter(g)
    k = sum(common.values())
    if not p or not g or k == 0:
        return 0.0
    pr, rc = k / len(p), k / len(g)
    return 2 * pr * rc / (pr + rc)


def mean_ci(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(n - 1, 1))
    return round(100 * m, 1), round(100 * 1.96 * sd / math.sqrt(n), 1)


def cluster_ci(groups: dict, reps: int = 2000) -> tuple:
    """Mean with a 95% interval from resampling whole conversations (the
    questions within one conversation are not independent)."""
    import random
    rng = random.Random(0)
    keys = sorted(groups)
    allx = [x for k in keys for x in groups[k]]
    means = []
    for _ in range(reps):
        pick = [rng.choice(keys) for _ in keys]
        xs = [x for k in pick for x in groups[k]]
        means.append(sum(xs) / len(xs))
    means.sort()
    return (round(100 * sum(allx) / len(allx), 1), round(100 * means[int(0.025 * reps)], 1),
            round(100 * means[int(0.975 * reps) - 1], 1))


def main():
    gold = {q["id"]: q for q in json.load(open(HERE / "gold.json"))}
    cats = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
    report = {}
    conds = [c for c in ("memory", "memory30", "full") if (HERE / f"answers_{c}.jsonl").exists()]
    for cond in conds:
        ans = {}
        for line in open(HERE / f"answers_{cond}.jsonl"):
            if line.strip():
                r = json.loads(line)
                ans[r["id"]] = r["answer"]
        missing = set(gold) - set(ans)
        by = defaultdict(list)
        abst, false_abst = [], []
        per_conv = defaultdict(list)
        for qid, q in gold.items():
            a = ans.get(qid, "")
            said_none = "not mentioned" in str(a).lower()
            if q["category"] == 5:
                abst.append(1.0 if said_none else 0.0)
            else:
                x = f1(a, q["gold"])
                by[cats[q["category"]]].append(x)
                per_conv[q["conv"]].append(x)
                false_abst.append(1.0 if said_none else 0.0)
        allf = [x for v in by.values() for x in v]
        report[cond] = {"missing": len(missing), "f1_all_1_4": mean_ci(allf),
                        "f1_all_1_4_conversation_bootstrap": cluster_ci(per_conv),
                        **{f"f1_{k}": mean_ci(v) for k, v in sorted(by.items())},
                        "abstained_on_adversarial": mean_ci(abst),
                        "abstained_on_answerable": mean_ci(false_abst)}
    # Paired difference on categories 1-4.
    ga = {c: {json.loads(l)["id"]: json.loads(l)["answer"] for l in open(HERE / f"answers_{c}.jsonl") if l.strip()}
          for c in conds}
    for c in conds:
        if c == "full":
            continue
        diffs = defaultdict(list)
        for i, q in gold.items():
            if q["category"] != 5:
                diffs[q["conv"]].append(f1(ga[c].get(i, ""), q["gold"]) - f1(ga["full"].get(i, ""), q["gold"]))
        report[f"{c}_minus_full_f1"] = {"per_question": mean_ci([x for v in diffs.values() for x in v]),
                                        "conversation_bootstrap": cluster_ci(diffs),
                                        "per_conversation": {k: round(100 * sum(v) / len(v), 1)
                                                             for k, v in sorted(diffs.items())}}
    full_tokens = {i: Path(HERE / f"conv{i}.txt").stat().st_size / 4 for i in range(5, 10)}
    report["context_tokens_per_question"] = {"full": round(sum(full_tokens.values()) / len(full_tokens))}
    for c, f in (("memory", "memory_questions.jsonl"), ("memory30", "memory30_questions.jsonl")):
        if (HERE / f).exists():
            report["context_tokens_per_question"][c] = round(sum(len(l) for l in open(HERE / f)) / 4 / len(gold))
    json.dump(report, open(HERE / "results.json", "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
