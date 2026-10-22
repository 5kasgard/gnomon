"""The situation model: what the person has said about their own situation,
kept exactly as said, with pointers to the record.

Some questions turn on something said long ago and never mentioned again
("my team just froze hiring", "I'm training for a marathon in March"). The
question shares no words with it, so recall cannot find it (ConvoMem's
"implicit connection" questions: the top 10 by any ranking held the
evidence 22% of the time). The situation model is built at write time
instead:

1. every sentence the person said that speaks of themselves and is not a
   request (a regular expression, free);
2. scored by a small logistic model on static embeddings plus a few word
   flags; the top share is kept (`SHARE`).

The kept sentences are the record's own characters, each with a pointer, in
the order they were said. They are not summarised: condensing them into a
profile lost the evidence for most questions and made answers worse than
no situation model at all (bench/situation_cues.md). The reader gets them
whole and picks the lines that bear on the question before answering
(`render`), which used the evidence most often in the reader tests.

The model depends only on the log, so it changes only when the person says
something new: a prompt cache holds it between questions.

Which sentences matter does not transfer between histories: weights learnt
from labels of LongMemEval kept 49% of ConvoMem's evidence, against 82%
for weights learnt from one ConvoMem history (bench/situation_product.md).
So no weights ship. Each memory learns its own, once: a model reads a
sample of about 1,000 of the person's candidate sentences and picks those
that could change advice later (`sample`, `LABEL_PROMPT`); `train` fits the
weights, saved beside the log (`<log>.situation.json`). With a model's
labels of a history's own sample that kept 63-72% of the evidence at
about a third of the size; keeping every candidate (the default) kept
96.5%. So learning is for memories too large to load whole.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Callable, Optional, Sequence

from .consolidate import sentences

SELF = re.compile(r"\b(I|I'm|I've|I'll|I'd|my|we|we're|we've|our|me)\b", re.I)
REQUEST = re.compile(r"\?\s*$|^\s*(can|could|would|will|should|what|how|why|when|where|which|who|is|are|do|does|"
                     r"please|help|let's|tell|give|show|write|draft|create|make|explain|list|suggest)\b", re.I)
FLAGS = [re.compile(p, re.I) for p in (
    r"\b(every|recurring|weekly|daily|for the next|until|this (week|month|quarter)|next (week|month))\b",
    r"\b(budget|deadline|frozen|approval|policy|rule|must|can't|cannot|won't)\b",
    r"\b(prefer|hate|love|dislike|enjoy|struggle|nervous|worried|bitter|frustrat\w*|stress\w*)\b",
    r"\b(my (team|manager|boss|director|wife|husband|partner|kids?|family|doctor)|I'm|I've been|I have)\b",
    r"\b(goal|plan|planning|trying to|training|learning|aiming)\b",
    r"\d")]
_ASSISTANTS = frozenset({"assistant", "claude", "model", "ai", "bot", "system"})
LABEL_PROMPT = (
    "Below are sentences the person said, one per line, each starting with its number in brackets. Choose every "
    "sentence that tells you something about their own situation that could change good advice later: their "
    "commitments and schedules, plans and upcoming events, constraints (time, money, budget, health, ability, "
    "rules, approvals they need), ongoing conditions and problems (including tensions with people or teams), "
    "goals and priorities, preferences and dislikes, habits, skills or the lack of them, and important facts "
    "about their team, role, company or life. Skip pure requests and one-off task details. When unsure, include "
    "the sentence: missing one costs more than keeping an extra. Expect to keep roughly one sentence in six.")
INSTRUCTION = ("Before answering, pick the lines below (if any) that should change your answer, "
               "then answer applying them. Later lines can replace earlier ones.")


def candidates(text: str) -> list:
    """(start, end, sentence) of each sentence that speaks of the speaker
    and is not a request."""
    return [(a, b, s) for a, b, s in sentences(text) if SELF.search(s) and not REQUEST.search(s)]


def features(texts: Sequence[str], embed: Callable):
    import numpy as np
    E = np.asarray(embed(list(texts)), dtype=np.float32)
    E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    F = np.array([[bool(f.search(t)) for f in FLAGS] + [min(len(t), 400) / 400] for t in texts], np.float32)
    return np.hstack([E, F, np.ones((len(texts), 1), np.float32)])


def train(texts: Sequence[str], labels: Sequence[bool], embed: Callable, steps: int = 500,
          lr: float = 0.1, l2: float = 1e-3) -> dict:
    """Weights from labelled sentences (True: keep). Class-balanced logistic
    regression, Adam; the share kept is the share labelled True."""
    import numpy as np
    X = features(texts, embed)
    y = np.asarray(labels, dtype=np.float32)
    if not 0 < y.mean() < 1:
        raise ValueError("labels must include both kept and skipped sentences")
    w = np.zeros(X.shape[1], np.float32)
    sw = np.where(y > 0, 0.5 / y.mean(), 0.5 / (1 - y.mean()))
    m_, v_ = np.zeros_like(w), np.zeros_like(w)
    for t in range(1, steps + 1):
        p = 1 / (1 + np.exp(-(X @ w)))
        g = X.T @ (sw * (p - y)) / len(y) + l2 * w
        m_ = 0.9 * m_ + 0.1 * g
        v_ = 0.999 * v_ + 0.001 * g * g
        w -= lr * (m_ / (1 - 0.9 ** t)) / (np.sqrt(v_ / (1 - 0.999 ** t)) + 1e-8)
    return {"w": [round(float(x), 6) for x in w], "share": float(y.mean()), "dim": int(X.shape[1])}


def weights_path(memory) -> Path:
    return Path(str(memory.path) + ".situation.json")


def load_weights(memory) -> Optional[dict]:
    try:
        return json.loads(weights_path(memory).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def save_weights(memory, weights: dict) -> Path:
    p = weights_path(memory)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(weights), encoding="utf-8")
    os.replace(tmp, p)
    return p


class Situation:
    """The situation model of one reader's memory. With an embedder and this
    memory's weights (`train`, saved beside the log) the top `share` of
    candidate sentences is kept; otherwise every candidate is kept. Scores
    are computed once per sentence: the log only grows."""

    def __init__(self, memory, reader: str, embed: Optional[Callable] = None, weights: Optional[dict] = None,
                 own: Optional[Callable[[str], bool]] = None):
        self.memory, self.reader, self.embed = memory, reader, embed
        self.weights = weights if weights is not None else (load_weights(memory) if embed is not None else None)
        self.own = own or (lambda speaker: speaker.lower() not in _ASSISTANTS)
        self._at = None
        self._scored: dict = {}  # (pointer, start) -> score
        self.lines: list = []

    def _refresh(self, as_of: Optional[str]) -> None:
        st = os.stat(self.memory.path)
        key = (st.st_size, st.st_mtime_ns, as_of)
        if self._at == key:
            return
        comps = self.memory.writers()[self.reader]["compartments"]
        state = self.memory.state(as_of=as_of, compartments=comps)["state"]
        cand = []
        for eid, ep in sorted(state["episodes"].items(), key=lambda kv: kv[1]["order"]):
            content = ep["body"]["content"]
            for n, t in enumerate(ep["body"]["turns"]):
                if not self.own(t["speaker"]):
                    continue
                for a, b, s in candidates(content[t["start"]:t["end"]]):
                    cand.append({"pointer": f"{eid}#{n}", "start": t["start"] + a, "end": t["start"] + b,
                                 "speaker": t["speaker"], "time": ep.get("record_time"),
                                 "text": s})
        if self.embed is not None and self.weights is not None:
            import numpy as np
            new = [c for c in cand if (c["pointer"], c["start"]) not in self._scored]
            if new:
                w = np.asarray(self.weights["w"], dtype=np.float32)
                X = features([c["text"] for c in new], self.embed)
                if X.shape[1] != len(w):
                    raise ValueError(f"weights expect {len(w)} features, the embedder gives {X.shape[1]}")
                for c, s in zip(new, X @ w):
                    self._scored[(c["pointer"], c["start"])] = float(s)
            k = math.ceil(self.weights["share"] * len(cand))
            ranked = sorted(cand, key=lambda c: -self._scored[(c["pointer"], c["start"])])[:k]
            keep = {(c["pointer"], c["start"]) for c in ranked}
            cand = [c for c in cand if (c["pointer"], c["start"]) in keep]  # record order
        self.lines, self._at = cand, key

    def candidates(self, as_of: Optional[str] = None) -> list:
        """Every candidate sentence, whatever the weights keep."""
        weights, self.weights, self._at = self.weights, None, None
        try:
            self._refresh(as_of)
            return list(self.lines)
        finally:
            self.weights, self._at = weights, None

    def sample(self, n: int = 1000, seed: int = 0) -> list:
        """Up to `n` candidate sentences drawn with `seed`, in record order:
        what a model labels once to train this memory's weights."""
        import random
        cand = self.candidates()
        idx = sorted(random.Random(seed).sample(range(len(cand)), min(n, len(cand))))
        return [cand[i] for i in idx]

    def learn(self, texts: Sequence[str], keep: Sequence[bool]) -> dict:
        """Fit this memory's weights to labelled sentences and save them."""
        if self.embed is None:
            raise ValueError("learning the selector needs an embedder (the MCP server's --meaning)")
        self.weights = train(texts, keep, self.embed)
        save_weights(self.memory, self.weights)
        self._at = None
        return self.weights

    def model(self, as_of: Optional[str] = None) -> list:
        """[{"pointer", "start", "end", "speaker", "time", "text"}], oldest first.
        A sentence said again word for word is kept once, where it was last said."""
        self._refresh(as_of)
        last = {}
        for i, c in enumerate(self.lines):
            last[" ".join(c["text"].lower().split())] = i
        keep = set(last.values())
        return [c for i, c in enumerate(self.lines) if i in keep]

    def render(self, as_of: Optional[str] = None, budget: Optional[int] = None) -> dict:
        """{"text", "lines", "tokens"}. With `budget` (tokens), the most
        recent lines that fit are kept."""
        lines = self.model(as_of)
        if budget is not None:
            out, used = [], 0
            for c in reversed(lines):
                cost = (len(c["text"]) + len(c["pointer"]) + 6) // 4
                if used + cost > budget:
                    break
                out.append(c)
                used += cost
            lines = out[::-1]
        body = [f"[{c['pointer']}] {c['text']}" for c in lines]
        text = "\n".join(["What the person has said about their own situation, in their words, oldest first.",
                          INSTRUCTION, ""] + body)
        return {"text": text, "lines": lines, "tokens": len(text) // 4}
