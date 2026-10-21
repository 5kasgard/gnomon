"""What a reply should see without having to ask for it.

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

import re
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
