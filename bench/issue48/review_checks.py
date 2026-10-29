"""Checks of the Haiku review's four new channels (a-d), run before amending PREREG.

Each check runs two worlds that differ only in a hidden A-side event and compares what a
B-side party gets back. Run: python3 bench/issue48/review_checks.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from gnomon import GateError, Memory, claim  # noqa: E402

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def world(build):
    d = tempfile.TemporaryDirectory()
    m = Memory(str(Path(d.name) / "m.jsonl"))
    try:
        return build(m)
    except GateError as e:
        return f"GateError {e.code}: {e}"
    finally:
        d.cleanup()


def base(m):
    m.register("o", "o", party="o", kind="human", capabilities=["owner", "schedule"],
               compartments=["a", "b"], record_time=T(0))
    for w, comps in (("x", ["a", "b"]), ("wa", ["a"]), ("wb", ["b"])):
        m.register("o", w, party="p" + w, kind="model", model="m", operator="o",
                   capabilities=["write", "spawn"], compartments=comps, record_time=T(1))


def check(name, build):
    hidden, plain = world(lambda m: build(m, True)), world(lambda m: build(m, False))
    print(f"{name}: differs={hidden != plain}\n  with hidden A event: {str(hidden)[:200]}\n"
          f"  without:            {str(plain)[:200]}")


# a: the event id returned to a B-side write by a shared writer counts its A events
def a(m, hide):
    base(m)
    if hide:
        for i in range(3):
            m.remember("x", claim(f"in a {i}", speaker="x", frame="f", compartment="a"), T(2))
    return m.append("x", "claim", claim("in b", speaker="x", frame="f", compartment="b"), T(3))["event_id"]


# b: naming a writer: hidden vs missing give different codes; an A-side spawn flips it
def b(m, hide):
    base(m)
    if hide:
        m.register("wa", "wa-1", party="pwa", kind="model", model="m", operator="o",
                   capabilities=["write"], compartments=["a"], record_time=T(2))
    try:
        m.episode("wb", "hi", compartment="b", participants=["wb"], bindings={"them": "wa-1"},
                  record_time=T(3))
        return "accepted"
    except GateError as e:
        return e.code


# c: an A-only lease seen in B's query 'through'
def c(m, hide):
    base(m)
    m.remember("wb", claim("b fact", speaker="wb", frame="fb", compartment="b"), T(2))
    if hide:
        m.lease("o", "wa", frame="fa", expires="2026-02-01T00:00:00Z", record_time=T(5))
    return json.dumps(m.query("wb", "?- claim(C).", proofs=0).get("through"))


# d: a later A-only event makes B's earlier-dated write fail (E_CLOCK)
def d(m, hide):
    base(m)
    if hide:
        m.remember("wa", claim("late a", speaker="wa", frame="f", compartment="a"), T(9))
    try:
        m.remember("wb", claim("b", speaker="wb", frame="f", compartment="b"), T(5))
        return "accepted"
    except GateError as e:
        return e.code


if __name__ == "__main__":
    for n, f in (("a write return", a), ("b existence", b), ("c through", c), ("d clock", d)):
        check(n, f)
