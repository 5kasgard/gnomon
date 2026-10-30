"""Gemini Argon's red-team findings on arm48-SZ, as two-world probes (written by the planner from
Argon's exact steps). Run: python3 -I redteam_argon.py /path/to/build/python"""
import sys, tempfile
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/wt/s-arm48-SZ/python")
from gnomon import GateError, Memory, claim

T = lambda h, m=0: f"2026-01-01T{h:02d}:{m:02d}:00Z"
def att(f):
    try: return ("ok", f())
    except GateError as e: return (e.code, str(e))
def new(): return Memory(tempfile.mkdtemp() + "/m.jsonl")
def both(build): return {"hidden": build(True), "without": build(False)}
H = dict(kind="human")

def a1_clock():
    def b(hide):
        m = new(); m.register("o", "o", party="o", capabilities=["owner"], compartments=["a", "b"], record_time=T(0), **H)
        m.register("o", "w-ab", party="p", capabilities=["write"], compartments=["a", "b"], record_time=T(0), **H)
        m.register("o", "w-b", party="p", capabilities=["write"], compartments=["b"], record_time=T(0), **H)
        if hide: m.remember("w-ab", claim("s", speaker="w-ab", frame="f", compartment="a"), T(2))
        r = att(lambda: m.remember("w-b", claim("s2", speaker="w-b", frame="f2", compartment="b"), T(1)))
        return (r[0], r[1] if r[0] != "ok" else "")
    return both(b)

def a2_lease_grantor():
    def b(hide):
        m = new(); m.register("o", "o", party="o", capabilities=["owner", "schedule"], compartments=["a", "b"], record_time=T(0), **H)
        m.register("o", "w-a", party="pa", capabilities=["write"], compartments=["a"], record_time=T(0), **H)
        m.register("o", "w-b", party="pb", capabilities=["write", "schedule"], compartments=["b"], record_time=T(0), **H)
        m.register("o", "w-b2", party="pb", capabilities=["write"], compartments=["b"], record_time=T(0), **H)
        if hide: m.lease("o", "w-a", "f", "2030-01-02T00:00:00Z", record_time=T(1))
        r = att(lambda: m.lease("w-b", "w-b2", "f", "2030-01-02T00:00:00Z", record_time=T(1)))
        return (r[0], r[1] if r[0] != "ok" else "")
    return both(b)

def a3_revoke_through():
    def b(hide):
        m = new(); m.register("o", "o", party="o", capabilities=["owner"], compartments=["a", "b"], record_time=T(0), **H)
        m.register("o", "w-ab", party="p", capabilities=["write"], compartments=["a", "b"], record_time=T(0), **H)
        m.register("o", "w-b", party="pb", capabilities=["write"], compartments=["b"], record_time=T(0), **H)
        if hide: m.revoke("o", "w-ab", ["a"], "secret", record_time=T(1))
        return m.query("w-b", '?- writer(W, P, K).', proofs=0).get("through")
    return both(b)

def a3b_revoke_hides_history():
    """Correctness: after revoking w_b's only compartment, do b readers still see its registration?"""
    m = new(); m.register("o", "o", party="o", capabilities=["owner"], compartments=["a", "b"], record_time=T(0), **H)
    m.register("o", "w-b", party="pb", capabilities=["write"], compartments=["b"], record_time=T(0), **H)
    m.register("o", "r-b", party="pr", capabilities=["write"], compartments=["b"], record_time=T(0), **H)
    before = sorted(m.writers(compartments=["b"]))
    m.revoke("o", "w-b", ["b"], "gone", record_time=T(1))
    after = sorted(m.writers(compartments=["b"]))
    return {"b readers see writers before": before, "after revoking w_b's only compartment": after}

def a4_as_of_drop():
    def b(hide):
        m = new(); m.register("o", "o", party="o", capabilities=["owner"], compartments=["a"], record_time=T(0), **H)
        if hide:
            m.remember("o", claim("o-a", speaker="o", frame="f", compartment="a"), T(10))
            m.register("o", "w-b", party="pb", capabilities=["write"], compartments=["b"], record_time=T(10), **H)
        else:
            m.register("o", "w-b", party="pb", capabilities=["write"], compartments=["b"], record_time=T(1), **H)
        w = att(lambda: m.remember("w-b", claim("x", speaker="w-b", frame="f", compartment="b"), T(8)))
        st = m.state(as_of=T(9), compartments=["b"])["state"]["claims"]
        return {"write": w[0], "as_of_9_b": sorted(c["body"]["statement"] for c in st.values())}
    return both(b)

if __name__ == "__main__":
    for f in (a1_clock, a2_lease_grantor, a3_revoke_through, a4_as_of_drop):
        r = f(); print(f"{f.__name__}: differ={r['hidden'] != r['without']}\n  hidden:  {str(r['hidden'])[:180]}\n  without: {str(r['without'])[:180]}")
    print("a3b_revoke_hides_history:", a3b_revoke_hides_history())
