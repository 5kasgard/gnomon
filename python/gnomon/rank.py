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
        self._start, self._time = {}, {}
        self._hits, self._scores, self.last_orders, self._turns = {}, {}, {}, {}
        self.calibration: Optional["Calibration"] = None

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
                self._time[f"{eid}#{n}"] = ep.get("record_time")
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

    def arms(self, query: str, as_of: Optional[str] = None, **recall_opts) -> dict:
        """Every mechanism's order for `query`, best first, as pointers:
        words, meaning, hybrid, own first and invariant (the default)."""
        r = self.memory.recall(self.reader, query, k=POOL, kinds=["turns"], as_of=as_of, **recall_opts)
        hits = [h for h in r["hits"] if "episode" in h]
        words = [f"{h['episode']}#{h['turn']}" for h in hits]
        self._hits = {f"{h['episode']}#{h['turn']}": h for h in hits}
        self.last_orders = {"words": words}
        if self.embed is None:
            return {"words": words}
        import numpy as np
        self._refresh(as_of)
        if not self._order:
            return {"words": words}
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
            scored.append((sum(self.weights[n] * v for n, v in f.items()), p))
        scored.sort(key=lambda s: -s[0])
        self._scores = {p: sc for sc, p in scored}
        rrf = lambda *orders: sorted({p for o in orders for p in o},  # noqa: E731
                                     key=lambda p: -sum(1 / (61 + o.index(p)) for o in orders if p in o))
        hybrid = rrf(words, dense)
        own_first = sorted(hybrid[:POOL], key=lambda p: not self.own(self._turns[p][0]))
        orders = {"words": words, "meaning": dense, "hybrid": hybrid, "own first": rrf(words, dense, own_first),
                  "invariant": [p for _, p in scored]}
        self.last_orders = orders
        return orders

    def _item(self, p: str, score: float) -> dict:
        if p in self._turns:
            speaker, text = self._turns[p]
        else:
            h = self._hits[p]
            speaker, text = h["speaker"], h["text"]
        start = self._start.get(p, self._hits[p]["start"] if p in self._hits else 0)
        when = self._time.get(p, self._hits[p].get("time") if p in self._hits else None)
        return {"pointer": p, "speaker": speaker, "text": text, "start": start, "time": when,
                "score": round(score, 4)}

    def rank(self, query: str, k: int = 10, as_of: Optional[str] = None, arm: Optional[str] = None,
             **recall_opts) -> list:
        """[{"pointer", "speaker", "text", "start", "time", "score"}], best first
        (start: the turn's code-point offset in its episode). `arm` picks the
        mechanism (default: the calibrated choice, else invariant)."""
        orders = self.arms(query, as_of=as_of, **recall_opts)
        if "invariant" not in orders:
            return [self._item(p, float(len(orders["words"]) - i)) for i, p in enumerate(orders["words"][:k])]
        choice = arm or (self.calibration.choice() if self.calibration else "invariant")
        got = orders.get(choice, orders["invariant"])
        return [self._item(p, self._scores.get(p, 0.0)) for p in got[:k]]


class Calibration:
    """Per-memory choice of mechanism, learnt from use. Each time a reader
    uses evidence (expands or cites pointers), `observe` records which
    mechanism had that evidence in its top 10. `choice` keeps the default
    unless another mechanism wins clearly: after at least `min_observations`,
    its mean paired advantage over the observations is more than `z`
    standard errors above zero. Simulated
    on four datasets this lifts LoCoMo from 51.8 to 69.1 recall@10 after 50
    observations and costs at most 0.1 where the default is already best
    (bench/compartments.md)."""

    ARMS = ("words", "meaning", "hybrid", "own first", "invariant")

    def __init__(self, default: str = "invariant", z: float = 2.0, record: Optional[Callable] = None,
                 min_observations: int = 10):
        self.default, self.z, self.record, self.min_obs = default, z, record, min_observations
        self.hits: list = []  # one dict per observation: arm -> 1.0 if it had the used evidence
        self.current = default

    def observe(self, orders: dict, used: Sequence[str], k: int = 10) -> Optional[str]:
        used = set(used)
        if not used:
            return None
        self.hits.append({a: float(bool(used & set(orders.get(a, [])[:k]))) for a in self.ARMS if a in orders})
        before, self.current = self.current, self._decide()
        if self.current != before and self.record:
            self.record(before, self.current, len(self.hits))
        return self.current

    def _decide(self) -> str:
        n = len(self.hits)
        if n < self.min_obs:  # also guards the zero-variance case of a few identical observations
            return self.default
        best, best_z = self.default, self.z
        for a in self.ARMS:
            if a == self.default:
                continue
            diffs = [h.get(a, 0.0) - h.get(self.default, 0.0) for h in self.hits]
            mean = sum(diffs) / n
            var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
            se = (var / n) ** 0.5
            zval = mean / se if se > 0 else (float("inf") if mean > 0 else 0.0)
            if zval > best_z:
                best, best_z = a, zval
        return best

    def choice(self) -> str:
        return self.current


def log_switch(memory, writer: str, compartment: str) -> Callable:
    """A Calibration `record` callback that writes each change of mechanism
    into the memory as a derived claim, so how the memory ranks is on the
    record and can be reviewed or superseded."""
    from . import claim

    def record(before: str, after: str, n: int) -> None:
        memory.remember(writer, claim(
            f"Recall for this memory now ranks by '{after}' instead of '{before}': "
            f"it held the evidence readers used clearly more often over {n} uses (paired, > 2 standard errors).",
            speaker=writer, frame="gnomon/ranking", compartment=compartment, stance="decided",
            truth="unknown", origin="derived"))
    return record
