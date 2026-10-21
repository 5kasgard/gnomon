"""How few tokens can carry the facts an answer needs?

Two views of the same recall, compared at the same token budget:
- messages: Gnomon's top hits as whole messages (what bench/beam.py scores);
- sentences: from the top 50 hits, only the sentences that share the
  question's rarer words (each with the sentence after it, where answers
  often sit), best first, each tagged with its message id so the whole
  message can be fetched back exactly.

Judge-free score: BEAM's rubrics name the facts an answer must contain
("LLM response should mention: March 1, 2024"). A fact counts as present
when every number in it and at least two thirds of its other content words
appear in the view. Facts are scored in the categories that have them as
stated facts (information extraction, knowledge updates, temporal and
multi-session reasoning, contradictions). The same check run on the
evidence messages alone gives the check's own ceiling.

    python bench/beam_compress.py --data BEAM --sizes 500K --budgets 500,1000,2000,4000,8000
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from beam import build, cluster_ci, evidence, tokens  # noqa: E402

CATS = ("information_extraction", "knowledge_update", "temporal_reasoning", "multi_session_reasoning",
        "contradiction_resolution")
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this
that these those i you your my me we our they them their he she his her what which who whom when where why how
should mention response llm about into than then so not no yes do does did done can could would will just also
any some all each more most other such only own same very s t don now there here up out over under again""".split())
_SENT = re.compile(r"[^.!?\n]+[.!?]?")
_WORD = re.compile(r"[a-z0-9]+(?:[.,:/-][0-9]+)*")
_NUM = re.compile(r"\d+(?:[.,:/]\d+)*")


def words(s: str) -> list:
    return _WORD.findall(s.lower())


def facts(q: dict) -> list:
    out = []
    for r in q.get("rubric", []):
        m = re.search(r"mention[s]?:?\s*(.+)", r, re.I)
        if m:
            out.append(m.group(1).strip())
    return out


def present(fact: str, view_words: set, view_text: str, asked: set = frozenset()) -> bool:
    """Every number of the fact in the view, and two thirds of its other
    content words, not counting words the question itself used (a view
    chosen by the question's words would hold those whatever it held)."""
    nums = _NUM.findall(fact)
    if any(n not in view_text for n in nums):
        return False
    content = [w for w in words(fact) if w not in STOP and w not in asked and not _NUM.fullmatch(w) and len(w) > 2]
    if not content:
        return bool(nums)
    return sum(w in view_words for w in content) >= math.ceil(2 * len(content) / 3)


def score(fs: list, view: str, asked: set = frozenset()):
    if not fs:
        return None
    vw = set(words(view))
    return sum(present(f, vw, view, asked) for f in fs) / len(fs)


def random_view(sents: list, rng, budget: int) -> str:
    """Chance level: random sentences of the conversation, same budget."""
    parts, spent = [], 0
    for s in rng.sample(sents, min(len(sents), 400)):
        t = f"[m] {s}\n"
        if spent + tokens(t) > budget:
            continue
        parts.append(t)
        spent += tokens(t)
    return "".join(parts)


def message_view(got: list, by_id: dict, budget: int) -> str:
    parts, spent = [], 0
    for g in got:
        t = f"{by_id[g]['role']}: {by_id[g]['content']}\n"
        if spent + tokens(t) > budget:
            break
        parts.append(t)
        spent += tokens(t)
    return "".join(parts)


def sentence_view(question: str, got: list, by_id: dict, df: Counter, n_sent: int, budget: int) -> str:
    """Sentences of the top 50 hits ranked by the summed rarity (idf) of the
    question words they hold, damped by their message's rank; each kept with
    the sentence after it; tagged [m<id>]."""
    qw = {w for w in words(question) if w not in STOP and len(w) > 2}
    cand = []
    for rank, g in enumerate(got[:50]):
        sents = [m.group(0).strip() for m in _SENT.finditer(by_id[g]["content"]) if m.group(0).strip()]
        for i, s in enumerate(sents):
            sw = set(words(s))
            w = sum(math.log(n_sent / (1 + df[x])) for x in qw & sw)
            if w > 0:
                nxt = sents[i + 1] if i + 1 < len(sents) else ""
                cand.append((w / math.sqrt(rank + 1), g, i, s + (" " + nxt if nxt else "")))
    cand.sort(key=lambda c: -c[0])
    parts, spent, seen = [], 0, set()
    for _, g, i, text in cand:
        if (g, i) in seen:
            continue
        t = f"[m{g}] {text}\n"
        if spent + tokens(t) > budget:
            continue
        seen |= {(g, i), (g, i + 1)}
        parts.append(t)
        spent += tokens(t)
    return "".join(parts)


