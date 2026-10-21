"""What a reply should see, in three tiers.

- Hot: standing instructions and preferences, loaded with every reply as a
  stable prefix (a prompt cache bills a repeat of it at a tenth of the
  price or less).
- Warm: the sentences of the best recall hits that hold the question's
  rarer words, each with a pointer to its turn.
- Cold: the record itself, lossless; `expand` fetches any pointer's whole
  turn exactly as recorded.

`context` assembles the first two for a question within a token budget.

Hot tier:

Standing instructions and stated preferences ("Always ...", "Never ...",
"I prefer X over Y") apply to later replies whatever those replies are
about, so the question that needs one rarely shares its words ("What time
is our next meeting?" against "Always use 24-hour time when I ask about
meetings"). Ranking cannot find them; loading them can. `directives` pulls
the sentences out deterministically, with where they came from, and
`standing` loads them with every recall within a fixed token budget.

Measured on BEAM (`python bench/beam.py --consolidate`); every choice here
was fixed on the 500K conversations before the other sizes were run
(bench/beam_consolidate.md).
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Iterable, Optional

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_DIRECTIVE = re.compile(
    r"^\s*(?:please\s+)?(?:always|never|don't ever|do not ever)\b"
    r"|\bI (?:really |strongly |generally |usually |always )?"
    r"(?:prefer|prioriti[sz]e|prioriti[sz]ed|favou?r|would rather|'d rather)\b"
    r"|\bI've been (?:told|instructed) to always\b",
    re.I,
)


def directive_sentences(text: str) -> list:
    """The sentences of `text` that state a standing instruction or a
    preference."""
    return [m.group(0).strip() for m in _SENTENCE.finditer(text) if _DIRECTIVE.search(m.group(0))]


def directives(messages: Iterable[tuple], speakers: Optional[set] = None) -> list:
    """[(source, speaker, text), ...] -> [{"source", "speaker", "text"}],
    in order. `speakers` limits it to the people whose instructions count
    (by default every speaker)."""
    out = []
    for source, speaker, text in messages:
        if speakers is not None and speaker not in speakers:
            continue
        s = directive_sentences(text)
        if s:
            out.append({"source": source, "speaker": speaker, "text": " ".join(s)})
    return out


def _words(s: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", s.lower()))


def standing(found: list, query: str = "", budget: int = 1500) -> list:
    """The directives to load with a reply, within `budget` tokens (4
    characters each). All of them while they fit; past that, the ones
    sharing most words with the query, then the most recent, returned in
    record order."""
    cost = lambda d: (len(d["text"]) + 3) // 4  # noqa: E731
    if sum(cost(d) for d in found) <= budget:
        return list(found)
    q = _words(query)
    ranked = sorted(range(len(found)), key=lambda i: (-len(q & _words(found[i]["text"])), -i))
    keep, spent = set(), 0
    for i in ranked:
        if spent + cost(found[i]) <= budget:
            keep.add(i)
            spent += cost(found[i])
    return [found[i] for i in sorted(keep)]


# ----------------------------------------------------------------- warm tier
# The selection measured in bench/beam_compress.py (same stop words, words,
# scoring): sentences of the top hits scored by the summed idf of the
# question's words they hold over sqrt(hit rank + 1), each with the
# sentence after it.

STOP = frozenset("""a an the and or but if of to in on at by for with from as is are was were be been being it its
this that these those i you your my me we our they them their he she his her what which who whom when where why
how should mention response llm about into than then so not no yes do does did done can could would will just
also any some all each more most other such only own same very s t don now there here up out over under
again""".split())
_WORD = re.compile(r"[a-z0-9]+(?:[.,:/-][0-9]+)*")
_ASSISTANTS = frozenset({"assistant", "claude", "model", "ai", "bot", "system"})


def _terms(s: str) -> list:
    return _WORD.findall(s.lower())


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


def sentences(text: str) -> list:
    """(start, end, text) of each sentence, in code points."""
    out = []
    for m in _SENTENCE.finditer(text):
        raw = m.group(0)
        lead = len(raw) - len(raw.lstrip())
        body = raw.strip()
        if body:
            out.append((m.start() + lead, m.start() + lead + len(body), body))
    return out


def select_sentences(question: str, hits: list, df: Counter, n_sent: int, budget: int) -> list:
    """`hits`: [{"text", "start", "pointer", ...}] best first. Returns
    [{"pointer", "start", "end", "text"}] within `budget` tokens, best
    first; start/end are code-point offsets in the hit's episode (the
    span covers the sentence and the one after it)."""
    qw = {w for w in _terms(question) if w not in STOP and len(w) > 2}
    cand = []
    for rank, h in enumerate(hits[:50]):
        ss = sentences(h["text"])
        for i, (a, b, text) in enumerate(ss):
            w = sum(math.log(n_sent / (1 + df[x])) for x in qw & set(_terms(text)))
            if w > 0:
                end = ss[i + 1][1] if i + 1 < len(ss) else b
                body = h["text"][a:end]  # the record's own characters
                cand.append((w / math.sqrt(rank + 1), rank, i, h, a, end, body))
    cand.sort(key=lambda c: -c[0])
    out, spent, seen = [], 0, set()
    for _, rank, i, h, a, end, body in cand:
        if (rank, i) in seen:
            continue
        cost = tokens(f"[{h['pointer']}] {body}\n")
        if spent + cost > budget:
            continue
        seen |= {(rank, i), (rank, i + 1)}
        out.append({"pointer": h["pointer"], "speaker": h.get("speaker"), "time": h.get("time"),
                    "start": h.get("start", 0) + a, "end": h.get("start", 0) + end, "text": body})
        spent += cost
    return out


# ----------------------------------------------------------------- the record
class Tiers:
    """The hot and warm tiers over a Memory, for one reader. What the slow
    part needs (every turn's directives, sentence counts for idf) is worked
    out once per state of the log and kept until the log grows."""

    def __init__(self, memory, reader: str, speakers: Optional[set] = None, ranker=None):
        """`ranker`: a gnomon.rank.Ranker to order the warm tier's candidates
        by the weak-signal weighting (words, meaning, surprise...); without
        one, recall's own order is used."""
        self.memory, self.reader, self.speakers, self.ranker = memory, reader, speakers, ranker
        self._at = None

    def _turns(self, as_of: Optional[str]):
        comps = self.memory.writers()[self.reader]["compartments"]
        st = self.memory.state(as_of=as_of, compartments=comps)
        eps = sorted(st["state"]["episodes"].items(), key=lambda kv: kv[1]["order"])
        for eid, ep in eps:
            content = ep["body"]["content"]
            for n, t in enumerate(ep["body"]["turns"]):
                yield f"{eid}#{n}", t["speaker"], content[t["start"]:t["end"]]

    def _refresh(self, as_of: Optional[str]) -> None:
        # The log only grows, so its size and modification time change with
        # every write; re-reading the state is needed only then.
        st = os.stat(self.memory.path)
        key = (st.st_size, st.st_mtime_ns, as_of)
        if self._at == key:
            return
        found, df, n = [], Counter(), 0
        for pointer, speaker, text in self._turns(as_of):
            for _, _, body in sentences(text):
                df.update(set(_terms(body)))
                n += 1
            ok = (speaker in self.speakers) if self.speakers is not None else speaker.lower() not in _ASSISTANTS
            if ok:
                found += directives([(pointer, speaker, text)])
        self.found, self.df, self.n_sent, self._at = found, df, max(n, 1), key

    def context(self, query: str, *, budget: int = 2000, directive_budget: int = 1500,
                as_of: Optional[str] = None, **recall_opts) -> dict:
        """The hot and warm tiers for `query`: {"standing": [...], "view":
        [...], "text": both rendered, "tokens": {...}}. The standing part
        comes first and changes only when a new directive is recorded, so a
        prompt cache can hold it."""
        self._refresh(as_of)
        hot = standing(self.found, query, directive_budget)
        if self.ranker is not None:
            hits = self.ranker.rank(query, k=50, as_of=as_of, **recall_opts)
        else:
            r = self.memory.recall(self.reader, query, k=50, kinds=["turns"], as_of=as_of, **recall_opts)
            hits = [{"pointer": f"{h['episode']}#{h['turn']}", "text": h["text"], "start": h["start"],
                     "speaker": h["speaker"], "time": h["time"]} for h in r["hits"] if "episode" in h]
        warm = select_sentences(query, hits, self.df, self.n_sent, budget)
        lines = []
        if hot:
            lines.append("Standing instructions and preferences (in force unless replaced later):")
            lines += [f"[{d['source']}] {d['speaker']}: {d['text']}" for d in hot]
            lines.append("")
        lines.append(f"From the record, for: {query}")
        lines += [f"[{s['pointer']}] {s['speaker']}: {s['text']}" for s in warm]
        lines.append("(Each [pointer] can be expanded to its whole turn, exactly as recorded.)")
        text = "\n".join(lines)
        return {"standing": hot, "view": warm, "text": text,
                "tokens": {"standing": sum(tokens(d["text"]) for d in hot),
                           "view": sum(tokens(s["text"]) for s in warm), "total": tokens(text)}}

    def expand(self, pointer: str, as_of: Optional[str] = None) -> dict:
        """The whole turn a pointer names, exactly as recorded."""
        eid, _, n = pointer.rpartition("#")
        comps = self.memory.writers()[self.reader]["compartments"]
        st = self.memory.select(compartments=comps, episodes=[eid])
        ep = st["episodes"].get(eid)
        if ep is None:
            raise KeyError(f"no episode {eid} visible to {self.reader}")
        t = ep["body"]["turns"][int(n)]
        return {"pointer": pointer, "speaker": t["speaker"], "time": ep["record_time"],
                "text": ep["body"]["content"][t["start"]:t["end"]], "start": t["start"], "end": t["end"]}
