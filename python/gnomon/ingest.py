"""Turn raw material (transcripts, documents) into claims, deterministically.

Order of work, cheapest and most certain first:

1. **Parse exactly.** Speakers, turns and sentences are found by plain code.
   The same input always gives the same output. Who said which part is
   recorded *in the episode* (its turns), so the core can hold every later
   claim to it.
2. **Classify by visible rules.** Stance, kind, truth and confidence come from
   named language cues; the report says which rule fired.
3. **Leave the rest.** A sentence the rules cannot classify safely (mixed
   signals, negated or conditional decisions, reported speech, too short,
   refers to something said earlier, no known speaker, hidden characters) is
   returned as *residue* for a model or a person to judge. It is never guessed.

Every claim written here is the speaker's sentence verbatim, quoted with its
position, and the core checks the words, the speaker's turn and the statement
against the episode. Even a bug here cannot put words in anyone's mouth.

Offsets are Unicode code points (Python string indices), matching the core.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

__all__ = [
    "Turn",
    "Sentence",
    "Candidate",
    "Residue",
    "Report",
    "parse_transcript",
    "parse_document",
    "split_sentences",
    "classify",
    "extract",
    "record_transcript",
    "record_messages",
    "record_document",
    "ingest",
]


# ------------------------------------------------------------------ parsing

@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str
    start: int  # code-point offset of `text` in the episode


@dataclass(frozen=True)
class Sentence:
    speaker: str  # "" if no known speaker
    text: str
    start: int  # code-point offset of `text` in the episode


# A speaker label at the start of a line: optional "[10:31]" stamp, optional
# markdown bold, the name, a colon. Only names in the participant list count,
# so quoted text ("Alex: ..." inside Claude's turn is still possible, see
# below) or labels like "Step 1:" never become speakers.
_LABEL = re.compile(r"^[ \t]*(?:\[[^\]\n]{1,24}\][ \t]*)?(?:\*\*|__)?(?P<name>[^\n:*_]{1,40}?)(?:\*\*|__)?[ \t]*:(?:\*\*|__)?(?:[ \t]+|$)")


def _lines(content: str):
    """(offset, line) for every line, splitting on every Unicode line break."""
    pos = 0
    for line in content.splitlines(keepends=True):
        yield pos, line
        pos += len(line)


def parse_transcript(content: str, participants: Iterable[str]) -> tuple[list[Turn], list[Sentence]]:
    """Split a transcript into turns of known participants.

    Returns (turns, unattributed): text before the first recognised speaker
    has no speaker and is returned separately, never dropped silently.
    """
    names = _check_names(participants)
    # Every labelled line ends the turn before it. A label that is not a
    # participant ("Bob:", "Step 1:") starts text with no known speaker, so
    # it can never be absorbed into the previous speaker's turn.
    marks: list[tuple[int, int, str]] = []  # (line start, body start, speaker or "")
    for pos, line in _lines(content):
        m = _LABEL.match(line)
        if m:
            marks.append((pos, pos + m.end(), names.get(m.group("name").strip().casefold(), "")))
    turns, unattributed = [], []
    first = marks[0][0] if marks else len(content)
    _unattributed(content, 0, first, unattributed)
    for i, (line_start, body_start, speaker) in enumerate(marks):
        body_end = marks[i + 1][0] if i + 1 < len(marks) else len(content)
        if not speaker:
            _unattributed(content, line_start, body_end, unattributed)
            continue
        raw = content[body_start:body_end]
        lead = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if text:
            turns.append(Turn(speaker, text, body_start + lead))
    return turns, unattributed


def _unattributed(content: str, a: int, b: int, out: list[Sentence]) -> None:
    piece = content[a:b]
    if piece.strip():
        out.append(Sentence("", piece.strip(), a + len(piece) - len(piece.lstrip())))


def _check_names(participants: Iterable[str]) -> dict[str, str]:
    """casefold -> name; refuses names that look alike or hide a line break."""
    names: dict[str, str] = {}
    for p in participants:
        if not p or any(c in p for c in "\r\n\t") or not unicodedata.is_normalized("NFC", p):
            raise ValueError(f"participant name {p!r} is empty, not in NFC form, or has a line break or tab")
        key = p.casefold()
        if key in names:
            raise ValueError(f"participants {names[key]!r} and {p!r} differ only in letter case")
        names[key] = p
    return names


def parse_document(content: str, speaker: str) -> list[Turn]:
    """A document has one speaker (its author); each paragraph is a turn."""
    turns = []
    for m in re.finditer(r"\S(?:.*\S)?(?:\n(?!\s*\n).*\S)*", content):
        turns.append(Turn(speaker, m.group(0), m.start()))
    return turns


# Abbreviations whose full stop does not end a sentence.
_ABBREV = {
    "e.g", "i.e", "etc", "vs", "cf", "dr", "mr", "mrs", "ms", "prof", "st", "no",
    "fig", "approx", "incl", "dept", "est", "jan", "feb", "mar", "apr", "jun",
    "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
_WORD_BEFORE = re.compile(r"[\w.]+\Z")
_END = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)|\n\s*\n")


def split_sentences(turn: Turn) -> list[Sentence]:
    """Split a turn into sentences, keeping exact offsets.

    Ends at . ! ? followed by whitespace (or a blank line), except after a
    known abbreviation, a single initial ("J. Smith") or inside a number
    ("3.5" never matches, since no whitespace follows the point).
    """
    text, out, begin = turn.text, [], 0
    for m in _END.finditer(text):
        end = m.end()
        if text[m.start()] == ".":
            # Only the word right before the point matters; looking at a
            # bounded window keeps this linear ("e.g. e.g. e.g. ...").
            word = _WORD_BEFORE.search(text, max(begin, m.start() - 24), m.start())
            w = word.group(0).lower() if word else ""
            if w in _ABBREV or (len(w) == 1 and w.isalpha()):
                continue
        _emit(turn, text, begin, end, out)
        begin = end
    _emit(turn, text, begin, len(text), out)
    return out


def _emit(turn: Turn, text: str, a: int, b: int, out: list[Sentence]) -> None:
    piece = text[a:b]
    lead = len(piece) - len(piece.lstrip())
    piece = piece.strip()
    if piece:
        out.append(Sentence(turn.speaker, piece, turn.start + a + lead))


# ----------------------------------------------------------- classification

@dataclass(frozen=True)
class Candidate:
    sentence: Sentence
    statement_kind: str
    stance: str
    truth: str
    confidence: int
    rule: str  # the named rule that decided it


@dataclass(frozen=True)
class Residue:
    sentence: Sentence
    reason: str


def _rx(words: str) -> re.Pattern:
    return re.compile(r"(?<![\w'])(?:" + words + r")(?![\w'])", re.IGNORECASE)


# Rules are data: each is (name, pattern).
DECIDED = [
    ("decided.lets", _rx(r"let'?s|let us")),
    ("decided.go_with", _rx(r"(?:we|i)(?:'ll| will| are going to|'re going to)? go with|we(?:'re| are) going with")),
    ("decided.decided", _rx(r"(?:we|i)(?:'ve| have)? decided|decision(?: is|:)|final answer")),
    ("decided.will_use", _rx(r"we(?:'ll| will) use|we(?:'re| are) using|we use")),
]
SUGGESTED = [
    ("suggested.hedge", _rx(r"maybe|perhaps|possibly")),
    ("suggested.modal", _rx(r"could|might|we should consider|worth considering|how about|what about|what if")),
    ("suggested.suggest", _rx(r"i(?:'d| would)? suggest|i(?:'d| would)? recommend|i propose|one option")),
]
HYPOTHETICAL = [
    ("hypothetical.if", re.compile(r"^\s*(?:if|suppose|supposing|imagine|assuming|hypothetically|unless)\b", re.IGNORECASE)),
]
PROMISE = [
    ("promise.will_do", _rx(r"i(?:'ll| will) (?:send|do|build|write|fix|finish|check|make|get|handle|look into|follow up)")),
    # Any first-person commitment about the future: a promise, not a fact.
    ("promise.commit", _rx(r"(?:i|we)(?:'ll| will| shall|'m going to| am going to|'re going to| are going to| promise)")),
]
# Other statements about the future are forecasts: not true or false yet.
FUTURE = _rx(r"will|won't|shall|going to|gonna|'ll|tomorrow|next (?:week|month|year)")
# Confidence cues for plain assertions.
WEAK = _rx(r"i think|i believe|probably|likely|i guess|seems|apparently|not sure|i suspect|i'm not sure")
STRONG = _rx(r"definitely|certainly|always|never|without doubt|for sure|guaranteed")

# Guards: cues that make a rule's reading unsafe. Each sends the sentence to
# residue rather than letting a surface phrase decide.
GUARDS = [
    # Somebody else's words reported: the speaker isn't asserting them.
    ("reports someone else's words",
     _rx(r"said|says|claims?|claimed|told|tells|according to|reportedly|thinks|thought|believes")),
    # A condition anywhere but the start (a leading one is hypothetical).
    ("conditional", re.compile(r"(?<=\S)\s+(?:if|unless|provided that|as long as|only if)\b", re.IGNORECASE)),
    # A question asked without a question mark.
    ("reads as a question",
     re.compile(r"^\s*(?:do|does|did|should|shall|can|could|would|will|is|are|was|were|may|have|has)\s+(?:we|i|you|it|they|he|she|this|that)\b", re.IGNORECASE)),
]
# Decisions and promises are guarded against "when": "we use Postgres when
# we need transactions" is a policy with a condition, not a plain decision.
# Plain descriptions keep it ("Python is slow when used for tight loops").
WHEN = re.compile(r"(?<=\S)\s+(?:when|whenever|once|after|before)\s", re.IGNORECASE)
# "..., not Go" / "and not Go" after a decision is a contrast, not a negation,
# unless what follows "not" defers or doubts the decision ("but not yet",
# ", not really").
_DEFERRALS = ("yet|now|anymore|any more|really|ever|decided|final|finally|sure|quite|entirely|completely|"
              "necessarily|always|today|tomorrow|soon|certain|definitely|officially|settled|confirmed")
_CONTRAST = re.compile(
    r"(?:,\s*|\s+(?:and|but)\s+)(?:not|rather than|instead of)\s+(?!(?:" + _DEFERRALS + r")\b)[\w'-]+(?:\s+[\w'-]+)?",
    re.IGNORECASE)
# Only decisions are guarded against negation and "let's see": "we haven't
# decided" is not a decision, nor is "let's say ...".
DECISION_GUARDS = [
    ("not a decision", _rx(r"let us know|let me know|let'?s (?:say|see|decide|discuss|think|talk|chat|wait|check|consider|figure)")),
    ("negated or undecided decision", _rx(r"not|n't|never|whether|no longer|undecided|yet to")),
]

# Sentences that cannot stand alone: what they mean lives elsewhere.
_CONTEXT_ONLY = re.compile(
    r"^(?:yes|yeah|yep|no|nope|ok(?:ay)?|sure|agreed|right|exactly|correct|thanks?(?: you)?|"
    r"same|true|false|done|cool|great|nice|good|perfect|fair|hmm+|ha+|lol)\W*$",
    re.IGNORECASE,
)
# Opening with a pronoun points back at something said earlier, whatever the
# sentence's length. Treating these as residue loses a few standalone
# sentences ("It is raining") but never records half a thought as a claim.
_DEICTIC = re.compile(r"^(?:that|this|it|those|these|they|he|she|which)(?:'s|'re)?\b", re.IGNORECASE)
# Phrases that only make sense with earlier context.
_ANAPHORIC = re.compile(
    r"(?<![\w'])(?:all (?:two|three|four|five|of them|of those)|both of (?:them|those)|either one|"
    r"the (?:first|second|third|last|latter|former|other) one|the above|as above|as before|"
    r"same as (?:before|above)|that one|this one|i agree|i disagree|you're right|good point)(?![\w'])"
    r"|(?<![\w'])then\W*$",
    re.IGNORECASE,
)

BASE_CONFIDENCE = {"asserted": 7000, "decided": 9000, "suggested": 5000, "hypothetical": 5000, "asked": 5000}
_ODD = set(f"{chr(0x034f)}{chr(0x115f)}{chr(0x1160)}{chr(0x3164)}{chr(0xffa0)}{chr(0x2800)}{chr(0xfffc)}")


def _hidden_char(ch: str) -> bool:
    """A character that does not show when printed, or a control character
    (newline and tab aside). Mirrors the core's rule; the core is still the
    authority and refuses anything it sees."""
    if ch in "\n\t":
        return False
    if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"):
        return True
    o = ord(ch)
    return 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or ch in _ODD


def _hidden(s: str) -> bool:
    return any(_hidden_char(ch) for ch in s)


def classify(s: Sentence, speakers: Iterable[str] = ()) -> Candidate | Residue:
    """Classify one sentence by rules, or return it as residue with a reason."""
    t = s.text
    if not s.speaker:
        return Residue(s, "no known speaker")
    if _hidden(t):
        return Residue(s, "contains hidden or control characters")
    for name in speakers:
        # A speaker label inside a sentence: someone else's words are quoted
        # here, or the transcript layout wasn't understood.
        if re.search(r"(?:^|\s)" + re.escape(name) + r"\s*:", t, re.IGNORECASE):
            return Residue(s, "contains a speaker label")
    words = re.findall(r"[\w']+", t)
    if _CONTEXT_ONLY.match(t):
        return Residue(s, "only meaningful with what was said before")
    if len(words) < 3:
        return Residue(s, "too short to stand alone")
    if _DEICTIC.match(t) or _ANAPHORIC.search(t):
        return Residue(s, "refers to something said earlier")

    if t.rstrip().endswith("?"):
        return Candidate(s, "ask", "asked", "unknown", BASE_CONFIDENCE["asked"], "asked.question_mark")

    hypothetical = [n for n, p in HYPOTHETICAL if p.search(t)]
    if hypothetical:
        # A sentence that opens as a condition is hypothetical as a whole,
        # even if the condition itself says "we use" or "maybe".
        return Candidate(s, "imagine", "hypothetical", "unknown", BASE_CONFIDENCE["hypothetical"], hypothetical[0])
    for reason, p in GUARDS:
        if p.search(t):
            return Residue(s, reason)

    decided = [n for n, p in DECIDED if p.search(t)]
    suggested = [n for n, p in SUGGESTED if p.search(t)]
    weak, strong = bool(WEAK.search(t)), bool(STRONG.search(t))
    if decided and (suggested or weak):
        return Residue(s, "mixed signals: " + ", ".join(sorted(decided + suggested + (["weak cue"] if weak else []))))
    if decided:
        if WHEN.search(t):
            return Residue(s, "conditional")
        uncontrasted = _CONTRAST.sub("", t)
        for reason, p in DECISION_GUARDS:
            if p.search(uncontrasted):
                return Residue(s, reason)
        return Candidate(s, "describe", "decided", "true", BASE_CONFIDENCE["decided"], decided[0])
    if suggested:
        return Candidate(s, "describe", "suggested", "unknown", BASE_CONFIDENCE["suggested"], suggested[0])

    promise = [n for n, p in PROMISE if p.search(t)]
    if promise and WHEN.search(t):
        return Residue(s, "conditional")
    if promise:
        return Candidate(s, "promise", "asserted", "unknown", BASE_CONFIDENCE["asserted"], promise[0])

    if FUTURE.search(t):
        return Candidate(s, "describe", "asserted", "unknown", BASE_CONFIDENCE["asserted"], "asserted.forecast")
    if weak and strong:
        return Residue(s, "mixed signals: weak and strong confidence cues")
    if weak:
        return Candidate(s, "describe", "asserted", "true", 5500, "asserted.hedged")
    if strong:
        return Candidate(s, "describe", "asserted", "true", 8500, "asserted.emphatic")
    return Candidate(s, "describe", "asserted", "true", BASE_CONFIDENCE["asserted"], "asserted.plain")


# ------------------------------------------------------------------ driving

@dataclass
class Report:
    episode: str
    written: list[tuple[str, Candidate]] = field(default_factory=list)  # (claim id, candidate)
    residue: list[Residue] = field(default_factory=list)
    refused: list[tuple[Candidate, str]] = field(default_factory=list)  # (candidate, gate error code)


def _classify_all(turns: list[Turn], unattributed: list[Sentence], speakers: list[str]):
    cands, residue = [], [Residue(u, "no known speaker") for u in unattributed]
    for turn in turns:
        for sentence in split_sentences(turn):
            r = classify(sentence, speakers)
            (cands if isinstance(r, Candidate) else residue).append(r)
    return cands, residue


def extract(content: str, *, participants: Iterable[str] = (), author: Optional[str] = None
            ) -> tuple[list[Candidate], list[Residue]]:
    """Pure function: content in, candidates and residue out. Writes nothing.

    Pass `participants` for a transcript, or `author` for a document.
    """
    participants = list(participants)
    if author:
        return _classify_all(parse_document(content, author), [], [author])
    turns, unattributed = parse_transcript(content, participants)
    return _classify_all(turns, unattributed, participants)


def _turn_records(turns: list[Turn]) -> list[dict]:
    return [{"speaker": t.speaker, "start": t.start, "end": t.start + len(t.text)} for t in turns]


def record_transcript(memory, writer: str, content: str, *, participants: Iterable[str], compartment: str,
                      medium: str = "chat", record_time: Optional[str] = None) -> str:
    """Record a transcript as an episode with its turns; returns the event id.

    The content is kept whole. The turns say who said which part, and the
    core holds every quoted claim to them.
    """
    participants = list(participants)
    turns, _ = parse_transcript(content, participants)
    body = {
        "compartment": compartment,
        "content": content,
        "medium": medium,
        "participants": participants,
        "turns": _turn_records(turns),
    }
    return memory.append(writer, "episode", body, record_time)["event_id"]


def record_messages(memory, writer: str, messages: Iterable[tuple[str, str]], *, compartment: str,
                    medium: str = "chat", record_time: Optional[str] = None) -> str:
    """Record structured messages [(speaker, text), ...] as an episode.

    Prefer this whenever the source already says who sent each message (chat
    exports, APIs): the turns come from the source, so nothing about who said
    what is parsed or guessed. In a plain-text transcript, a line such as
    "Alex: ..." quoted inside someone else's message is indistinguishable
    from Alex speaking.
    """
    messages = list(messages)
    _check_names(dict.fromkeys(sp for sp, _ in messages))
    parts, turns, pos, participants = [], [], 0, []
    for speaker, text in messages:
        label = f"{speaker}: "
        body_text = text.strip()
        if not body_text:
            continue
        if parts:
            parts.append("\n")
            pos += 1
        parts.append(label + body_text)
        turns.append(Turn(speaker, body_text, pos + len(label)))
        pos += len(label) + len(body_text)
        if speaker not in participants:
            participants.append(speaker)
    body = {
        "compartment": compartment,
        "content": "".join(parts),
        "medium": medium,
        "participants": participants,
        "turns": _turn_records(turns),
    }
    return memory.append(writer, "episode", body, record_time)["event_id"]


def record_document(memory, writer: str, content: str, *, author: str, compartment: str,
                    medium: str = "document", record_time: Optional[str] = None) -> str:
    """Record a document by one author (no turns needed); returns the event id."""
    _check_names([author])
    body = {"compartment": compartment, "content": content, "medium": medium, "participants": [author]}
    return memory.append(writer, "episode", body, record_time)["event_id"]


Judge = Callable[[Residue, str], Optional[Candidate]]


def ingest(memory, writer: str, episode_id: str, *, frame: str, compartment: str,
           judge: Optional[Judge] = None, record_time: Optional[str] = None) -> Report:
    """Extract claims from an episode already in `memory` and record them.

    Who said what is taken from the episode itself (its turns, or its single
    participant), never re-guessed. `judge` (optional, a model or a person)
    is asked about residue only. It may only choose the classification of the
    sentence it was asked about; the sentence, speaker and quote are always
    the original. Its claims are recorded as "inferred", confidence <= 7000.
    """
    from . import GateError, claim

    state = memory.state()["state"]
    me = state["writers"].get(writer)
    my_party = me["party"] if me else None
    visible = memory.state(compartments=me["compartments"] if me else [])["state"]
    ep = visible["episodes"].get(episode_id)
    if ep is None:
        raise GateError("E_REF", f"episode '{episode_id}' does not exist or is not accessible")
    body = ep["body"]
    content, participants = body["content"], list(body.get("participants", []))
    # If the writer's own party recorded this episode, its quotes of anyone
    # else are that party's account of what was said: capped at 7000, as
    # the core requires.
    recorder = state["writers"].get(ep["writer"], {})
    self_recorded = my_party is not None and recorder.get("party") == my_party
    _check_names(participants)  # look-alike names: refuse rather than attribute
    report = Report(episode_id)
    if body.get("turns"):
        turns = [Turn(t["speaker"], content[t["start"]:t["end"]], t["start"]) for t in body["turns"]]
        covered = sorted((t["start"], t["end"]) for t in body["turns"])
        gaps, pos = [], 0
        for a, b in covered + [(len(content), len(content))]:
            gap = content[pos:a]
            label = _LABEL.match(gap.strip())
            only_label = label is not None and not gap.strip()[label.end():].strip()
            if gap.strip() and not only_label:
                lead = len(gap) - len(gap.lstrip())
                gaps.append(Sentence("", gap.strip(), pos + lead))
            pos = b
        cands, residue = _classify_all(turns, gaps, participants)
    elif len(participants) == 1:
        cands, residue = _classify_all(parse_document(content, participants[0]), [], participants)
    else:
        report.residue = [Residue(Sentence("", content.strip(), len(content) - len(content.lstrip())),
                                  "episode records no turns: cannot tell who said what")]
        return report

    judged: list[tuple[Candidate, bool]] = [(c, False) for c in cands]
    if judge is not None:
        still = []
        for r in residue:
            answer = judge(r, content) if r.sentence.speaker else None
            if answer is None:
                still.append(r)
            else:
                # Only the classification is the judge's; the words and the
                # speaker stay exactly what the episode says.
                judged.append((Candidate(r.sentence, answer.statement_kind, answer.stance, answer.truth,
                                         answer.confidence, "judge:" + answer.rule), True))
        residue = still
    report.residue = residue

    def _own(speaker: str) -> bool:
        return speaker.lower() in (writer.lower(), (my_party or "").lower())

    for c, by_judge in judged:
        s = c.sentence
        body = claim(
            s.text,
            speaker=s.speaker,
            frame=frame,
            compartment=compartment,
            stance=c.stance,
            truth=c.truth,
            confidence=min(c.confidence, 7000) if by_judge or (self_recorded and not _own(s.speaker)) else c.confidence,
            origin="inferred" if by_judge else "direct",
            statement_kind=c.statement_kind,
            source_episode=episode_id,
            quote=s.text,
            quote_start=s.start,
        )
        try:
            report.written.append((memory.remember(writer, body, record_time), c))
        except GateError as e:
            report.refused.append((c, e.code))
    return report
