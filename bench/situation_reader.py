"""Does the situation model change answers? ConvoMem implicit connections,
2 test histories, every question with evidence. Haiku answers each question
twice, from (A) Gnomon's top 10 messages, and (B) the same top 10 plus the
distilled situation model of that history (situation_distil.py, dev share).

Scoring:
- primary, judge-free: the answer USES the evidence when it contains at
  least two of the evidence's distinctive words (one or more is reported
  as a sensitivity check) (content words of the
  evidence messages that are not in the question, not stop words, and in
  fewer than 1% of the history's sentences);
- secondary, blinded: a separate Haiku judge sees the question, ConvoMem's
  rubric and the two answers as "1" and "2" in a seeded random order, and
  says which follows the rubric better, or "tie".

    python bench/situation_reader.py prepare --convomem FILE --dev DEV2 --test TEST --out DIR
    python bench/situation_reader.py pairs --out DIR       (after the readers)
    python bench/situation_reader.py score --out DIR       (after the judge)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that
these those i you your my me we our they them their he she his her what which who whom when where why how should
can could would will just also any some all each more most other such only own same very there here about into than
then so not no yes do does did done have has had get got going go make made want need really think know like feel
one two new time today week year next last good great""".split())
WORD = re.compile(r"[a-z][a-z'-]{2,}")
HISTORIES = (0, 1)


