"""A write-time situation model: what the user has said about their own
circumstances, selected once by a small model, loaded with every recall.

Step 1 (plain code): the user's sentences that are first person and not a
request, numbered, one file per history. Step 2 (a model, once per
history): return the numbers of the sentences that state something about
the user's own situation (commitments, plans, constraints, conditions,
goals, preferences, habits, abilities). Nothing is rewritten: each kept
fact is the user's exact sentence with a pointer to its message. Step 3:
for each question, the kept facts most similar in meaning are loaded next
to the recall results, their tokens counted.

    python bench/situation.py prepare --convomem FILE --out DIR --cases N [--seed S]
    python bench/situation.py score --convomem FILE --dir DIR --cases N
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SENT = re.compile(r"[^.!?\n]+[.!?]?")
REQUEST = re.compile(r"\?\s*$|^\s*(can|could|would|will|should|what|how|why|when|where|which|who|is|are|do|does|"
                     r"please|help|let's|tell|give|show|write|draft|create|make|explain|list|suggest)\b", re.I)
SELF = re.compile(r"\b(I|I'm|I've|I'll|I'd|my|we|we're|we've|our|me)\b", re.I)
PROMPT_V1 = """Below are sentences a user said to an AI assistant over many conversations, one per line, each
starting with its number in brackets. Choose every sentence that states something about the user's own
situation that could matter for advice given later: commitments and schedules, plans and upcoming events,
constraints (time, money, health, ability, rules they must follow), ongoing conditions, goals, preferences
and dislikes, habits, skills or the lack of them, and important facts about their team or life. Skip
requests, one-off task details, and statements about other things. Write only the chosen numbers,
comma-separated, to the output file, nothing else."""

# v2, the version tested: four readers per history, one quarter each, and a
# prompt that leans towards inclusion. On a development history it kept 82%
# of the evidence against 40% for one reader with PROMPT_V1.
PROMPT = """It holds sentences a user said to an AI assistant over many conversations, one per line, each
starting with its number in brackets. Choose every sentence that tells you something about the user's own
situation that could change good advice later: their commitments and schedules, plans and upcoming events,
constraints (time, money, budget, health, ability, rules, approvals they need), ongoing conditions and
problems (including tensions with people or teams), goals and priorities, preferences and dislikes, habits,
skills or the lack of them, and important facts about their team, role, company or life. Skip pure requests
and one-off task details. When unsure, include the sentence: missing one costs more than keeping an extra.
Expect to keep roughly one sentence in six."""
CHUNKS = 4


def candidates(bs) -> list:
    out = []
    for b in bs:
        for m in b["turns"][0]:
            if m["role"] != "user":
                continue
            for x in SENT.finditer(m["content"]):
                s = x.group(0).strip()
                if s and SELF.search(s) and not REQUEST.search(s):
                    out.append((m["id"], s))
    return out


def cases(path: Path, n: int, seed: int):
    import multi
    return list(multi.load_convomem([path], per_file=n, seed=seed, q_per_case=5))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare"])
    p.add_argument("--convomem", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cases", type=int, default=3)
    p.add_argument("--seed", type=int, default=20261021)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, (conv, bs, qs) in enumerate(cases(Path(a.convomem), a.cases, a.seed)):
        cand = candidates(bs)
        lines = [f"[{k}] {s}" for k, (_, s) in enumerate(cand)]
        (out / f"history_{i}.txt").write_text("\n".join(lines), encoding="utf-8")
        size = (len(lines) + CHUNKS - 1) // CHUNKS
        for c in range(CHUNKS):
            (out / f"history_{i}_chunk{c}.txt").write_text("\n".join(lines[c * size:(c + 1) * size]), encoding="utf-8")
        (out / f"history_{i}.map.json").write_text(json.dumps({"conv": conv, "map": [g for g, _ in cand],
                                                              "text": [s for _, s in cand]}), encoding="utf-8")
        print(conv, len(cand), "sentences", sum(len(s) + 8 for _, s in cand) // 4, "tokens")


if __name__ == "__main__":
    main()
