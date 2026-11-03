"""Gemini Argon's final red-team findings on arm48-SZ2, as two-world probes (written by the planner
from Argon's steps; ids use '-', times are real ISO times). Run: python3 -I redteam2_argon.py BUILD/python"""
import sys, tempfile
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/wt/s-arm48-SZ2/python")
from gnomon import GateError, Memory, claim

T = lambda h: f"2026-01-01T{h:02d}:00:00Z"
EXP = "2030-01-01T00:00:00Z"
def att(f):
    try: r = f(); return ("ok", r if isinstance(r, str) else "")
    except GateError as e: return (e.code, str(e))
def new(): return Memory(tempfile.mkdtemp() + "/m.jsonl")
def both(b): return {"hidden": b(True), "without": b(False)}
def owner(m): m.register("o", "o", party="o", kind="human", capabilities=["owner", "schedule"], compartments=["a", "b"], record_time=T(1))
def hum(m, w, comps, caps=("write",), t=1, party=None):
    m.register("o", w, party=party or ("p" + w), kind="human", capabilities=list(caps), compartments=comps, record_time=T(t))

def r1_release_unscoped():
    def b(hide):
        m = new(); owner(m); hum(m, "wa", ["a"], ("write", "schedule")); hum(m, "wb", ["b"], ("write", "schedule"))
        hum(m, "wa2", ["a"])
        lid = "o/5"
        if hide: lid = m.lease("wa", "wa2", "f1", EXP, record_time=T(2))
        return att(lambda: m.release("wb", lid, record_time=T(3)))
    return both(b)

def r2_lease_touches_all_lessee():
    def b(hide):
        m = new(); owner(m); hum(m, "wa", ["a"], ("write", "schedule")); hum(m, "wb", ["b"]); hum(m, "wab", ["a", "b"])
        if hide: m.lease("wa", "wab", "f1", EXP, record_time=T(5))
        return att(lambda: m.remember("wb", claim("s", speaker="wb", frame="f2", compartment="b"), T(3)))
    return both(b)

def r3_as_of_operator():
    def b(hide):
        m = new(); owner(m)
        # wa may register (owner-only in Gnomon; so the owner registers opa in A at t5 in the hidden world)
        m.register("o", "opa", party="pa", kind="human", capabilities=["write"], compartments=["a"], record_time=T(5 if hide else 1))
        reg = att(lambda: m.register("o", "botb", party="pb", kind="model", model="m", operator="opa",
                                     capabilities=["write"], compartments=["b"], record_time=T(5 if hide else 2)))
        if hide and reg[0] != "ok":
            return {"reg": reg}
        try: st = sorted(m.state(as_of=T(3), compartments=["b"])["state"]["writers"])
        except GateError as e: st = (e.code, str(e))
        return {"reg": reg[0], "as_of_3_b": st}
    return both(b)

def r3b_as_of_operator_backdated():
    """Argon's exact ordering: opa registered at t5, then botb (operator opa) at t2 by the owner."""
    m = new(); owner(m)
    m.register("o", "opa", party="pa", kind="human", capabilities=["write"], compartments=["a"], record_time=T(5))
    reg = att(lambda: m.register("o", "botb", party="pb", kind="model", model="m", operator="opa",
                                 capabilities=["write"], compartments=["b"], record_time=T(2)))
    try: st = sorted(m.state(as_of=T(3))["state"]["writers"])
    except GateError as e: st = (e.code, str(e))
    return {"botb registration at t2": reg, "as_of(t3) full": st}

def r4_live_lease_from_future():
    def b(hide):
        m = new(); owner(m); hum(m, "wa", ["a"], ("write", "schedule")); hum(m, "wa2", ["a"]); hum(m, "wab", ["a", "b"])
        if hide: m.lease("wa", "wa2", "f1", EXP, record_time=T(5))
        return att(lambda: m.remember("wab", claim("s", speaker="wab", frame="f1", compartment="b"), T(2)))
    return both(b)

if __name__ == "__main__":
    for f in (r1_release_unscoped, r2_lease_touches_all_lessee, r3_as_of_operator, r4_live_lease_from_future):
        r = f(); print(f"{f.__name__}: differ={r['hidden'] != r['without']}\n  hidden:  {str(r['hidden'])[:200]}\n  without: {str(r['without'])[:200]}")
    print("r3b_as_of_operator_backdated:", r3b_as_of_operator_backdated())
