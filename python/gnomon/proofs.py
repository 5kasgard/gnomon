"""Read and independently check the proofs that come with query answers.

A query answer is only as good as its proof, so this module checks proofs
without trusting the Rust core: it has its own Datalog parser and printer,
its own canonical JSON and SHA-256 (from the standard library), and reads
the log file directly.

`check(result, ...)` confirms, for every node of every proof:

* **Derived facts**: the rule is literally one of the program's rules or the
  built-in views (parsed here, independently); the fact is the rule's head
  under some substitution; the premises are exactly its positive literals,
  in order; every comparison and `contains` holds; and the atoms recorded
  as absent are exactly its negated literals.
* **Base facts**: every cited event is in the log with that hash (recomputed
  here), the log's hash chain is unbroken up to it, it was recorded no later
  than the query's `as_of`, and for every relation read directly from an
  event, the event really says it (the claim's id is the hash of that body,
  the field has that value, the writer is that writer...).

* **Disputes**: `truth`, `defeated`, `disputed` and `resolution` are
  re-derived here by folding the log up to the end of the reader's view
  (`through`), and must match, value and cited events alike.

What it cannot check from a proof alone: that an absent fact is really
absent (absence is judged against the whole visible log, not just the
proof). That is re-derivable by running the query again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ._jcs import canonical

__all__ = ["parse", "rule_text", "check", "explain", "Rule"]

# ------------------------------------------------------------------ parsing

OPS = ("!=", "<=", ">=", "=", "<", ">")


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Const:
    value: Any  # int or str


WILD = "_"


@dataclass
class Atom:
    pred: str
    args: list


@dataclass
class Rule:
    head: Atom
    body: list  # ("pos", Atom) | ("neg", Atom) | ("cmp", op, a, b) | ("contains", a, b)


def _lex(src: str):
    i, n, line = 0, len(src), 1
    out = []
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "%":
            while i < n and src[i] != "\n":
                i += 1
        elif c in "(),.":
            out.append((c, None))
            i += 1
        elif src.startswith(":-", i) or src.startswith("?-", i):
            out.append((src[i:i + 2], None))
            i += 2
        elif any(src.startswith(op, i) for op in OPS):
            op = next(op for op in OPS if src.startswith(op, i))
            out.append(("op", op))
            i += len(op)
        elif c == '"':
            i += 1
            buf = []
            while True:
                if i >= n or src[i] == "\n":
                    raise ParseError(f"line {line}: unterminated string")
                ch = src[i]
                if ch == '"':
                    i += 1
                    break
                if ch == "\\":
                    nxt = src[i + 1] if i + 1 < n else ""
                    simple = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}
                    if nxt in simple:
                        buf.append(simple[nxt])
                        i += 2
                    elif nxt == "u" and src.startswith("{", i + 2):
                        end = src.find("}", i + 3)
                        digits = src[i + 3:end] if end > 0 else ""
                        if not digits or len(digits) > 6 or any(d not in "0123456789abcdefABCDEF" for d in digits):
                            raise ParseError(f"line {line}: bad \\u{{hex}} escape")
                        cp = int(digits, 16)
                        if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                            raise ParseError(f"line {line}: not a character")
                        buf.append(chr(cp))
                        i = end + 1
                    else:
                        raise ParseError(f"line {line}: unknown escape")
                else:
                    buf.append(ch)
                    i += 1
            out.append(("str", "".join(buf)))
        elif c.isascii() and (c.isdigit() or c == "-"):
            j = i + 1
            while j < n and src[j].isascii() and src[j].isdigit():
                j += 1
            if src[i:j] == "-":
                raise ParseError(f"line {line}: '-' must start a number")
            v = int(src[i:j])
            if not -(2**63) <= v < 2**63:
                raise ParseError(f"line {line}: integer out of range")
            out.append(("int", v))
            i = j
        elif c.isascii() and (c.isalpha() or c == "_"):
            j = i + 1
            while j < n and src[j].isascii() and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            out.append(("var" if (word[0].isupper() or word[0] == "_") else "ident", word))
            i = j
        else:
            raise ParseError(f"line {line}: unexpected character {c!r}")
    return out


def parse(src: str) -> tuple[list[Rule], Optional[list]]:
    """Parse a program into rules and (if present) the query body."""
    toks = _lex(src)
    pos = 0
    rules: list[Rule] = []
    query = None

    def peek(k=0):
        return toks[pos + k] if pos + k < len(toks) else (None, None)

    def take(kind):
        nonlocal pos
        t = peek()
        if t[0] != kind:
            raise ParseError(f"expected {kind}, found {t}")
        pos += 1
        return t[1]

    def term():
        nonlocal pos
        kind, v = peek()
        pos += 1
        if kind == "var":
            return WILD if v == "_" else Var(v)
        if kind in ("str", "ident"):
            return Const(v)
        if kind == "int":
            return Const(v)
        raise ParseError(f"expected a term, found {kind}")

    def literal():
        nonlocal pos
        if peek()[0] == "ident" and peek()[1] == "not" and peek(1)[0] == "ident":
            pos += 1
            lit = positive()
            if lit[0] != "pos":
                raise ParseError("'not' must be followed by an atom")
            return ("neg", lit[1])
        return positive()

    def positive():
        nonlocal pos
        if peek()[0] == "ident" and peek()[1] == "not" and peek(1)[0] == "ident":
            raise ParseError("'not' cannot be repeated")
        if peek()[0] == "ident" and peek(1)[0] == "(":
            name = take("ident")
            take("(")
            args = []
            if peek()[0] != ")":
                args.append(term())
                while peek()[0] == ",":
                    pos += 1
                    args.append(term())
            take(")")
            if name == "contains":
                return ("contains", args[0], args[1])
            return ("pos", Atom(name, args))
        start_kind = peek()[0]
        left = term()
        if peek()[0] == "op":
            op = take("op")
            return ("cmp", op, left, term())
        if start_kind == "ident":
            return ("pos", Atom(left.value, []))
        raise ParseError("expected an atom or a comparison")

    def body():
        nonlocal pos
        lits = [literal()]
        while peek()[0] == ",":
            pos += 1
            lits.append(literal())
        return lits

    while pos < len(toks):
        if peek()[0] == "?-":
            pos += 1
            query = body()
            take(".")
            continue
        head = literal()
        if head[0] != "pos":
            raise ParseError("a rule's head must be an atom")
        lits = []
        if peek()[0] == ":-":
            pos += 1
            lits = body()
        take(".")
        rules.append(Rule(head[1], lits))
    return rules, query


def query_rule(query: list) -> Rule:
    """The query as the core runs it: a rule for "?" over its named
    variables in order of first appearance (names starting with _ are not
    reported)."""
    names: list[str] = []

    def visit(t):
        if isinstance(t, Var) and t.name not in names and not t.name.startswith("_"):
            names.append(t.name)

    for lit in query:
        if lit[0] in ("pos", "neg"):
            for a in lit[1].args:
                visit(a)
        elif lit[0] == "cmp":
            visit(lit[2])
            visit(lit[3])
        else:
            visit(lit[1])
            visit(lit[2])
    return Rule(Atom("?", [Var(n) for n in names]), query)


# ----------------------------------------------------------------- printing

def _val_text(v) -> str:
    if isinstance(v, int):
        return str(v)
    out = ['"']
    for ch in v:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append("\\u{%x}" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _term_text(t) -> str:
    if t == WILD:
        return "_"
    if isinstance(t, Var):
        return t.name
    return _val_text(t.value)


def _atom_text(a: Atom) -> str:
    if not a.args:
        return a.pred
    return a.pred + "(" + ", ".join(_term_text(t) for t in a.args) + ")"


def rule_text(r: Rule) -> str:
    """The canonical text of a rule, exactly as proofs print it."""
    s = _atom_text(r.head)
    if r.body:
        parts = []
        for lit in r.body:
            if lit[0] == "pos":
                parts.append(_atom_text(lit[1]))
            elif lit[0] == "neg":
                parts.append("not " + _atom_text(lit[1]))
            elif lit[0] == "cmp":
                parts.append(f"{_term_text(lit[2])} {lit[1]} {_term_text(lit[3])}")
            else:
                parts.append(f"contains({_term_text(lit[1])}, {_term_text(lit[2])})")
        s += " :- " + ", ".join(parts)
    return s + "."


# ----------------------------------------------------------------- checking

_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def ascii_lower(s: str) -> str:
    """Lower-case ASCII letters only, as `contains` does: the same answer on
    every platform and Unicode version."""
    return s.translate(_ASCII_LOWER)


def _same_type(a, b) -> bool:
    return isinstance(a, int) == isinstance(b, int)


def _cmp(op, a, b) -> bool:
    """Integers compare as numbers, strings by code point; values of
    different types are never equal and never ordered."""
    same = _same_type(a, b)
    if op == "=":
        return same and a == b
    if op == "!=":
        return not (same and a == b)
    if not same:
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _unify(args, values, env) -> Optional[dict]:
    if len(args) != len(values):
        return None
    env = dict(env)
    for t, v in zip(args, values):
        if t == WILD:
            continue
        if isinstance(t, Const):
            if not (_same_type(t.value, v) and t.value == v):
                return None
        elif t.name in env:
            if not (_same_type(env[t.name], v) and env[t.name] == v):
                return None
        else:
            env[t.name] = v
    return env


def _value(t, env):
    if isinstance(t, Const):
        return t.value
    if isinstance(t, Var):
        return env.get(t.name, _UNBOUND)
    return _UNBOUND


_UNBOUND = object()


def _instance_ok(rule: Rule, fact: list, premises: list[list], absent: list[list]) -> bool:
    env = _unify(rule.head.args, fact[1:], {})
    if env is None or rule.head.pred != fact[0]:
        return False
    positives = [lit[1] for lit in rule.body if lit[0] == "pos"]
    if len(positives) != len(premises):
        return False
    negatives = [lit[1] for lit in rule.body if lit[0] == "neg"]
    if len(negatives) != len(absent):
        return False
    pi = ni = 0
    for lit in rule.body:
        kind = lit[0]
        if kind == "pos":
            p = premises[pi]
            pi += 1
            if p[0] != lit[1].pred:
                return False
            env = _unify(lit[1].args, p[1:], env)
            if env is None:
                return False
        elif kind == "neg":
            a = absent[ni]
            ni += 1
            want = [lit[1].pred] + [None if t == WILD else _value(t, env) for t in lit[1].args]
            if any(w is _UNBOUND for w in want) or want != a:
                return False
        elif kind == "cmp":
            op, l, r = lit[1], _value(lit[2], env), _value(lit[3], env)
            if l is _UNBOUND and op == "=" and r is not _UNBOUND:
                env[lit[2].name] = r
            elif r is _UNBOUND and op == "=" and l is not _UNBOUND:
                env[lit[3].name] = l
            elif l is _UNBOUND or r is _UNBOUND or not _cmp(op, l, r):
                return False
        else:
            l, r = _value(lit[1], env), _value(lit[2], env)
            if not (isinstance(l, str) and isinstance(r, str) and ascii_lower(r) in ascii_lower(l)):
                return False
    # The head may use variables bound by '=' after unification with it.
    return _unify(rule.head.args, fact[1:], env) is not None


def _load_log(path) -> tuple[list, list[str]]:
    """The log's events in order, up to the first whose hash or chain link
    does not check out."""
    problems: list[str] = []
    events: list[dict] = []
    prev = "0" * 64
    text = Path(path).read_text(encoding="utf-8")
    for n, line in enumerate(text.split("\n")[:-1]):
        ev = json.loads(line)
        if canonical(ev) != line:
            problems.append(f"log line {n} is not in canonical form")
            break
        body = {k: v for k, v in ev.items() if k != "hash"}
        h = hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
        if h != ev.get("hash") or ev.get("prev") != prev:
            problems.append(f"log line {n}: hash chain broken")
            break
        prev = h
        events.append(ev)
    return events, problems


def _claim_id(body) -> str:
    return "c:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


_FIELD = {
    "statement": "statement", "speaker": "speaker", "frame": "frame", "compartment": "compartment",
    "stance": "stance", "origin": "origin", "statement_kind": "statement_kind", "confidence": "confidence",
    "asserted_truth": "truth", "source": "source_episode", "quote_start": "quote_start",
    "validity_domain": "validity_domain", "valid_from": "valid_from", "valid_to": "valid_to",
}


def _event_says(fact: list, ev: dict) -> bool:
    """Does this event, read directly, state this base fact?"""
    rel, args = fact[0], fact[1:]
    kind, body, eid = ev["kind"], ev["body"], ev["event_id"]
    if rel in _FIELD:
        return kind == "claim" and _claim_id(body) == args[0] and body.get(_FIELD[rel]) == args[1]
    if rel == "claim":
        return kind == "claim" and _claim_id(body) == args[0]
    if rel == "reference":
        c, target, relation = args
        return kind == "claim" and _claim_id(body) == c \
            and {"claim": target, "relation": relation} in body.get("about", [])
    if rel in ("recorded", "author"):
        want = ev["record_time"] if rel == "recorded" else ev["writer"]
        return kind == "claim" and _claim_id(body) == args[0] and want == args[1]
    if rel == "asserted_by":
        return kind == "claim" and _claim_id(body) == args[0] and ev["writer"] == args[1]
    if rel == "verdict":
        v, c, w, res, method = args
        return kind == "verdict" and eid == v and body.get("claim") == c and ev["writer"] == w \
            and body.get("result") == res and body.get("method") == method
    if rel == "supersession":
        s, old, new, k, by = args
        return kind == "supersede" and eid == s and (body.get("old"), body.get("new"), body.get("kind")) == (old, new, k) \
            and ev["writer"] == by
    if rel == "evidence":
        return eid == args[0] and args[1] in body.get("evidence", [])
    if rel == "appeal":
        a, c, by = args
        return kind == "appeal" and eid == a and body.get("claim") == c and ev["writer"] == by
    if rel == "episode":
        e, w, medium = args
        return kind == "episode" and eid == e and ev["writer"] == w and body.get("medium") == medium
    if rel == "handoff":
        c, frame, w, t = args
        return kind == "handoff" and eid == c and body.get("frame") == frame and ev["writer"] == w \
            and ev["record_time"] == t
    if rel == "participant":
        return kind == "episode" and eid == args[0] and args[1] in body.get("participants", [])
    if rel == "writer":
        w, party, k = args
        return kind == "register" and body.get("writer") == w and body.get("party") == party and body.get("kind") == k
    if rel == "bound":
        e, label, w = args
        return kind == "episode" and eid == e and body.get("bindings", {}).get(label) == w
    if rel == "identity":
        w, key, val = args
        return kind == "register" and body.get("writer") == w \
            and key in ("name", "model", "operator", "surface", "session") and body.get(key) == val
    if rel == "capability":
        return kind == "register" and body.get("writer") == args[0] and args[1] in body.get("capabilities", [])
    if rel == "member":
        return kind == "register" and body.get("writer") == args[0] and args[1] in body.get("compartments", [])
    return False


def _cycles(nodes: dict) -> list[str]:
    """A proof must be well-founded: no fact may (indirectly) rest on itself."""
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    for root in nodes:
        if state.get(root):
            continue
        stack = [(root, iter(nodes[root].get("premises", [])))]
        state[root] = 1
        while stack:
            nid, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[nid] = 2
                stack.pop()
            elif nxt in nodes:
                if state.get(nxt) == 1:
                    return [f"{nxt}: the proof is circular"]
                if not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(nodes[nxt].get("premises", []))))
    return []


class _Disputes:
    """Disputes, rulings and appeals, folded from the log in order, exactly
    as section 4 of the spec defines them, without the core. Gives each
    claim's effective truth and defeat with the events that decide them, the
    open disputes, and how each dispute ended."""

    def __init__(self, events: list[dict]):
        self.first: dict[str, tuple[int, dict]] = {}  # claim -> its first event
        self.actions: dict[str, list] = {}  # claim -> [(index, kind, value, causes)]
        self.closer: dict[str, tuple[int, dict, str]] = {}  # dispute -> how it ended
        self.open: dict[str, set] = {}  # claim -> open contests and appeals
        open_contests: dict[str, dict] = {}  # id -> event
        contests_of: dict[str, set] = {}  # claim -> its open contests
        open_appeals: dict[str, dict] = {}

        def act(c, i, kind, value, causes):
            self.actions.setdefault(c, []).append((i, kind, value, frozenset(causes)))

        def end_contest(cid, i, closer, outcome):
            ct = open_contests.pop(cid)
            for c in (ct["body"]["old"], ct["body"]["new"]):
                contests_of.get(c, set()).discard(cid)
            self.closer[cid] = (i, closer, outcome)
            if outcome == "withdrawn":
                return
            old, new = ct["body"]["old"], ct["body"]["new"]
            for c in (old, new):
                wins = outcome == "both" or (outcome == "old") == (c == old)
                first = self.first.get(c, (None, {"event_id": None}))[1]["event_id"]
                if wins:
                    act(c, i, "truth", None, [cid, closer["event_id"], first])
                else:
                    act(c, i, "truth", "false", [cid, closer["event_id"]])
                    act(c, i, "defeat", True, [cid, closer["event_id"]])

        for i, ev in enumerate(events):
            kind, b, eid = ev["kind"], ev["body"], ev["event_id"]
            if kind == "claim":
                self.first.setdefault(_claim_id(b), (i, ev))
            elif kind == "supersede" and b.get("kind") == "contested":
                open_contests[eid] = ev
                for c in (b.get("old"), b.get("new")):
                    contests_of.setdefault(c, set()).add(eid)
            elif kind == "supersede" and b.get("kind") == "refuted":
                o = b.get("old")
                act(o, i, "truth", "false", [eid])
                act(o, i, "defeat", True, [eid])
                for cid in sorted(contests_of.get(o, ())):
                    end_contest(cid, i, ev, "new" if open_contests[cid]["body"]["old"] == o else "old")
            elif kind == "appeal":
                open_appeals[eid] = ev
            elif kind == "resolve":
                d = b.get("dispute")
                if d in open_contests:
                    end_contest(d, i, ev, b.get("outcome"))
                elif d in open_appeals:
                    ap = open_appeals.pop(d)
                    self.closer[d] = (i, ev, b.get("outcome"))
                    c = ap["body"].get("claim")
                    if b.get("outcome") == "reinstated":
                        first = self.first.get(c, (None, {"event_id": None}))[1]["event_id"]
                        act(c, i, "truth", None, [d, eid, first])
                        act(c, i, "defeat", False, [])
        for cid, ct in open_contests.items():
            for c in (ct["body"]["old"], ct["body"]["new"]):
                self.open.setdefault(c, set()).add(cid)
        for aid, ap in open_appeals.items():
            self.open.setdefault(ap["body"].get("claim"), set()).add(aid)

    def asserted(self, c):
        return self.first[c][1]["body"].get("truth") if c in self.first else None

    def truth(self, c):
        """(value, the cause sets that may be cited for it)."""
        acts = [a for a in self.actions.get(c, []) if a[1] == "truth"]
        if not acts:
            return self.asserted(c), [frozenset([self.first[c][1]["event_id"]])] if c in self.first else []
        last = acts[-1][0]
        final = [a for a in acts if a[0] == last]
        value = final[-1][2] if final[-1][2] is not None else self.asserted(c)
        return value, [a[3] for a in final if (a[2] if a[2] is not None else self.asserted(c)) == value]

    def defeat(self, c):
        """The cause sets that may be cited for `defeated(c)`, or [] if it is
        not defeated: the latest defeat, unless a later reinstatement undid it
        (a defeat wins over a reinstatement in the same event)."""
        acts = sorted([a for a in self.actions.get(c, []) if a[1] == "defeat"], key=lambda a: (a[0], a[2]))
        if not acts or not acts[-1][2]:
            return []
        last = acts[-1][0]
        return [a[3] for a in acts if a[0] == last and a[2]]


_GROUP = {"truth", "defeated", "resolution", "disputed"}
_IDENTITY = {"group", "instance_of", "speaks_as"}


class _Identity:
    """Who is who, worked out from the log alone (as the core does, section
    2a): each writer's registration and parent, and who each claim's speaker
    is, settled when the claim was first recorded."""

    def __init__(self, events: list[dict]):
        self.reg: dict[str, dict] = {}        # writer -> its register event
        self.parent: dict[str, str] = {}      # instance -> the writer that started it
        self.speaker: dict[str, tuple] = {}   # claim id -> (writer, {cited event ids})
        episodes: dict[str, dict] = {}
        for ev in events:
            kind, b = ev["kind"], ev["body"]
            if kind == "register":
                w = b.get("writer")
                if w in self.reg:
                    continue
                by = self.reg.get(ev["writer"])
                if by is not None and "owner" not in by["body"].get("capabilities", []):
                    self.parent[w] = ev["writer"]
                self.reg[w] = ev
            elif kind == "episode":
                episodes[ev["event_id"]] = ev
            elif kind == "claim":
                cid = _claim_id(b)
                if cid in self.speaker:
                    continue
                sp, src = b.get("speaker"), b.get("source_episode")
                who = None
                if src is not None:
                    ep = episodes.get(src)
                    if ep is not None and sp in ep["body"].get("bindings", {}):
                        who = (ep["body"]["bindings"][sp], {ev["event_id"], src})
                elif sp in self.reg:
                    who = (sp, {ev["event_id"], self.reg[sp]["event_id"]})
                else:
                    author = self.reg.get(ev["writer"])
                    if author is not None and author["body"].get("name") == sp:
                        who = (ev["writer"], {ev["event_id"], author["event_id"]})
                self.speaker[cid] = who

    def group(self, w: str) -> tuple[str, set]:
        cited = {self.reg[w]["event_id"]}
        while w in self.parent:
            w = self.parent[w]
            cited.add(self.reg[w]["event_id"])
        b = self.reg[w]["body"]
        k, m = b.get("kind"), b.get("model")
        g = f"{k}:{m.lower()}" if k in ("model", "tool") and m is not None else f"{k}:{w}"
        return g, cited

    def establishes(self, fact: list, cited: set) -> bool:
        rel, args = fact[0], fact[1:]
        if rel == "group":
            w, g = args
            return w in self.reg and self.group(w) == (g, cited)
        if rel == "instance_of":
            w, p = args
            return self.parent.get(w) == p and cited == {self.reg[w]["event_id"], self.reg[p]["event_id"]}
        if rel == "speaks_as":
            c, w = args
            who = self.speaker.get(c)
            return who is not None and who[0] == w and who[1] == cited
        return False


def _visible(events: list[dict], compartments: set) -> list[bool]:
    """Which events a reader with these compartments can see, worked out
    from the log alone (as the core does): a claim's, an episode's or a
    verdict's claim's compartment; a dispute's or appeal's claim's; a
    ruling's dispute's; a registration if the writer shares a compartment."""
    claim_c: dict[str, str] = {}
    item_c: dict[str, str] = {}  # episode, supersession or appeal id -> compartment
    out = []
    for ev in events:
        kind, b, eid = ev["kind"], ev["body"], ev["event_id"]
        if kind == "claim":
            c = claim_c.setdefault(_claim_id(b), b.get("compartment"))
        elif kind in ("episode", "handoff"):
            c = item_c.setdefault(eid, b.get("compartment"))
        elif kind == "verdict":
            c = claim_c.get(b.get("claim"))
        elif kind == "supersede":
            c = item_c.setdefault(eid, claim_c.get(b.get("old")))
        elif kind == "appeal":
            c = item_c.setdefault(eid, claim_c.get(b.get("claim")))
        elif kind == "resolve":
            c = item_c.get(b.get("dispute"))
        elif kind == "register":
            out.append(bool(set(b.get("compartments", [])) & compartments))
            continue
        else:
            out.append(True)
            continue
        out.append(c in compartments)
    return out


def _establishes(fact: list, cited: set, d: _Disputes) -> bool:
    rel = fact[0]
    if rel == "truth":
        value, options = d.truth(fact[1])
        return value == fact[2] and cited in options
    if rel == "defeated":
        return cited in d.defeat(fact[1])
    if rel == "disputed":
        return len(cited) == 1 and cited <= d.open.get(fact[1], set())
    if rel == "resolution":
        dispute, outcome, r = fact[1], fact[2], fact[3]
        end = d.closer.get(dispute)
        return end is not None and end[1]["event_id"] == r and end[2] == outcome and cited == {dispute, r}
    return False


def check(result: dict, program: str, views: str = "", log: Optional[str | Path] = None,
          require_hashes: bool = False) -> list[str]:
    """Check every proof in a query result. Returns the problems found (an
    empty list means every proof checks out). `program` is the query that
    was run, `views` the built-in views (gnomon.VIEWS) when querying a
    memory, and `log` the log file for answers that cite events."""
    problems: list[str] = []
    try:
        rules, query = parse(views + "\n" + program)
    except ParseError as e:
        return [f"program does not parse: {e}"]
    if query is None:
        return ["program has no query"]
    allowed = {rule_text(r): r for r in rules}
    q = query_rule(query)
    allowed[rule_text(q)] = q

    ordered: list = []
    if log is not None:
        ordered, problems = _load_log(log)
    # The reader's view ends at `through`: later events are not part of it.
    through = result.get("through")
    as_of = result.get("as_of")
    view = result.get("view")
    seen: Optional[list] = None
    # A proof that cites the log must say whose view it is, where that view
    # ends and which moment it describes: without them it could pass off a
    # stale view as a later one. (The core always gives all three.)
    if log is not None and any("sources" in n for n in result.get("nodes", {}).values()):
        missing = [k for k, v in (("view", view), ("through", through), ("as_of", as_of)) if not v]
        if missing:
            problems.append(f"the result does not state its {', '.join(missing)}")
    if view is not None and log is not None:
        comps = set(view.get("compartments", []))
        regs = [ev for ev in ordered if ev["kind"] == "register" and ev["body"].get("writer") == view.get("reader")]
        if not regs or set(regs[0]["body"].get("compartments", [])) != comps:
            problems.append(f"the view's compartments are not those of reader {view.get('reader')!r}")
        seen = _visible(ordered, comps)
    if through:
        ends = [i for i, ev in enumerate(ordered)
                if ev["event_id"] == through.get("event") and ev["hash"] == through.get("hash")]
        if not ends:
            problems.append(f"the view's last event {through.get('event')} is not in the log with that hash")
        elif as_of is None or ordered[ends[0]]["record_time"] > as_of:
            problems.append("the result does not say which moment its view describes")
        elif seen is not None:
            if not seen[ends[0]]:
                problems.append("the view's last event is not one the reader can see")
            # The moment the result describes takes in every event the reader
            # can see up to as_of: none may come after the view's last event.
            for i in range(ends[0] + 1, len(ordered)):
                if ordered[i]["record_time"] > as_of:
                    break
                if seen[i]:
                    problems.append(f"event {ordered[i]['event_id']} is in the moment the result describes "
                                    f"but left out of its view")
                    break
        if seen is not None:
            hidden = {ev["event_id"] for ev, ok in zip(ordered, seen) if not ok}
        ordered = ordered[: ends[0] + 1] if ends else []
    elif seen is not None:
        hidden = {ev["event_id"] for ev, ok in zip(ordered, seen) if not ok}
    elif result.get("nodes") and log is not None:
        ordered = []
    if seen is None:
        hidden = set()
    events = {ev["event_id"]: ev for ev in ordered if ev["event_id"] not in hidden}
    disputes: Optional[_Disputes] = None
    identity: Optional[_Identity] = None

    nodes = result.get("nodes", {})
    table = result.get("rules", {})
    problems += _cycles(nodes)
    for nid, node in nodes.items():
        fact = node["fact"]
        if "rule" in node:
            text = table.get(node["rule"], {}).get("text")
            if text not in allowed:
                problems.append(f"{nid}: rule is not in the program: {text}")
                continue
            missing = [p for p in node["premises"] if p not in nodes]
            if missing:
                problems.append(f"{nid}: premises {missing} are missing")
                continue
            prem = [nodes[p]["fact"] for p in node["premises"]]
            if not _instance_ok(allowed[text], fact, prem, node.get("absent", [])):
                problems.append(f"{nid}: {fact} does not follow from its rule and premises")
        else:
            sources = node.get("sources") or []
            if not sources:
                problems.append(f"{nid}: base fact {fact} cites no events")
            cited = []
            for s in sources:
                ev = events.get(s.get("event"))
                if ev is None or ev["hash"] != s.get("hash") or ev["kind"] != s.get("kind"):
                    problems.append(f"{nid}: cited event {s.get('event')} is not in the log with that hash")
                elif as_of is not None and ev["record_time"] > as_of:
                    problems.append(f"{nid}: cited event {s.get('event')} is later than as_of")
                elif fact[0] not in _GROUP | _IDENTITY and not _event_says(fact, ev):
                    problems.append(f"{nid}: event {s.get('event')} does not state {fact}")
                else:
                    cited.append(ev)
            if fact[0] in _IDENTITY and cited and len(cited) == len(sources):
                if identity is None:
                    identity = _Identity(ordered)
                if not identity.establishes(fact, {ev["event_id"] for ev in cited}):
                    problems.append(f"{nid}: the log does not establish {fact} from {[s.get('event') for s in sources]}")
            if fact[0] in _GROUP and cited and len(cited) == len(sources):
                if disputes is None:
                    disputes = _Disputes(ordered)
                if not _establishes(fact, {ev["event_id"] for ev in cited}, disputes):
                    problems.append(f"{nid}: the log does not establish {fact} from {[s.get('event') for s in sources]}")

    problems.extend(_check_hashes(result, require_hashes))
    for i, ans in enumerate(result.get("answers", [])):
        node = nodes.get(ans["proof"])
        if node is None or node["fact"] != ["?"] + ans["values"]:
            problems.append(f"answer {i}: its proof does not prove it")
        elif "proof_hash" in ans and node.get("hash") != ans["proof_hash"]:
            problems.append(f"answer {i}: its proof_hash is not its proof's hash")
    return problems


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_hashes(result: dict, required: bool = False) -> list[str]:
    """Rules are named by the hash of their text and proof nodes by the
    hash of what they say and of what they rest on (a Merkle DAG). Recompute
    every hash; a proof whose hashes are present must match them all."""
    problems: list[str] = []
    rules, nodes = result.get("rules", {}), result.get("nodes", {})
    marked = ["hash" in x for x in list(rules.values()) + list(nodes.values())]
    marked += ["proof_hash" in a for a in result.get("answers", [])]
    if (any(marked) or required) and not all(marked):
        problems.append("the proof is not fully content-addressed" if required and not any(marked)
                        else "some of the proof is content-addressed and some is not")
    for rid, r in rules.items():
        if "hash" in r and r["hash"] != _sha(r.get("text", "")):
            problems.append(f"rule {rid}: its hash is not the hash of its text")
    memo: dict[str, Optional[str]] = {}
    opened: set[str] = set()  # expanded, not yet hashed: meeting one again is a cycle
    for start in nodes:
        stack = [(start, False)]
        while stack:
            nid, ready = stack.pop()
            if nid in memo or nid not in nodes:
                continue
            n = nodes[nid]
            if "rule" in n and not ready:
                if nid in opened:
                    memo[nid] = None  # circular: reported by the main check
                    continue
                opened.add(nid)
                stack.append((nid, True))
                stack.extend((p, False) for p in n.get("premises", []) if p not in memo)
                continue
            if "rule" in n:
                rh = rules.get(n["rule"], {}).get("hash")
                ph = [memo.get(p) for p in n.get("premises", [])]
                if rh is None or None in ph:
                    memo[nid] = None
                    continue
                body = {"fact": n["fact"], "rule": rh, "premises": ph, "absent": n.get("absent", [])}
            else:
                body = {"fact": n["fact"], "sources": [x.get("hash") for x in n.get("sources", [])]}
            h = _sha(canonical(body))
            if "hash" in n and n["hash"] != h:
                problems.append(f"{nid}: its hash does not match what it says and rests on")
            memo[nid] = h
    return problems


# --------------------------------------------------------------- explaining

def explain(result: dict, index: int = 0, max_nodes: int = 200) -> str:
    """A proof as indented text: each fact, the rule that gives it, and the
    events at the leaves."""
    nodes = result["nodes"]
    ans = result["answers"][index]
    lines: list[str] = []
    shown: set[str] = set()

    def fmt(fact):
        if fact[0] == "?":
            fact = ["answer"] + fact[1:]
        return fact[0] + "(" + ", ".join(_val_text(v) for v in fact[1:]) + ")" if len(fact) > 1 else fact[0]

    def walk(nid, depth):
        if len(lines) >= max_nodes:
            return
        n = nodes[nid]
        pad = "  " * depth
        if nid in shown:
            lines.append(f"{pad}{fmt(n['fact'])}  (shown above)")
            return
        shown.add(nid)
        if "rule" in n:
            rule = result.get("rules", {}).get(n["rule"], {})
            lines.append(f"{pad}{fmt(n['fact'])}  by {rule.get('origin', '?')}: {rule.get('text', '?')}")
            for a in n.get("absent", []):
                shown_a = a[0] + "(" + ", ".join("_" if v is None else _val_text(v) for v in a[1:]) + ")"
                lines.append(f"{pad}  absent: {shown_a}")
            for p in n["premises"]:
                walk(p, depth + 1)
        else:
            refs = ", ".join(f"{s['event']} ({s['kind']}, {s['hash'][:12]}…)" for s in n.get("sources", []))
            lines.append(f"{pad}{fmt(n['fact'])}  from {refs}")

    walk(ans["proof"], 0)
    return "\n".join(lines)
