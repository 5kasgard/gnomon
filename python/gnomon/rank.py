"""Ranking by many weak signals, with the weights that held across datasets.

Recall by words (the Rust core) finds candidates; an optional meaning
layer (any `embed(texts) -> list of vectors`, e.g. model2vec's static
embeddings) adds candidates by meaning. Each candidate then gets eight
signals, weighted by `WEIGHTS`:

  word        how high recall ranked it (1/(60+rank), scaled)
  sim         cosine similarity to the question
  dense_rank  how high meaning ranked it
  own         said by the person, not derived by an assistant
  number      holds a number
  surprise    1 - its closest cosine to the previous 2,000 turns, when written
  topic_surprise  the same, among the question's candidates only
  atypical    1 - its cosine to the candidates' centre

These eight are the signals whose weights kept their sign when fitted
separately on BEAM, LoCoMo and LongMemEval; trained on those three with
feature dropout, the weighting scored 72.7 recall@10 on ConvoMem, which it
had never seen, against 69.9 for words and meaning fused alone
(bench/multi_invariant.md). Without an embedder, `rank` returns recall's
own order: Gnomon keeps working with no dependencies. With one, numpy is
used (an embedder needs it anyway).
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional, Sequence

WEIGHTS = {
    "word": 1.5986, "sim": 1.9724, "dense_rank": 0.9057, "own": 0.3590,
    "number": 0.1332, "surprise": 0.4484, "topic_surprise": 0.4050, "atypical": 0.1524,
}
WINDOW = 2000
POOL = 200
_NUM = re.compile(r"\d")
_ASSISTANTS = frozenset({"assistant", "claude", "model", "ai", "bot", "system"})


def static_embedder(name: str = "minishlab/potion-retrieval-32M") -> Optional[Callable]:
    """model2vec's static embeddings if installed, else None."""
    try:
        from model2vec import StaticModel
    except ImportError:
        return None
    model = StaticModel.from_pretrained(name)
    return lambda texts: model.encode(list(texts))


class Ranker:
    """Turns of one reader's memory, embedded once per state of the log
    (with their surprise when written), ranked per question."""

    def __init__(self, memory, reader: str, embed: Optional[Callable] = None,
                 weights: Optional[dict] = None, own: Optional[Callable[[str], bool]] = None):
        self.memory, self.reader, self.embed = memory, reader, embed
        self.weights = dict(WEIGHTS if weights is None else weights)
        self.own = own or (lambda speaker: speaker.lower() not in _ASSISTANTS)
        self._at = None
        self._start = {}

    def _refresh(self, as_of: Optional[str]) -> None:
        import numpy as np  # only with an embedder, which needs numpy anyway
        st = os.stat(self.memory.path)
        key = (st.st_size, st.st_mtime_ns, as_of)
        if self._at == key:
            return
        comps = self.memory.writers()[self.reader]["compartments"]
        state = self.memory.state(as_of=as_of, compartments=comps)["state"]
        turns = []
        for eid, ep in sorted(state["episodes"].items(), key=lambda kv: kv[1]["order"]):
            content = ep["body"]["content"]
            for n, t in enumerate(ep["body"]["turns"]):
                turns.append((f"{eid}#{n}", t["speaker"], content[t["start"]:t["end"]]))
                self._start[f"{eid}#{n}"] = t["start"]
        order = [t[0] for t in turns]
        old_order = getattr(self, "_order", [])
        # The log only grows: earlier turns keep their vectors and their
        # surprise, which depended only on what came before them.
        keep = len(old_order) if order[:len(old_order)] == old_order else 0
        E_old = self._E[:keep] if keep else np.zeros((0, 0), dtype=np.float32)
        new = turns[keep:]
        if new:
            V = np.asarray(self.embed([t[2][:4000] for t in new]), dtype=np.float32)
            V /= np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-9)
            E = V if not keep else np.vstack([E_old, V])
        else:
            E = E_old
        sur = np.ones(len(order), dtype=np.float32)
        if keep:
            sur[:keep] = self._sur[:keep]
        for s0 in range(max(keep, 1), len(order), 1024):
            blk = E[s0:s0 + 1024]
            lo = max(0, s0 - WINDOW)
            sims = blk @ E[lo:s0 + len(blk)].T
            for i in range(len(blk)):
                upto = s0 + i - lo
                if upto > 0:
                    sur[s0 + i] = 1.0 - sims[i, max(0, upto - WINDOW):upto].max()
        self._turns = {p: (sp, tx) for p, sp, tx in turns}
        self._index = {p: i for i, p in enumerate(order)}
        self._order, self._E, self._sur, self._at = order, E, sur, key

    def rank(self, query: str, k: int = 10, as_of: Optional[str] = None, **recall_opts) -> list:
        """[{"pointer", "speaker", "text", "start", "score"}], best first
        (start: the turn's code-point offset in its episode)."""
        r = self.memory.recall(self.reader, query, k=POOL, kinds=["turns"], as_of=as_of, **recall_opts)
        words = [f"{h['episode']}#{h['turn']}" for h in r["hits"] if "episode" in h]
        if self.embed is None:
            return [{"pointer": p, "speaker": h["speaker"], "text": h["text"], "start": h["start"],
                     "score": float(len(words) - i)}
                    for i, (p, h) in enumerate(zip(words, [h for h in r["hits"] if "episode" in h])) if i < k]
        import numpy as np
        self._refresh(as_of)
        if not self._order:
            return []
        qv = np.asarray(self.embed([query])[0], dtype=np.float32)
        qv /= max(float(np.linalg.norm(qv)), 1e-9)
        sims = self._E @ qv
        dense = [self._order[i] for i in np.argsort(-sims)[:POOL]]
        members = set(words) | set(dense)
        idx = np.array(sorted(self._index[p] for p in members if p in self._index))  # record order
        pool = [self._order[i] for i in idx]
        wr = {p: 60 / (60 + i + 1) for i, p in enumerate(words)}
        dr = {p: 60 / (60 + i + 1) for i, p in enumerate(dense)}
        P = self._E[idx]
        S = P @ P.T
        topic = np.ones(len(idx), dtype=np.float32)
        for i in range(1, len(idx)):
            topic[i] = 1.0 - S[i, :i].max()
        centre = P.mean(0)
        centre /= max(float(np.linalg.norm(centre)), 1e-9)
        atyp = 1.0 - P @ centre
        scored = []
        for j, p in enumerate(pool):
            speaker, text = self._turns[p]
            f = {"word": wr.get(p, 0.0), "sim": float(sims[idx[j]]), "dense_rank": dr.get(p, 0.0),
                 "own": float(self.own(speaker)), "number": float(bool(_NUM.search(text))),
                 "surprise": float(self._sur[idx[j]]), "topic_surprise": float(topic[j]),
                 "atypical": float(atyp[j])}
            scored.append((sum(self.weights[n] * v for n, v in f.items()), p, speaker, text))
        scored.sort(key=lambda s: -s[0])
        return [{"pointer": p, "speaker": sp, "text": tx, "start": self._start.get(p, 0), "score": round(s, 4)}
                for s, p, sp, tx in scored[:k]]
