"""Gnomon: lossless, verifiable memory for people and AI models.

The Rust core owns every rule. This package is a thin, typed doorway to it:
nothing written here can bypass the write gate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import _native

__all__ = [
    "GateError",
    "Memory",
    "VIEWS",
    "datalog",
    "canonicalize",
    "canonicalize_text",
    "claim",
    "claim_id",
    "now",
    "sha256_hex",
]
__version__ = "0.1.0"


class GateError(Exception):
    """The core refused an operation. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _unwrap(result: dict) -> dict:
    if not result.get("ok"):
        raise GateError(result.get("code", "E_UNKNOWN"), result.get("message", ""))
    return result


def _dumps(obj: Any) -> str:
    # allow_nan=False: NaN/Infinity never even reach the core.
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)


def datalog(program: str) -> dict:
    """Run a self-contained Datalog program (facts written as clauses) and
    return its answers with proofs. See gnomon.proofs to check or explain
    them."""
    return _unwrap(_native.call("gn_datalog", program))["result"]


def now() -> str:
    """Current UTC time in the exact form the log requires."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: bytes) -> str:
    return _unwrap(_native.call("gn_sha256_hex", data, len(data)))["hex"]


def canonicalize(value: Any) -> str:
    """RFC 8785 canonical JSON of a Python value, as computed by the core."""
    return canonicalize_text(_dumps(value))


def canonicalize_text(text: str) -> str:
    """RFC 8785 canonical form of JSON text, as computed by the core."""
    return _unwrap(_native.call("gn_canonicalize", text))["canonical"]


def claim_id(body: dict) -> str:
    return _unwrap(_native.call("gn_claim_id", _dumps(body)))["id"]


def claim(
    statement: str,
    *,
    speaker: str,
    frame: str,
    compartment: str,
    stance: str = "asserted",
    truth: str = "unknown",
    confidence: int = 5000,
    origin: str = "derived",
    statement_kind: str = "describe",
    **optional: Any,
) -> dict:
    """Build a claim body. Optional fields: validity_domain, valid_from,
    valid_to, source_episode, intended, delivered, observed."""
    body = {
        "statement": statement,
        "statement_kind": statement_kind,
        "origin": origin,
        "stance": stance,
        "speaker": speaker,
        "truth": truth,
        "confidence": confidence,
        "frame": frame,
        "compartment": compartment,
    }
    body.update({k: v for k, v in optional.items() if v is not None})
    return body


class Memory:
    """A log file and everything derived from it."""

    def __init__(self, path: str | Path):
        self.path = str(path)

    # -- writing ------------------------------------------------------
    def append(self, writer: str, kind: str, body: dict, record_time: Optional[str] = None) -> dict:
        draft = {
            "writer": writer,
            "record_time": record_time or now(),
            "kind": kind,
            "body": body,
        }
        return _unwrap(_native.call("gn_append", self.path, _dumps(draft)))["event"]

    def register(
        self,
        by: str,
        writer: str,
        *,
        party: str,
        kind: str,
        capabilities: Iterable[str],
        compartments: Iterable[str],
        record_time: Optional[str] = None,
        name: Optional[str] = None,
        model: Optional[str] = None,
        operator: Optional[str] = None,
        surface: Optional[str] = None,
        session: Optional[str] = None,
    ) -> dict:
        """Register a writer: one person, or one running instance of a model
        or tool (each session or agent is its own writer). A model names the
        model it runs, as its runtime reports it, and its `operator`, the
        registered human it acts for; a tool names its operator."""
        body = {
            "writer": writer,
            "party": party,
            "kind": kind,
            "capabilities": list(capabilities),
            "compartments": list(compartments),
            **{k: v for k, v in (("name", name), ("model", model), ("operator", operator),
                                 ("surface", surface), ("session", session)) if v is not None},
        }
        # A new memory starts at the current protocol version.
        if by == writer and (not Path(self.path).exists() or Path(self.path).stat().st_size == 0):
            body["schema_version"] = SCHEMA_VERSION
        return self.append(by, "register", body, record_time)

    def episode(self, writer: str, content: str, *, compartment: str, medium: str = "chat",
                participants: Iterable[str] = (), record_time: Optional[str] = None,
                bindings: Optional[dict] = None) -> dict:
        """Record an episode. `bindings` says which registered writer each
        participant's name refers to ({"Claude": "claude-c1"})."""
        body = {
            "compartment": compartment,
            "content": content,
            "medium": medium,
            "participants": list(participants),
        }
        if bindings:
            body["bindings"] = dict(bindings)
        return self.append(writer, "episode", body, record_time)

    def remember(self, writer: str, body: dict, record_time: Optional[str] = None) -> str:
        """Record a claim; returns its content address."""
        self.append(writer, "claim", body, record_time)
        return claim_id(body)

    # ------------------------------------------------------------ forum (M7)
    def ask(self, writer: str, question: str, *, frame: str, compartment: str, speaker: Optional[str] = None,
            about: Optional[list] = None, record_time: Optional[str] = None) -> str:
        """Post a question; returns its id. `about`: [(claim id, relation)]."""
        body = claim(question, speaker=speaker or writer, frame=frame, compartment=compartment,
                     stance="asked", truth="unknown", origin="manual", statement_kind="ask",
                     about=_about(about))
        return self.remember(writer, body, record_time)

    def respond(self, writer: str, to: str, relation: str, statement: str, *, frame: str, compartment: str,
                truth: str = "unknown", confidence: int = 5000, origin: str = "manual",
                speaker: Optional[str] = None, record_time: Optional[str] = None) -> str:
        """Answer, critique, reply to or cite claim `to` (relation: answers,
        critiques, replies, cites); returns the new claim's id. A response
        is a claim of its own, judged like any other; it changes no truth."""
        body = claim(statement, speaker=speaker or writer, frame=frame, compartment=compartment,
                     truth=truth, confidence=confidence, origin=origin, about=_about([(to, relation)]))
        return self.remember(writer, body, record_time)

    def discussion(self, reader: str, claim_id: str, as_of: Optional[str] = None, limit: int = 500) -> dict:
        """Everything on record about a claim, as `reader` sees it: the claim
        and every claim that answers, critiques, replies to or cites it, or
        any of those, in the order they were recorded. Each post appears
        once, with its standing (truth, verdicts, disputes, supersessions)
        and the posts in the thread it refers to. At most `limit` posts;
        `truncated` says if there were more."""
        st = self.state(as_of=as_of, compartments=self._compartments(reader))["state"]
        claims = st["claims"]
        if claim_id not in claims:
            raise GateError("E_REF", f"claim '{claim_id}' does not exist or is not accessible")

        def order(cid: str) -> int:
            return claims[cid]["order"]

        children: dict[str, set] = {}
        for cid, c in claims.items():
            for r in c["body"].get("about", []):
                children.setdefault(r["claim"], set()).add(cid)
        # Breadth first from the claim, each post once, then by record order.
        seen, frontier, truncated = {claim_id}, [claim_id], False
        while frontier:
            nxt = []
            for cid in frontier:
                for ch in sorted(children.get(cid, ()), key=order):
                    if ch not in seen:
                        if len(seen) >= limit:
                            truncated = True
                            break
                        seen.add(ch)
                        nxt.append(ch)
            frontier = nxt
        posts = []
        for cid in sorted(seen, key=order):
            c = claims[cid]
            posts.append({
                "claim": cid, "statement": c["body"]["statement"], "speaker": c["body"]["speaker"],
                "author": c["author"], "time": c["record_time"], "stance": c["body"]["stance"],
                "truth": c["effective_truth"], "disputed": c["disputed"], "supports": c["supports"],
                "refutes": c["refutes"], "abstains": c["abstains"], "superseded_by": c["superseded_by"],
                "refers_to": [r for r in c["body"].get("about", []) if r["claim"] in seen],
            })
        return {"root": claim_id, "posts": posts, "truncated": truncated}

    # ------------------------------------------------------- continuity (M8)
    def handoff(self, writer: str, *, frame: str, summary: str, compartment: str,
                   goal: Optional[str] = None, done: Iterable[str] = (), next: Iterable[str] = (),
                   open: Iterable[str] = (), refs: Iterable[str] = (), artifacts: Iterable[dict] = (),
                   follows: Optional[str] = None, lease: Optional[str] = None,
                   record_time: Optional[str] = None) -> str:
        """Record where a piece of work (`frame`) stands, so that anyone can
        pick it up: a summary, the goal, what is done, what comes next, what
        is open, the claims and episodes it rests on (`refs`), and artifacts
        ({"uri", "note", "sha256"}). In a leased frame (protocol version 6)
        the holder names its `lease`. Returns the handoff's event id."""
        body: dict[str, Any] = {"compartment": compartment, "frame": frame, "summary": summary}
        for key, val in (("done", list(done)), ("next", list(next)), ("open", list(open)),
                         ("refs", list(refs)), ("artifacts", [dict(x) for x in artifacts])):
            if val:
                body[key] = val
        if goal:
            body["goal"] = goal
        if follows:
            body["follows"] = follows
        if lease:
            body["lease"] = lease
        return self.append(writer, "handoff", body, record_time)["event_id"]

    def packet(self, reader: str, resident: str, frame: Optional[str] = None, as_of: Optional[str] = None,
               limit: int = 20, turns: int = 12) -> dict:
        """A resident's start packet (protocol version 4, issue #55), built
        only from the frames the gate guards: who it is (`self/<resident>`),
        its standing instructions (`standing/<resident>`), and where its own
        work stands (resume of `run/<resident>/<frame>`; default: its most
        recently active run frame).

        Each of the first two is one page: the owner's latest claim in its
        frame is the whole current page, and the owner's earlier ones are
        history (`earlier` counts them). From version 4 only the owner can
        write those frames, and `guarded` is True. Claims there by anyone
        else (possible only before version 4) never count as the page;
        `others` counts them so they can be flagged."""
        st = self.state(as_of=as_of, compartments=self._compartments(reader))["state"]
        guarded = st.get("schema_version", 1) >= 4

        owners = {w for w, i in st["writers"].items() if "owner" in i["capabilities"]}

        def page(family):
            # Only an owner's page counts. Others can exist only from before
            # version 4 (an upgraded log); they are counted, never shown as binding.
            f = f"{family}/{resident}"
            rows = sorted((c["order"], cid, c["first_event"].rsplit("/", 1)[0], c)
                          for cid, c in st["claims"].items() if c["body"].get("frame") == f)
            mine = [r for r in rows if r[2] in owners]
            others = len(rows) - len(mine)
            if not mine:
                return {"claim": None, "others": others} if others else None
            _, cid, writer, c = mine[-1]
            return {"claim": cid, "writer": writer, "time": c["record_time"], "text": c["body"]["statement"],
                    "earlier": len(mine) - 1, "others": others}

        mine = f"run/{resident}"
        if frame is not None:
            run = f"{mine}/{frame}"
        else:
            runs = [f["frame"] for f in self.frames(reader, as_of=as_of)
                    if f["frame"] == mine or f["frame"].startswith(mine + "/")]
            run = runs[0] if runs else None
        work = self.resume(reader, frame=run, as_of=as_of, limit=limit, turns=turns) if run else None
        return {"resident": resident, "guarded": guarded, "self": page("self"), "standing": page("standing"),
                "frame": run, "work": work}

    def frames(self, reader: str, as_of: Optional[str] = None) -> list:
        """Every piece of work (frame) the reader can see, most recently
        active first: its last activity, how many claims and handoffs it
        has, and its latest handoff."""
        return _unwrap(_native.call("gn_frames", self.path, as_of, reader))["frames"]

    def resume(self, reader: str, frame: Optional[str] = None, as_of: Optional[str] = None,
               limit: int = 20, turns: int = 12) -> dict:
        """Pick up a piece of work exactly where it was left, whoever left
        it: the latest handoff of `frame` (default: the most recently active
        frame), the earlier ones, how everything it rests on stands now (a
        claim it relied on may have been refuted since), and what happened
        after it: new claims, decisions, open questions and critiques
        nobody has answered. Everything with its source. Long texts are cut
        to a budget (marked "cut"), and lists to the `limit` most recent."""
        limit = max(1, min(int(limit), 200))
        turns = max(0, min(int(turns), 200))
        asked = frame
        if frame is not None and not _plain_text(frame, 2000):
            frame = "\x00"  # no frame can have that name: an empty resume, below
        # Only the part of the state this needs, worked out natively.
        got = _unwrap(_native.call("gn_resume_state", self.path, as_of, reader,
                                   None if frame is None else frame, turns))
        st = got["state"]
        empty = {"frame": frame, "handoff": None, "earlier_handoffs": [], "rests_on": [],
                 "since": {"claims": [], "more": 0}, "decisions": [], "open_questions": [],
                 "unanswered_critiques": [], "recent_turns": self._recent_turns(st, None, turns)}
        if frame is None:
            frame = got["frame"]
            if frame is None:
                return dict(empty, frames=[])
        if frame == "\x00":
            return dict(empty, frame=asked)
        hs = sorted(((hid, h) for hid, h in st["handoffs"].items() if h["body"]["frame"] == frame),
                    key=lambda kv: kv[1]["order"])
        latest = hs[-1] if hs else None
        since = latest[1]["order"] if latest else -1
        claims = st["claims"]

        def standing(cid: str) -> dict:
            c = claims[cid]
            return {"claim": cid, "statement": _cut(c["body"]["statement"], 1000),
                    "speaker": c["body"]["speaker"], "author": c["author"], "time": c["record_time"],
                    "stance": c["body"]["stance"], "truth": c["effective_truth"], "disputed": c["disputed"],
                    "superseded_by": c["superseded_by"], "supports": len(c["supports"]),
                    "refutes": len(c["refutes"])}
        rests_on = []
        if latest:
            for rid in latest[1]["body"].get("refs", []):
                if rid in claims:
                    rests_on.append(standing(rid))
                elif rid in st["episodes"]:
                    e = st["episodes"][rid]
                    rests_on.append({"episode": rid, "writer": e["writer"], "time": e["record_time"],
                                     "participants": e["body"].get("participants", [])})
                elif rid in st["handoffs"]:
                    rests_on.append({"handoff": rid, "time": st["handoffs"][rid]["record_time"]})
        after = sorted((cid for cid, c in claims.items() if c["body"]["frame"] == frame and c["order"] > since),
                       key=lambda cid: claims[cid]["order"])
        fq = json.dumps(frame, ensure_ascii=False)

        # One query for all three (the memory is loaded once), answers only.
        program = (f'r("open", Q, "") :- open_question(Q), frame(Q, {fq}).\n'
                   f'r("decision", C, "") :- decision(C), frame(C, {fq}).\n'
                   f'r("critique", K, C) :- unanswered_critique(K, C), frame(C, {fq}).\n'
                   "?- r(T, A, B).")
        rows: dict[str, list] = {"open": [], "decision": [], "critique": []}
        for a in self.query(reader, program, as_of, proofs=0)["answers"]:
            t_, x, y = a["values"]
            if x in claims:
                rows[t_].append((x, y))
        for k in rows:
            rows[k].sort(key=lambda v: claims[v[0]]["order"])
        open_qs = [standing(x) for x, _ in rows["open"]]
        decisions = [standing(x) for x, _ in rows["decision"]]
        critiques = [{"critique": standing(k), "of": c} for k, c in rows["critique"]]
        h = None
        if latest:
            b = latest[1]["body"]
            h = {"handoff": latest[0], "writer": latest[1]["writer"], "time": latest[1]["record_time"],
                 "summary": _cut(b["summary"], 4000)}
            for key in ("goal", "follows", "refs"):
                if key in b:
                    h[key] = b[key]
            for key in ("done", "next", "open"):
                if key in b:
                    h[key] = [_cut(x, 500) for x in b[key]]
            if "artifacts" in b:
                h["artifacts"] = [{k: (_cut(v, 500) if k != "sha256" else v) for k, v in x.items()}
                                  for x in b["artifacts"]]
        return {
            "frame": frame,
            "handoff": h,
            "earlier_handoffs": [{"handoff": hid, "writer": x["writer"], "time": x["record_time"],
                                  "summary": _cut(x["body"]["summary"], 300)} for hid, x in hs[:-1]][-10:],
            "rests_on": rests_on,
            "since": {"claims": [standing(cid) for cid in after[-limit:]], "more": max(0, len(after) - limit)},
            "decisions": decisions[-limit:],
            "open_questions": open_qs[-limit:],
            "unanswered_critiques": critiques[-limit:],
            "recent_turns": self._recent_turns(st, latest[1]["writer"] if latest else None, turns),
        }

    @staticmethod
    def _recent_turns(st: dict, writer: Optional[str], n: int) -> list:
        """The last `n` turns of recorded conversation: those recorded by the
        writer that left the latest handoff (that session's own words and
        its user's), or, without one, by the writer of the most recent
        conversation on record. One session's conversation, never several
        interleaved."""
        eps = sorted(st["episodes"].items(), key=lambda kv: kv[1]["order"])
        if writer is None and eps:
            writer = eps[-1][1]["writer"]  # one conversation: the latest one's writer's, never interleaved
        eps = [kv for kv in eps if kv[1]["writer"] == writer]
        out: list = []
        for eid, e in reversed(eps):
            body = e["body"]
            content, turns = body.get("content", ""), body.get("turns", [])
            binds = body.get("bindings", {})
            for i in range(len(turns) - 1, -1, -1):
                tr = turns[i]
                text = content[tr["start"]:tr["end"]]  # offsets are code points, as Python's
                out.append({"episode": eid, "turn": i, "speaker": tr["speaker"],
                            "speaker_is": binds.get(tr["speaker"]), "time": e["record_time"],
                            "text": _cut(text, 600), "hidden_chars": e["hidden_chars"]})
                if len(out) >= n:
                    return list(reversed(out))
        return list(reversed(out))

    def _compartments(self, reader: str) -> list:
        writers = self.writers()
        if reader not in writers:
            raise GateError("E_UNKNOWN_WRITER", f"reader '{reader}' is not registered")
        return writers[reader]["compartments"]

    def verdict(self, writer: str, claim: str, result: str, method: str,
                note: Optional[str] = None, record_time: Optional[str] = None) -> dict:
        body = {"claim": claim, "result": result, "method": method}
        if note:
            body["note"] = note
        return self.append(writer, "verdict", body, record_time)

    def lease(self, writer: str, target: str, frame: str, expires: str, reason: Optional[str] = None,
              record_time: Optional[str] = None) -> str:
        """Give `target` a hold on `frame` until `expires` (protocol version 6,
        issue #54): only it (and its instances, and the owner) may write
        there meanwhile, naming the lease. Needs `schedule` (or owner); never
        to oneself; one live lease per frame. Returns the lease's event id,
        which is also its fencing token."""
        body = {"writer": target, "frame": frame, "expires": expires}
        if reason:
            body["reason"] = reason
        return self.append(writer, "lease", body, record_time)["event_id"]

    def release(self, writer: str, lease: str, reason: Optional[str] = None,
                record_time: Optional[str] = None) -> dict:
        """End a lease early: its holder, whoever granted it, or the owner."""
        body = {"lease": lease}
        if reason:
            body["reason"] = reason
        return self.append(writer, "release", body, record_time)

    def revoke(self, writer: str, target: str, compartments: Iterable[str], reason: str,
               record_time: Optional[str] = None) -> dict:
        """End `target`'s access to `compartments` from now on (protocol
        version 5, issue #49): the owner only; the target's instances lose
        them too; what it wrote before stays on record."""
        body = {"writer": target, "compartments": list(compartments), "reason": reason}
        return self.append(writer, "revoke", body, record_time)

    def supersede(self, writer: str, old: str, new: str, kind: str, evidence: Iterable[str],
                  reason: str, record_time: Optional[str] = None) -> dict:
        body = {"old": old, "new": new, "kind": kind, "evidence": list(evidence), "reason": reason}
        return self.append(writer, "supersede", body, record_time)

    def resolve(self, writer: str, dispute: str, outcome: str, reason: str,
                record_time: Optional[str] = None) -> dict:
        body = {"dispute": dispute, "outcome": outcome, "reason": reason}
        return self.append(writer, "resolve", body, record_time)

    def appeal(self, writer: str, claim: str, evidence: Iterable[str], reason: str,
               record_time: Optional[str] = None) -> dict:
        """Appeal a refuted or defeated claim, citing supporting verdicts from
        parties with no stake in it. A neutral party decides it with
        resolve(..., outcome="reinstated" | "upheld")."""
        body = {"claim": claim, "evidence": list(evidence), "reason": reason}
        return self.append(writer, "appeal", body, record_time)

    # -- reading ------------------------------------------------------
    def verify(self, budget: Optional[int] = None) -> dict:
        return _unwrap(_native.call("gn_verify", self.path, -1 if budget is None else budget))

    def state(self, as_of: Optional[str] = None, compartments: Optional[Iterable[str]] = None) -> dict:
        comps = None if compartments is None else _dumps(list(compartments))
        return _unwrap(_native.call("gn_state", self.path, as_of, comps))

    # ------------------------------------------------------- organisations
    def position(self, writer: str, org: str, title: str, *, compartment: str, duties: str,
                 reports_to: Optional[str] = None, record_time: Optional[str] = None) -> dict:
        """Define (or redefine) a position in an organisation: its title,
        whom it reports to (None: the organisation's owners) and its duties.
        Allowed to owners and governors, and to holders of a position it
        reports to, directly or through others."""
        body = {"compartment": compartment, "org": org, "title": title, "duties": duties}
        if reports_to is not None:
            body["reports_to"] = reports_to
        return self.append(writer, "position", body, record_time)

    def appoint(self, writer: str, org: str, title: str, holder: str, *, compartment: str,
                record_time: Optional[str] = None) -> str:
        """Appoint `holder` to a position; returns the appointment's id."""
        body = {"compartment": compartment, "org": org, "title": title, "holder": holder}
        return self.append(writer, "appoint", body, record_time)["event_id"]

    def dismiss(self, writer: str, appointment: str, reason: str, record_time: Optional[str] = None) -> dict:
        """End an appointment (or, by its holder, resign)."""
        return self.append(writer, "dismiss", {"appointment": appointment, "reason": reason}, record_time)

    def writers(self, compartments: Optional[Iterable[str]] = None) -> dict:
        """The registered writers, as `state()` shows them (all, or those in
        `compartments`), without rendering the rest of the state."""
        comps = None if compartments is None else _dumps(list(compartments))
        return _unwrap(_native.call("gn_writers", self.path, comps))["writers"]

    def select(self, compartments: Optional[Iterable[str]] = None, claims: Iterable[str] = (),
               episodes: Iterable[str] = (), handoffs: Iterable[str] = ()) -> dict:
        """Part of the state, as `state()` shows it: the writers and only the
        named claims, episodes and handoffs (those that exist and are in
        `compartments`; all compartments if None)."""
        comps = None if compartments is None else _dumps(list(compartments))
        sel = _dumps({"claims": list(claims), "episodes": list(episodes), "handoffs": list(handoffs)})
        return _unwrap(_native.call("gn_select", self.path, comps, sel))["state"]

    def query(self, reader: str, program: str, as_of: Optional[str] = None,
              proofs: Optional[int] = None) -> dict:
        """Answer a Datalog query over this memory as `reader` sees it (only
        its compartments), as of `as_of` (latest if None).

        The result has `variables`, `answers` (each with `values` and the id
        of its `proof`), and the proof `nodes`: every derived fact names its
        rule and premises, every base fact the log events it comes from.
        Check them with gnomon.proofs.check, print them with
        gnomon.proofs.explain. Base relations and built-in views are listed
        in the spec (and in gnomon.VIEWS). `proofs`: give proofs for the
        first n answers only (the rest carry a null proof; `proved` counts
        them): proofs are most of a result's cost, and 0 makes a query that
        only needs answers fast."""
        if proofs is None:
            return _unwrap(_native.call("gn_query", self.path, as_of, reader, program))["result"]
        opts = _dumps({"proofs": int(proofs)})
        return _unwrap(_native.call("gn_query_opts", self.path, as_of, reader, program, opts))["result"]

    def recall(self, reader: str, query: str, *, k: int = 10, kinds: Optional[Iterable[str]] = None,
               speaker: Optional[str] = None, after: Optional[str] = None, before: Optional[str] = None,
               now: Optional[str] = None, context: int = 0, as_of: Optional[str] = None,
               tuning: Optional[dict] = None) -> dict:
        """Find what was said, ranked, in plain language: the turns of
        recorded conversations and documents, and claims, as `reader` sees
        them. Each hit gives its words, speaker, time, exact position and
        source event, the terms that matched, and every relative date in it
        resolved by the calendar ("yesterday" -> 2023-05-07). `now` is the
        moment of the question, so "last weekend" in it can be resolved too.
        `tuning` switches ranking techniques on or off for measurement:
        joins, speaker_prior, propagate (on by default) and prf (off)."""
        req: dict[str, Any] = {"query": query, "k": k, "context": context}
        if kinds is not None:
            req["kinds"] = list(kinds)
        for key, val in (("speaker", speaker), ("after", after), ("before", before), ("now", now),
                         ("tuning", tuning)):
            if val is not None:
                req[key] = val
        return _unwrap(_native.call("gn_recall", self.path, as_of, reader, _dumps(req)))["result"]

    def checkpoint(self) -> tuple[int, str]:
        r = self.verify()
        return r["count"], r["head"]

    def check_checkpoint(self, count: int, head: str) -> None:
        _unwrap(_native.call("gn_checkpoint", self.path, count, head))