def run(root: Path, size: str, budgets: list) -> dict:
    rows = []
    for d in sorted((root / "chats" / size).iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        qf = d / "probing_questions" / "probing_questions.json"
        if not (d / "chat.json").exists() or not qf.exists():
            continue
        chat = json.loads((d / "chat.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as t:
            m, ids, order = build(chat, Path(t))
            by_id = {x["id"]: x for x in order}
            df, n_sent, all_sents = Counter(), 0, []
            for x in order:
                for s in _SENT.finditer(x["content"]):
                    df.update(set(words(s.group(0))))
                    n_sent += 1
                    if s.group(0).strip():
                        all_sents.append(s.group(0).strip())
            rng = random.Random(int(d.name) if d.name.isdigit() else 0)
            for cat, qs in json.loads(qf.read_text(encoding="utf-8")).items():
                if cat not in CATS:
                    continue
                for q in qs:
                    fs = facts(q)
                    if not fs:
                        continue
                    r = m.recall("owner", q["question"], k=100)
                    got = []
                    for h in r["hits"]:
                        g = ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1])))
                        if g is not None and g not in got:
                            got.append(g)
                    ev, _ = evidence(q)
                    ev_text = "".join(f"{by_id[i]['role']}: {by_id[i]['content']}\n" for i in sorted(ev) if i in by_id)
                    asked = set(words(q["question"]))
                    numeric = [f for f in fs if _NUM.search(f)]
                    row = {"conv": f"{size}/{d.name}", "category": cat, "facts": len(fs),
                           "ceiling": score(fs, ev_text, asked) if ev_text else None,
                           "ceiling_num": score(numeric, ev_text, asked) if ev_text else None}
                    for b in budgets:
                        views = {"messages": message_view(got, by_id, b),
                                 "sentences": sentence_view(q["question"], got, by_id, df, n_sent, b),
                                 "random": random_view(all_sents, rng, b)}
                        for name, v in views.items():
                            row[f"{name}@{b}"] = score(fs, v, asked)
                            row[f"{name}_num@{b}"] = score(numeric, v, asked)
                    rows.append(row)
    return {"rows": rows}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--sizes", default="500K")
    p.add_argument("--budgets", default="500,1000,2000,4000,8000")
    p.add_argument("--json")
    a = p.parse_args()
    budgets = [int(x) for x in a.budgets.split(",")]
    out = {}
    for size in a.sizes.split(","):
        rows = run(Path(a.data), size, budgets)["rows"]
        groups = lambda key: {c: [r[key] for r in rows if r["conv"] == c and r[key] is not None]  # noqa: E731
                              for c in {r["conv"] for r in rows}}
        summ = {"questions": len(rows), "facts": sum(r["facts"] for r in rows)}
        fmt = lambda c: f"{c[0]:5.1f} [{c[1]}, {c[2]}]" if c[0] is not None else "-"  # noqa: E731
        for suffix, label in (("", "all facts"), ("_num", "facts with a number")):
            summ["ceiling" + suffix] = cluster_ci(groups("ceiling" + suffix))
            print(f"== {size}: {len(rows)} questions, {summ['facts']} facts; {label}; present in the "
                  f"evidence messages themselves: {summ['ceiling' + suffix][0]}")
            print(f"{'tokens':>8} {'whole messages':>20} {'sentences':>20} {'random sentences':>20}")
            for b in budgets:
                for name in ("messages", "sentences", "random"):
                    summ[f"{name}{suffix}@{b}"] = cluster_ci(groups(f"{name}{suffix}@{b}"))
                print(f"{b:>8} " + " ".join(f"{fmt(summ[f'{n}{suffix}@{b}']):>20}"
                                             for n in ("messages", "sentences", "random")))
        out[size] = {"summary": summ, "rows": rows}
    if a.json:
        Path(a.json).write_text(json.dumps({s: v["summary"] for s, v in out.items()}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
