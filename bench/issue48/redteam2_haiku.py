"""Two-world probes for the issue #48 isolation change (gnomon, protocol v7).

Each probe builds two fresh logs. They differ only in one action that the
B-side party cannot see. The B-side party's call result is recorded in each
world; a difference is a leak (or a wrong result).
"""

import os
import sys
import tempfile

sys.path.insert(0, "/tmp/claude-0/wt/s-arm48-SZ2/python")
from gnomon import GateError, Memory, claim  # noqa: E402

SCRATCH = "/tmp/claude-0/-home-claude/1f3974a4-3c1f-5540-8d6f-3e5d61d75f8f/scratchpad"


def fresh():
    d = tempfile.mkdtemp(dir=SCRATCH)
    return Memory(os.path.join(d, "log.gn"))


def base(m, with_ann=True):
    """Owner root (a, b); ann (a); bee (b, lessor); gr (a, grantor); hh (a, holder)."""
    m.register("root", "root", party="root", kind="human", capabilities=["owner"],
               compartments=["a", "b"], record_time="2026-10-01T00:00:00Z")
    if with_ann:
        m.register("root", "ann", party="ann", kind="human", capabilities=["write"],
                   compartments=["a"], record_time="2026-10-01T00:00:00Z")
    m.register("root", "bee", party="bee", kind="human", capabilities=["write", "schedule"],
               compartments=["b"], record_time="2026-10-01T00:00:00Z")
    m.register("root", "gr", party="gr", kind="human", capabilities=["write", "schedule"],
               compartments=["a"], record_time="2026-10-01T00:00:00Z")
    m.register("root", "hh", party="hh", kind="human", capabilities=["write"],
               compartments=["a"], record_time="2026-10-01T00:00:00Z")


def attempt(fn):
    try:
        r = fn()
        return ("ok", r)
    except GateError as e:
        return (e.code, str(e))


def claim_body(stmt, comp, frame="f1"):
    return claim(stmt, speaker="x", frame=frame, compartment=comp)


# 1. A hidden lease sits at event id gr/1. The B-side writer releases gr/1.
#    World "hidden": gr/1 is a live lease in A. World "without": gr/1 is a claim in A.
def probe_release_hidden_lease():
    def world(hidden):
        m = fresh()
        base(m)
        if hidden:
            eid = m.lease("gr", "hh", "f1", "2027-06-01T00:00:00Z", record_time="2026-10-03T00:00:00Z")
        else:
            m.remember("gr", claim_body("a claim", "a"), record_time="2026-10-03T00:00:00Z")
            eid = "gr/1"
        assert eid == "gr/1", eid
        return attempt(lambda: m.release("bee", "gr/1", record_time="2026-10-05T00:00:00Z"))
    return {"hidden": world(True), "without": world(False)}


# 1b. Same, but the hidden lease was already released by gr (gr/2).
def probe_release_hidden_ended():
    def world(hidden):
        m = fresh()
        base(m)
        if hidden:
            m.lease("gr", "hh", "f1", "2027-06-01T00:00:00Z", record_time="2026-10-03T00:00:00Z")
            m.release("gr", "gr/1", record_time="2026-10-03T00:00:00Z")
        else:
            m.remember("gr", claim_body("a", "a"), record_time="2026-10-03T00:00:00Z")
            m.remember("gr", claim_body("b", "a"), record_time="2026-10-03T00:00:00Z")
        return attempt(lambda: m.release("bee", "gr/1", record_time="2026-10-05T00:00:00Z"))
    return {"hidden": world(True), "without": world(False)}


# 2. Write return: the B-side writer appends a claim. Only an A-side claim differs.
def probe_write_return_prev():
    def world(hidden):
        m = fresh()
        base(m)
        if hidden:
            m.append("ann", "claim", claim_body("secret", "a"), record_time="2026-10-03T00:00:00Z")
        env = m.append("bee", "claim", claim_body("public", "b"), record_time="2026-10-03T00:00:00Z")
        return {"prev": env.get("prev"), "has_seq": "seq" in env, "event_id": env.get("event_id")}
    return {"hidden": world(True), "without": world(False)}


# 3. Through of a query by the B-side reader. Only an A-side claim differs.
def probe_through_hash():
    def world(hidden):
        m = fresh()
        base(m)
        if hidden:
            m.remember("ann", claim_body("secret", "a"), record_time="2026-10-03T00:00:00Z")
        m.remember("bee", claim_body("public", "b"), record_time="2026-10-03T00:00:00Z")
        r = m.query("bee", '?- identity(W, "party", P).')
        return r.get("through")
    return {"hidden": world(True), "without": world(False)}


# 4. Lease target visibility: bee (b) leases to ann (a). Only ann's registration differs.
def probe_lease_target():
    def world(hidden):
        m = fresh()
        base(m, with_ann=hidden)
        return attempt(lambda: m.lease("bee", "ann", "f9", "2027-06-01T00:00:00Z",
                                       record_time="2026-10-03T00:00:00Z"))
    return {"hidden": world(True), "without": world(False)}


# 5. Scoped frame conflict: bee (b) leases frame f1. Only gr's lease to ann (a) differs.
def probe_frame_conflict():
    # bee (b) leases f1 to cc (b). gr (a) holds a live lease on f1 to hh (a) in the hidden world.
    def world(hidden):
        m = fresh()
        base(m)
        m.register("root", "cc", party="cc", kind="human", capabilities=["write"],
                   compartments=["b"], record_time="2026-10-01T00:00:00Z")
        if hidden:
            m.lease("gr", "hh", "f1", "2027-06-01T00:00:00Z", record_time="2026-10-03T00:00:00Z")
        return attempt(lambda: m.lease("bee", "cc", "f1", "2027-06-01T00:00:00Z",
                                       record_time="2026-10-03T00:00:00Z"))
    return {"hidden": world(True), "without": world(False)}


# 6. Clock: bee (b) writes at a time earlier than ann's (a) later-dated claim.
def probe_clock():
    def world(hidden):
        m = fresh()
        base(m)
        if hidden:
            m.remember("ann", claim_body("late", "a"), record_time="2026-10-20T00:00:00Z")
        return attempt(lambda: m.remember("bee", claim_body("early", "b"), record_time="2026-10-10T00:00:00Z"))
    return {"hidden": world(True), "without": world(False)}


# 7. as_of with non-monotone record times (correctness): claims dated <= t.
def probe_as_of():
    m = fresh()
    base(m)
    m.remember("ann", claim_body("late-a", "a"), record_time="2026-10-09T00:00:00Z")
    m.remember("bee", claim_body("early-b", "b"), record_time="2026-10-05T00:00:00Z")
    full = m.state(as_of="2026-10-07T00:00:00Z")["state"]["claims"]
    restricted = m.state(as_of="2026-10-07T00:00:00Z", compartments=["b"])["state"]["claims"]
    return {"hidden": {"full_count": len(full), "b_count": len(restricted)},
            "without": {"expected_full": 1, "expected_b": 1}}


PROBES = [
    probe_release_hidden_lease,
    probe_release_hidden_ended,
    probe_write_return_prev,
    probe_through_hash,
    probe_lease_target,
    probe_frame_conflict,
    probe_clock,
    probe_as_of,
]


if __name__ == "__main__":
    for p in PROBES:
        try:
            r = p()
            differ = r["hidden"] != r["without"]
            print(f"{p.__name__}: worlds differ = {differ}")
            print(f"   hidden:  {r['hidden']}")
            print(f"   without: {r['without']}")
        except Exception as e:  # report probe-harness failures instead of hiding them
            print(f"{p.__name__}: ERROR {type(e).__name__}: {e}")
