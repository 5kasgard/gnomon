"""Two-world probes for Gnomon #48 cross-compartment isolation (build s-arm48-SZ).

Each probe builds two logs: "hidden" (the A-side action happened) and
"without" (it did not). It returns what the B side gets in each world:
the return value, or (code, message) when the gate refuses.
"""
import os
import sys
import tempfile

sys.path.insert(0, "/tmp/claude-0/wt/s-arm48-SZ/python")
from gnomon import GateError, Memory, claim  # noqa: E402

SCRATCH = "/tmp/claude-0/-home-claude/1f3974a4-3c1f-5540-8d6f-3e5d61d75f8f/scratchpad"
T0 = "2026-01-01T10:00:00Z"


def attempt(fn):
    try:
        return fn()
    except GateError as e:
        return (e.code, str(e))


def fresh():
    d = tempfile.mkdtemp(dir=SCRATCH)
    m = Memory(os.path.join(d, "log.jsonl"))
    m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"],
               compartments=["a", "b"], record_time="2026-01-01T09:00:00Z")
    return m


def both(build):
    """build(m, world) sets up one world; returns {"hidden": ..., "without": ...}."""
    out = {}
    for world in ("hidden", "without"):
        out[world] = build(world)
    return out


# 1. Clock: a writer in {a,b} that writes in a moves clock[b]; a B-only writer is refused.
def p_clock_shared_writer():
    def build(world):
        m = fresh()
        m.register("owner", "x", party="xp", kind="human", capabilities=["write"],
                   compartments=["b"], record_time=T0)
        m.register("owner", "w", party="wp", kind="human", capabilities=["write"],
                   compartments=["a", "b"], record_time=T0)
        if world == "hidden":
            m.remember("w", claim("hidden", speaker="w", frame="f1", compartment="a"),
                       "2026-01-01T12:00:00Z")
        return attempt(lambda: m.remember(
            "x", claim("x", speaker="x", frame="f2", compartment="b"), "2026-01-01T11:00:00Z"))
    return both(build)


# 2. Lease write refusal names the holder (E_LEASE on a claim write) when only the grantor shares B.
def p_lease_write_names_holder():
    def build(world):
        m = fresh()
        m.register("owner", "h", party="hp", kind="human", capabilities=["write"],
                   compartments=["a"], record_time=T0)
        m.register("owner", "w", party="wp", kind="human", capabilities=["write"],
                   compartments=["b"], record_time=T0)
        if world == "hidden":
            m.lease("owner", "h", "F", "2026-12-31T00:00:00Z", record_time="2026-01-01T10:30:00Z")
        return attempt(lambda: m.remember(
            "w", claim("w", speaker="w", frame="F", compartment="b"), "2026-01-01T11:00:00Z"))
    return both(build)


# 3. A claim naming a lease by event id: E_LEASE (holder shown) vs E_REF (absent).
def p_named_lease_probe():
    def build(world):
        m = fresh()
        m.register("owner", "h", party="hp", kind="human", capabilities=["write"],
                   compartments=["a"], record_time=T0)
        m.register("owner", "w", party="wp", kind="human", capabilities=["write"],
                   compartments=["b"], record_time=T0)
        if world == "hidden":
            lid = m.lease("owner", "h", "F", "2026-12-31T00:00:00Z",
                          record_time="2026-01-01T10:30:00Z")
            assert lid == "owner/4", lid
        return attempt(lambda: m.remember(
            "w", claim("w", speaker="w", frame="F", compartment="b", lease="owner/4"),
            "2026-01-01T11:00:00Z"))
    return both(build)


# 4. `through` for B changes when the owner revokes a shared writer from an A compartment.
def p_revoke_moves_through():
    prog = None  # filled below after probing the query language

    def build(world):
        m = fresh()
        m.register("owner", "r", party="rp", kind="human", capabilities=["write"],
                   compartments=["b"], record_time=T0)
        m.register("owner", "w", party="wp", kind="human", capabilities=["write"],
                   compartments=["a", "b"], record_time=T0)
        m.remember("w", claim("pub", speaker="w", frame="f", compartment="b"),
                   "2026-01-01T10:10:00Z")
        if world == "hidden":
            m.revoke("owner", "w", ["a"], "test", record_time="2026-01-01T11:00:00Z")
        r = m.query("r", PROG, proofs=0)
        t = r["through"]
        return {"event": t.get("event"), "kind": t.get("kind"), "time": t.get("time")}
    return both(build)


# 5. Correctness: a B-only writer refused for a record time earlier than an A-only event
#    by an owner it shares no compartment with (owner itself is in a and b).
def p_owner_a_clock_blocks_b():
    def build(world):
        m = fresh()
        m.register("owner", "x", party="xp", kind="human", capabilities=["write"],
                   compartments=["b"], record_time=T0)
        if world == "hidden":
            m.remember("owner", claim("a-only", speaker="owner", frame="f1", compartment="a"),
                       "2026-01-01T10:30:00Z")
        return attempt(lambda: m.remember(
            "x", claim("x", speaker="x", frame="f2", compartment="b"), "2026-01-01T10:20:00Z"))
    return both(build)


# 6. Correctness: as_of drops a write dated before its writer's registration (owner in b only).
def p_as_of_drops_early_write():
    def build(world):
        m = Memory(os.path.join(tempfile.mkdtemp(dir=SCRATCH), "log.jsonl"))
        m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"],
                   compartments=["b"], record_time="2026-01-01T09:00:00Z")
        m.register("owner", "w", party="wp", kind="human", capabilities=["write"],
                   compartments=["a"], record_time="2026-01-01T12:00:00Z")
        if world == "hidden":
            m.remember("w", claim("early", speaker="w", frame="f", compartment="a"),
                       "2026-01-01T11:00:00Z")
        asof = m.state(as_of="2026-01-01T11:30:00Z")["state"]["claims"]
        full = m.state()["state"]["claims"]
        # Full view at the same moment: the write dated 11:00 is in it.
        return {"as_of_11:30": sorted(asof), "full_view": sorted(full)}
    return both(build)


# 7. Session id reuse across disjoint compartments (spec: allowed, no difference expected).
def p_session_disjoint():
    def build(world):
        m = fresh()
        m.register("owner", "op", party="opp", kind="human", capabilities=["write"],
                   compartments=["a", "b"], record_time=T0)
        m.register("owner", "z", party="wp", kind="model", model="m1", operator="op",
                   capabilities=["write"], compartments=["a"], record_time=T0, session="s1")
        if world == "without":
            pass
        return attempt(lambda: m.register("owner", "y", party="wp", kind="model", model="m1",
                                          operator="op", capabilities=["write"],
                                          compartments=["b"], record_time="2026-01-01T10:05:00Z",
                                          session="s1") and "registered")
    return both(build)


PROG = "?- claim(X)."

PROBES = [
    p_clock_shared_writer,
    p_lease_write_names_holder,
    p_named_lease_probe,
    p_revoke_moves_through,
    p_owner_a_clock_blocks_b,
    p_as_of_drops_early_write,
    p_session_disjoint,
]

if __name__ == "__main__":
    for p in PROBES:
        try:
            r = p()
        except Exception as e:  # report harness problems instead of hiding them
            print(f"{p.__name__}: ERROR {type(e).__name__}: {e}")
            continue
        differ = r["hidden"] != r["without"]
        print(f"{p.__name__}: differ={differ}")
        print(f"  hidden:  {r['hidden']}")
        print(f"  without: {r['without']}")
