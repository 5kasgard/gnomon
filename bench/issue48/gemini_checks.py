"""Checks of the Gemini review of arm48-combo2 (REVIEWS.md), run before acting on it.

Each check runs two worlds that differ only in a hidden A-side action and compares what the B
side gets back. Run on a build of the branch under review: python3 bench/issue48/gemini_checks.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from gnomon import GateError, Memory, claim  # noqa: E402
from gnomon.session import instance_for  # noqa: E402

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731
M = dict(kind="model", model="m", operator="o")


def world(build):
    d = tempfile.TemporaryDirectory()
    try:
        return build(Memory(str(Path(d.name) / "m.jsonl")))
    except GateError as e:
        return f"escaped {e.code}: {e}"
    finally:
        d.cleanup()


def code(fn):
    try:
        r = fn()
        return f"ok {r!r}"[:120]
    except GateError as e:
        return f"{e.code}: {e}"


def owner(m, comps=("a", "b")):
    m.register("o", "o", party="o", kind="human", capabilities=["owner", "schedule"],
               compartments=list(comps), record_time=T(0))


def check(name, build, note=""):
    h, p = world(lambda m: build(m, True)), world(lambda m: build(m, False))
    print(f"{name}: crosses={h != p} {note}\n  hidden:  {str(h)[:220]}\n  without: {str(p)[:220]}")


# G1: P12r variant. Hidden spawner c-x (A) starts its instance c-x-1; spawner c (B) asks for c-x-1.
def g1(m, hide):
    owner(m)
    m.register("o", "c", party="p", capabilities=["write", "spawn"], compartments=["b"], record_time=T(1), **M)
    m.register("o", "c-x", party="p", capabilities=["write", "spawn"], compartments=["a"], record_time=T(1), **M)
    if hide:
        m.register("c-x", "c-x-1", party="p", capabilities=["write"], compartments=["a"], record_time=T(2), **M)
    return code(lambda: m.register("c", "c-x-1", party="p", capabilities=["write"], compartments=["b"],
                                   record_time=T(3), **M))


# G2: as_of. A hidden A event dated later than B's next write cuts B's as_of view short.
def g2(m, hide):
    owner(m)
    for w, c in (("wa", "a"), ("wb", "b")):
        m.register("o", w, party="p" + w, capabilities=["write"], compartments=[c], record_time=T(1), **M)
    if hide:
        m.remember("wa", claim("late a", speaker="wa", frame="f", compartment="a"), T(9))
    m.remember("wb", claim("b at 3", speaker="wb", frame="f", compartment="b"), T(3))
    st = m.state(as_of=T(5), compartments=["b"])["state"]
    return sorted(c["body"]["statement"] for c in st["claims"].values())


# G3a: an A-side lease to a writer holding A and B advances B's clock; a B-only writer then fails.
def g3a(m, hide):
    owner(m)
    m.register("o", "w", party="pw", capabilities=["write"], compartments=["a", "b"], record_time=T(1), **M)
    m.register("o", "sa", party="psa", capabilities=["write", "schedule"], compartments=["a"], record_time=T(1), **M)
    m.register("o", "wb", party="pwb", capabilities=["write"], compartments=["b"], record_time=T(1), **M)
    if hide:
        m.lease("sa", "w", frame="fa", expires="2026-02-01T00:00:00Z", record_time=T(9))
    return code(lambda: m.remember("wb", claim("b", speaker="wb", frame="f", compartment="b"), T(5)))


# G3b: a writer in A and B: its own A write at a later time makes its B write fail (same writer).
def g3b(m, hide):
    owner(m)
    m.register("o", "x", party="px", capabilities=["write"], compartments=["a", "b"], record_time=T(1), **M)
    if hide:
        m.remember("x", claim("a late", speaker="x", frame="f", compartment="a"), T(9))
    return code(lambda: m.remember("x", claim("b", speaker="x", frame="f", compartment="b"), T(5)))


# G4: V1 and a writer in A and B: an A-side lease on it shows in a B-only reader's `through`.
def g4(m, hide):
    owner(m)
    m.register("o", "w", party="pw", capabilities=["write"], compartments=["a", "b"], record_time=T(1), **M)
    m.register("o", "sa", party="psa", capabilities=["write", "schedule"], compartments=["a"], record_time=T(1), **M)
    m.register("o", "wb", party="pwb", capabilities=["write"], compartments=["b"], record_time=T(1), **M)
    m.remember("wb", claim("b", speaker="wb", frame="fb", compartment="b"), T(2))
    if hide:
        m.lease("sa", "w", frame="fa", expires="2026-02-01T00:00:00Z", record_time=T(3))
    return m.query("wb", "?- claim(C).", proofs=0).get("through")


# G4r: V1 and release: is a released lease's release still visible where its lease is?
def g4r(m, hide):
    owner(m)
    m.register("o", "w", party="pw", capabilities=["write"], compartments=["b"], record_time=T(1), **M)
    m.register("o", "wb", party="pwb", capabilities=["write"], compartments=["b"], record_time=T(1), **M)
    lid = m.lease("o", "w", frame="fb", expires="2030-01-01T00:00:00Z", record_time=T(2))
    if hide:
        m.release("o", lid, record_time=T(3))
    return m.query("wb", "?- claim(C).", proofs=0).get("through")


# G5: instance_for with an owner-registered c-1 in a compartment c cannot see (f1 again, and
# as the MCP server and hook call it: instance_for reads memory.writers() unrestricted).
def g5(m, hide):
    owner(m)
    m.register("o", "c", party="p", capabilities=["write", "spawn"], compartments=["b"], record_time=T(1), **M)
    if hide:
        m.register("o", "c-1", party="q", kind="human", capabilities=["write"], compartments=["a"], record_time=T(2))
    return code(lambda: instance_for(m, "c", "s1"))


# G6: global seq in a B-only writer's write return counts A events.
def g6(m, hide):
    owner(m)
    for w, c in (("wa", "a"), ("wb", "b")):
        m.register("o", w, party="p" + w, capabilities=["write"], compartments=[c], record_time=T(1), **M)
    if hide:
        for i in range(3):
            m.remember("wa", claim(f"a {i}", speaker="wa", frame="f", compartment="a"), T(2))
    r = m.append("wb", "claim", claim("b", speaker="wb", frame="f", compartment="b"), T(3))
    return {k: r.get(k) for k in ("event_id", "seq")}


if __name__ == "__main__":
    check("G1 P12r variant (gate)", g1)
    check("G2 as_of cut short", g2)
    check("G3a lease advances B clock", g3a)
    check("G3b own A write blocks own B write", g3b, "(same writer: it holds A)")
    check("G4 lease on an A+B writer in B's through", g4)
    check("G4r release visibility", g4r, "(control: release of a visible lease should show)")
    check("G5 instance_for, hidden c-1", g5, "(the instance id differs; the call must succeed both ways)")
    check("G6 global seq in a B write return", g6)