#: The protocol version new memories start at (see the spec, section 2a).
SCHEMA_VERSION = 7


def _cut(text: str, n: int) -> str:
    """At most n characters, saying so when cut."""
    return text if len(text) <= n else text[:n] + f" [cut: {len(text) - n} more characters]"


def _plain_text(text: str, n: int) -> bool:
    """Could this be a name in the record: 1-n characters of real text?"""
    if not 0 < len(text) <= n:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text)


def _about(refs) -> Optional[list]:
    """[(claim id, relation)] -> the claim body's 'about' list."""
    if not refs:
        return None
    return [{"claim": c, "relation": r} for c, r in refs]


def title(writers: dict, wid: str) -> str:
    """How to name a writer so it cannot be mistaken for another: its name,
    the model it runs and where (for an instance, its session), its id, and
    who it acts for. `writers` is state()["state"]["writers"].

    >>> title(ws, "claude-c1-2")
    'Claude (claude-opus-5-5, claude-code, session 7f3a) [claude-c1-2] for John Doe [john]'
    """
    w = writers.get(wid)
    if w is None:
        return f"{wid} (not a registered writer)"
    details = [x for x in (w.get("model"), w.get("surface"),
                           f"session {w['session']}" if w.get("session") else None) if x]
    out = (w.get("name") or wid) + (f" ({', '.join(details)})" if details else "") + f" [{wid}]"
    op = w.get("operator")
    if op:
        opname = (writers.get(op) or {}).get("name")
        out += f" for {opname} [{op}]" if opname else f" for {op}"
    return out


#: The built-in views, as Datalog source (each line says what it means).
VIEWS: str = _unwrap(_native.call("gn_views"))["views"]
