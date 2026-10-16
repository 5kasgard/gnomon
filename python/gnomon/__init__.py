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
    ) -> dict:
        body = {
            "writer": writer,
            "party": party,
            "kind": kind,
            "capabilities": list(capabilities),
            "compartments": list(compartments),
        }
        return self.append(by, "register", body, record_time)

    def episode(self, writer: str, content: str, *, compartment: str, medium: str = "chat",
                participants: Iterable[str] = (), record_time: Optional[str] = None) -> dict:
        body = {
            "compartment": compartment,
            "content": content,
            "medium": medium,
            "participants": list(participants),
        }
        return self.append(writer, "episode", body, record_time)

    def remember(self, writer: str, body: dict, record_time: Optional[str] = None) -> str:
        """Record a claim; returns its content address."""
        self.append(writer, "claim", body, record_time)
        return claim_id(body)

    def verdict(self, writer: str, claim: str, result: str, method: str,
                note: Optional[str] = None, record_time: Optional[str] = None) -> dict:
        body = {"claim": claim, "result": result, "method": method}
        if note:
            body["note"] = note
        return self.append(writer, "verdict", body, record_time)

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

    def query(self, reader: str, program: str, as_of: Optional[str] = None) -> dict:
        """Answer a Datalog query over this memory as `reader` sees it (only
        its compartments), as of `as_of` (latest if None).

        The result has `variables`, `answers` (each with `values` and the id
        of its `proof`), and the proof `nodes`: every derived fact names its
        rule and premises, every base fact the log events it comes from.
        Check them with gnomon.proofs.check, print them with
        gnomon.proofs.explain. Base relations and built-in views are listed
        in the spec (and in gnomon.VIEWS)."""
        return _unwrap(_native.call("gn_query", self.path, as_of, reader, program))["result"]

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


#: The built-in views, as Datalog source (each line says what it means).
VIEWS: str = _unwrap(_native.call("gn_views"))["views"]