def prepare(a):
    import multi
    from model2vec import StaticModel
    from situation_distil import chosen, featurise, train
    model = StaticModel.from_pretrained(multi.MODEL)
    dev = json.loads((Path(a.dev) / "history_0.map.json").read_text())
    ydev = chosen(Path(a.dev), 0, len(dev["text"]))
    w = train(featurise(dev["text"], model), ydev.astype(np.float32))
    share = float(ydev.mean())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gold = []
    cases = list(multi.load_convomem([Path(a.convomem)], per_file=4, q_per_case=10 ** 6))
    for i, (conv, bs, qs) in enumerate(cases):
        if i not in HISTORIES:
            continue
        mp = json.loads((Path(a.test) / f"history_{i}.map.json").read_text())
        assert mp["conv"] == conv
        s = featurise(mp["text"], model) @ w
        keep = sorted(np.argsort(-s)[:int(round(share * len(mp["text"])))])
        (out / f"situation_{i}.txt").write_text("\n".join(mp["text"][j] for j in keep), encoding="utf-8")
        msgs = {m["id"]: m for b in bs for m in b["turns"][0]}
        df = Counter(w_ for t in mp["text"] for w_ in set(WORD.findall(t.lower())))
        feats = [d for d in multi.features(conv, bs, qs, model)]
        qmap = {q["question"]: q for q in qs}
        rows = []
        for n, d in enumerate(feats):
            q = next(q for q in qs if q["ev"] == d["ev"])
            view = [f"{msgs[g]['role']}: {msgs[g]['content']}" for g in d["hybrid"][:10]]
            qid = f"h{i}_q{n}"
            rows.append({"id": qid, "question": q["question"], "memory": view})
            evw = {w_ for e in d["ev"] for w_ in WORD.findall(msgs[e]["content"].lower())}
            qw = set(WORD.findall(q["question"].lower()))
            distinct = sorted(w_ for w_ in evw - qw - STOP if df[w_] < 0.01 * len(mp["text"]))
            gold.append({"id": qid, "question": q["question"], "rubric": q.get("rubric", ""),
                         "evidence": [msgs[e]["content"] for e in sorted(d["ev"])], "distinct": distinct})
        with open(out / f"questions_{i}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(conv, len(rows), "questions; situation model", len(keep), "sentences,",
              sum(len(mp["text"][j]) + 8 for j in keep) // 4, "tokens")
    (out.parent / f"{out.name}_gold.json").write_text(json.dumps(gold, indent=0), encoding="utf-8")


def load(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["id"]] = r["answer"]
    return out


def answers(out):
    A, B = {}, {}
    for i in HISTORIES:
        A.update(load(out / f"answers_A_{i}.jsonl"))
        B.update(load(out / f"answers_B_{i}.jsonl"))
    return A, B


def pairs(a):
    out = Path(a.out)
    gold = {g["id"]: g for g in json.loads((out.parent / f"{out.name}_gold.json").read_text())}
    A, B = answers(out)
    rng = random.Random(20261022)
    rows, key = [], {}
    for qid in sorted(set(A) & set(B)):
        flip = rng.random() < 0.5
        one, two = (B[qid], A[qid]) if flip else (A[qid], B[qid])
        key[qid] = "B" if flip else "A"  # which condition is answer "1"
        rows.append({"id": qid, "question": gold[qid]["question"], "rubric": gold[qid]["rubric"],
                     "answer_1": one, "answer_2": two})
    with open(out / "judge_pairs.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    (out.parent / f"{out.name}_judge_key.json").write_text(json.dumps(key), encoding="utf-8")
    print(len(rows), "pairs")


def score(a):
    out = Path(a.out)
    gold = {g["id"]: g for g in json.loads((out.parent / f"{out.name}_gold.json").read_text())}
    A, B = answers(out)
    ids = sorted(set(A) & set(B) & {g for g in gold if gold[g]["distinct"]})
    hits = lambda ans, g: sum(bool(re.search(rf"\b{re.escape(w)}", ans.lower())) for w in gold[g]["distinct"])  # noqa: E731
    ua1 = np.array([hits(A[g], g) >= 1 for g in ids], float)
    ub1 = np.array([hits(B[g], g) >= 1 for g in ids], float)
    ua = np.array([hits(A[g], g) >= 2 for g in ids], float)
    ub = np.array([hits(B[g], g) >= 2 for g in ids], float)
    print(f"{len(ids)} questions with distinctive evidence words (of {len(set(A) & set(B))} answered)")
    print(f"  answer uses the evidence (2+ distinctive words): top 10 alone {100 * ua.mean():.1f}%, "
          f"with situation model {100 * ub.mean():.1f}%   [1+ words: {100 * ua1.mean():.1f}% vs {100 * ub1.mean():.1f}%]")
    print(f"  paired: better with situation model on {int(((ub - ua) > 0).sum())}, worse on {int(((ub - ua) < 0).sum())}")
    res = {"uses_evidence": {"A": ua.mean(), "B": ub.mean(), "A_1plus": ua1.mean(), "B_1plus": ub1.mean(), "n": len(ids),
                             "B_better": int(((ub - ua) > 0).sum()), "B_worse": int(((ub - ua) < 0).sum())}}
    jf = out / "judge_verdicts.jsonl"
    if jf.exists():
        key = json.loads((out.parent / f"{out.name}_judge_key.json").read_text())
        v = load(jf)
        wins = Counter()
        pos = Counter()
        for qid, verdict in v.items():
            verdict = str(verdict).strip().lower()
            if verdict not in ("1", "2", "tie"):
                continue
            pos[verdict] += 1
            if verdict == "tie":
                wins["tie"] += 1
            else:
                first = key[qid]
                winner = first if verdict == "1" else ("A" if first == "B" else "B")
                wins[winner] += 1
        n = sum(wins.values())
        print(f"  blinded judge ({n} pairs): situation model better {wins['B']}, top 10 alone better {wins['A']}, tie {wins['tie']}"
              f"; position check: picked 1 {pos['1']}, picked 2 {pos['2']}")
        res["judge"] = {"B": wins["B"], "A": wins["A"], "tie": wins["tie"], "picked_1": pos["1"], "picked_2": pos["2"]}
    (out / "reader_results.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare", "pairs", "score"])
    p.add_argument("--convomem")
    p.add_argument("--dev")
    p.add_argument("--test")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    {"prepare": prepare, "pairs": pairs, "score": score}[a.cmd](a)
