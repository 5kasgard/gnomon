"""Live probes of Gnomon's compartment boundary (bench/archive/map/PROTOCOL.md, C1-C7).

Each probe builds a fresh log in a temp dir with the real Gnomon library
(/home/claude/gnomon-full/python), tries one way for information to cross
from compartment A to compartment B, and records what happened. Output:
probes.json beside this file. Run: python3 probes.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from gnomon import GateError, Memory, claim  # noqa: E402

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731
RESULTS: list[dict] = []


def probe(pid, channel, question, expect):
    def deco(fn):
        d = tempfile.TemporaryDirectory()
        m = Memory(str(Path(d.name) / "m.jsonl"))
        try:
            observed, crosses = fn(m)
        except Exception as e:  # a probe that breaks is reported, never hidden
            observed, crosses = f"probe error: {type(e).__name__}: {e}", None
        RESULTS.append({"id": pid, "channel": channel, "question": question, "expected_if_sealed": expect,
                        "observed": observed, "crosses": crosses})
        d.cleanup()
        return fn
    return deco


def base(m: Memory):
    """Owner in A and B; x in A and B (write, verify); a in A only; b in B only."""
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    for w, comps, party in (("x", ["a", "b"], "px"), ("wa", ["a"], "pa"), ("wb", ["b"], "pb")):
        m.register("o", w, party=party, kind="model", model=f"m-{w}", operator="o",
                   capabilities=["write", "verify"], compartments=comps, record_time=T(1))
    secret = m.remember("wa", claim("The answer to item 7 is 42.", speaker="wa", frame="f", compartment="a",
                                    truth="true", origin="manual", confidence=9000), T(2))
    return secret


def code(fn):
    try:
        fn()
        return "accepted"
    except GateError as e:
        return e.code


@probe("P1", "C1", "Can a writer in A and B read A and copy it into B?", "n/a (this is the road)")
def _(m):
    s = base(m)
    st = m.state(compartments=m._compartments("x"))["state"]
    text = st["claims"][s]["body"]["statement"]
    m.remember("x", claim("Copied: " + text, speaker="x", frame="f", compartment="b"), T(3))
    seen_by_b = [c["body"]["statement"] for c in m.state(compartments=["b"])["state"]["claims"].values()]
    return f"x read A and wrote into B; wb now sees {seen_by_b}", True


@probe("P2", "C1", "Can a verify-only writer in A and B carry content A->B (verdict note)?", "n/a")
def _(m):
    s = base(m)
    m.register("o", "v", party="pv", kind="tool", operator="o", capabilities=["verify"], compartments=["a", "b"],
               record_time=T(3))
    cb = m.remember("wb", claim("Item 7 needs checking.", speaker="wb", frame="f", compartment="b"), T(4))
    m.verdict("v", cb, "abstain", "human", note="FYI the answer is 42", record_time=T(5))
    st = m.state(compartments=["b"])["state"]
    vs = [v for v in st.get("verdicts", {}).values()] if isinstance(st.get("verdicts"), dict) else st.get("verdicts")
    seen = "FYI the answer is 42" in json.dumps(m.state(compartments=["b"]))
    q = m.query("wb", '?- verdict(V, C, W, R, M).', proofs=0)["answers"]
    return f"verdict with A's content in its note accepted; note in B-scoped state: {seen}; wb query sees {len(q)} verdict(s)", True


@probe("P3", "C2", "Claim in B 'about' a claim in A, by a writer in both?", "E_REF")
def _(m):
    s = base(m)
    r = code(lambda: m.remember("x", claim("Re", speaker="x", frame="f", compartment="b",
                                           about=[{"claim": s, "relation": "cites"}]), T(3)))
    return r, r == "accepted"


@probe("P4", "C2", "Handoff in B with refs to A, by a writer in both?", "E_REF")
def _(m):
    s = base(m)
    r = code(lambda: m.handoff("x", frame="f", summary="s", compartment="b", refs=[s], record_time=T(3)))
    return r, r == "accepted"


@probe("P5", "C2", "Claim in B quoting an episode in A, by a writer in both?", "E_COMPARTMENT or E_REF")
def _(m):
    base(m)
    ep = m.episode("x", "Hello", compartment="a", participants=["x"], record_time=T(3))["event_id"]
    r = code(lambda: m.remember("x", claim("Hello", speaker="x", frame="f", compartment="b",
                                           source_episode=ep, quote="Hello", quote_start=0, origin="direct"), T(4)))
    return r, r == "accepted"


@probe("P6", "C2", "Supersession with old in A and new in B?", "E_COMPARTMENT")
def _(m):
    s = base(m)
    nb = m.remember("x", claim("New", speaker="x", frame="f", compartment="b"), T(3))
    r = code(lambda: m.supersede("x", s, nb, "contested", [], "r", T(4)))
    return r, r == "accepted"


@probe("P7", "C2", "Verdict by a B-only writer on a claim in A?", "E_REF")
def _(m):
    s = base(m)
    r = code(lambda: m.verdict("wb", s, "supports", "human", record_time=T(3)))
    return r, r == "accepted"


@probe("P8", "C2", "Episode in B binding a name to an A-only writer?", "E_IDENTITY, same as a missing writer")
def _(m):
    base(m)
    r1 = code(lambda: m.episode("wb", "hi", compartment="b", participants=["W"], bindings={"W": "wa"}, record_time=T(3)))
    r2 = code(lambda: m.episode("wb", "hi", compartment="b", participants=["W"], bindings={"W": "nobody"}, record_time=T(3)))
    return f"bound to hidden writer: {r1}; bound to no writer: {r2}", (r1 != r2) or r1 == "accepted"


@probe("P9", "C3", "Does identical content in A and B corroborate across them (one claim)?", "two different claims")
def _(m):
    base(m)
    ca = m.remember("wa", claim("Same words.", speaker="wa", frame="f", compartment="a"), T(3))
    cb = m.remember("wb", claim("Same words.", speaker="wa", frame="f", compartment="b"), T(3))
    return f"ids equal: {ca == cb}", ca == cb


@probe("P10", "C3", "Does a B-only reader see a writer's operator who lives only in A?",
       "operator hidden or shown alike")
def _(m):
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    m.register("o", "secretop", party="sp", kind="human", capabilities=["write"], compartments=["a"],
               name="Secret Operator", record_time=T(1))
    m.register("o", "bot", party="pb", kind="model", model="m", operator="secretop", capabilities=["write"],
               compartments=["b"], record_time=T(1))
    m.register("o", "wb", party="pwb", kind="human", capabilities=["write"], compartments=["b"], record_time=T(1))
    w = m.writers(compartments=["b"])
    shown = w.get("bot", {}).get("operator")
    q = m.query("wb", '?- identity("bot", "operator", V).', proofs=0)["answers"]
    leak = shown == "secretop" or any(a["values"] == ["secretop"] for a in q)
    return f"writers(b) shows bot.operator={shown!r}; query answers {[a['values'] for a in q]}", leak


@probe("P11", "C3", "Session-id collision: can an A-side spawner signal a B-side spawner of the same party?",
       "B learns nothing about A")
def _(m):
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    for w, c in (("ca", "a"), ("cb", "b")):
        m.register("o", w, party="anthropic", kind="model", model="m", operator="o", name="Claude",
                   surface="claude-code", capabilities=["write", "spawn"], compartments=[c], record_time=T(1))
    bits = [1, 0, 1, 1]
    for i, bit in enumerate(bits):  # A encodes: register session sig-i iff bit is 1
        if bit:
            m.register("ca", f"ca-{i}", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                       surface="claude-code", capabilities=["write"], compartments=["a"], session=f"sig-{i}",
                       record_time=T(2))
    got = []
    for i in range(len(bits)):  # B decodes by trying the same session ids
        r = code(lambda: m.register("cb", f"cb-{i}", party="anthropic", kind="model", model="m", operator="o",
                                    name="Claude", surface="claude-code", capabilities=["write"],
                                    compartments=["b"], session=f"sig-{i}", record_time=T(3)))
        got.append(1 if r == "E_IDENTITY" else 0)
    return f"sent {bits}, B decoded {got} from E_IDENTITY refusals", got == bits


@probe("P12", "C3", "Writer-id namespace: can parent 'c' (in A) signal parent 'c-x' (in B)?",
       "B learns nothing about A")
def _(m):
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    for w, c in (("c", "a"), ("c-x", "b")):
        m.register("o", w, party="p", kind="model", model="m", operator="o", name="C",
                   capabilities=["write", "spawn"], compartments=[c], record_time=T(1))
    bits = [0, 1, 1]
    for i, bit in enumerate(bits):
        if bit:
            m.register("c", f"c-x-{i}", party="p", kind="model", model="m", operator="o", name="C",
                       capabilities=["write"], compartments=["a"], record_time=T(2))
    got = []
    for i in range(len(bits)):
        r = code(lambda: m.register("c-x", f"c-x-{i}", party="p", kind="model", model="m", operator="o",
                                    name="C", capabilities=["write"], compartments=["b"], record_time=T(3)))
        got.append(1 if r == "E_DUPLICATE" else 0)
    return f"sent {bits}, B decoded {got} from E_DUPLICATE refusals", got == bits


@probe("P13", "C3", "Name uniqueness across parties: does registering a name reveal a hidden writer?",
       "only the owner registers other parties, so no non-owner channel")
def _(m):
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    m.register("o", "h", party="ph", kind="human", capabilities=["write"], compartments=["a"], name="Hidden",
               record_time=T(1))
    r = code(lambda: m.register("o", "k", party="pk", kind="human", capabilities=["write"], compartments=["b"],
                                name="Hidden", record_time=T(2)))
    return f"owner registering a B writer named like an A writer: {r} (only the owner can see this)", False


@probe("P14", "C3", "Writer-seq gaps: does a B-only reader see that a two-compartment writer acted in A?",
       "known limit (spec section 5)")
def _(m):
    base(m)
    m.remember("x", claim("in b 1", speaker="x", frame="f", compartment="b"), T(3))
    for i in range(3):
        m.remember("x", claim(f"in a {i}", speaker="x", frame="f", compartment="a"), T(4))
    m.remember("x", claim("in b 2", speaker="x", frame="f", compartment="b"), T(5))
    evs = sorted(c["first_event"] for c in m.state(compartments=["b"])["state"]["claims"].values())
    return f"B sees x's events {evs}: the gap counts x's hidden events", True


@probe("P15", "C4", "Do state, query, recall, frames, resume, discussion for a B-only reader show anything of A?",
       "nothing")
def _(m):
    s = base(m)
    m.handoff("wa", frame="fa", summary="zebrafish handoff", compartment="a", record_time=T(3))
    m.episode("wa", "Zebrafish quokka.", compartment="a", participants=["wa"], record_time=T(3))
    outs = {
        "state": m.state(compartments=m._compartments("wb")),
        "query": m.query("wb", "?- claim(C).", proofs=0),
        # only the hits: the reply echoes the reader's own query terms
        "recall": m.recall("wb", "zebrafish quokka item answer")["hits"],
        "frames": m.frames("wb"),
        "resume": m.resume("wb"),
    }
    try:
        outs["discussion"] = m.discussion("wb", s)
    except GateError as e:
        outs["discussion"] = e.code
    marks = ("item 7 is 42", "ebrafish", "uokka", s, "wa/")
    found = [k for k, v in outs.items() if any(x in json.dumps(v) for x in marks)]
    return f"views that mention A's content or id: {found or 'none'}", bool(found)


@probe("P16", "C5", "Can the owner write into, or register a writer for, a compartment it is not in?",
       "refused")
def _(m):
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a"], record_time=T(0))
    r1 = code(lambda: m.remember("o", claim("x", speaker="o", frame="f", compartment="z"), T(1)))
    r2 = code(lambda: m.register("o", "o2", party="o", kind="human", capabilities=["owner"],
                                 compartments=["a", "z"], record_time=T(1)))
    return f"owner writes to z directly: {r1}; owner registers a new owner identity in a and z: {r2}", r2 == "accepted"


@probe("P17", "C5", "Can a writer's compartments ever be removed (revocation)?", "some way to close a road")
def _(m):
    base(m)
    r = code(lambda: m.register("o", "x", party="px", kind="model", model="m-x", operator="o",
                                capabilities=["write"], compartments=["b"], record_time=T(3)))
    return f"re-registering x with fewer compartments: {r}; no revoke/deregister event kind exists in the core", True


@probe("P18", "C6", "Does a spawned session instance inherit all of its template's compartments?",
       "n/a (sizes the road)")
def _(m):
    from gnomon.session import instance_for
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    m.register("o", "claude", party="anthropic", kind="model", model="m", operator="o", name="Claude",
               surface="claude-code", capabilities=["write", "verify", "spawn"], compartments=["a", "b"],
               record_time=T(1))
    w = instance_for(m, "claude", "sess-1")
    return f"instance {w} compartments {m.writers()[w]['compartments']}", True


@probe("P19", "C7", "Is there any check that the process appending as writer W is W (authentication)?",
       "some check")
def _(m):
    s = base(m)
    # This process is none of the writers; it simply names one.
    r = code(lambda: m.remember("o", claim("Written by whoever can open the file.", speaker="o", frame="f",
                                           compartment="b"), T(3)))
    return f"appending as the owner from an arbitrary process: {r} (identity is by registration only, spec 16)", r == "accepted"


@probe("P20", "C7", "Is compartment A's content readable from the raw log file by any process with file access?",
       "encrypted per compartment")
def _(m):
    base(m)
    raw = Path(m.path).read_text()
    return f"'The answer to item 7 is 42.' in plain text in the log: {'item 7 is 42' in raw}", 'item 7 is 42' in raw


@probe("P21", "C7", "Does an MCP session's writer read every compartment of its template?",
       "n/a (sizes the road)")
def _(m):
    import subprocess
    base(m)
    req = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "p", "version": "0"}}},
           {"jsonrpc": "2.0", "method": "notifications/initialized"},
           {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "recall", "arguments": {"query": "answer item 7"}}}]
    p = subprocess.run([sys.executable, "-m", "gnomon.mcp", "--log", m.path, "--writer", "x"],
                       input="\n".join(json.dumps(r) for r in req) + "\n", capture_output=True, text=True,
                       env={"PYTHONPATH": "/home/claude/gnomon-full/python", "PATH": "/usr/bin:/bin"}, timeout=60)
    hit = "42" in p.stdout
    return f"MCP recall as x (in a and b) returns A's content: {hit}", hit


@probe("P22", "C4", "Do recall scores for a B-only reader depend on A's content (corpus statistics)?",
       "scores identical whatever A holds")
def _(m):
    base(m)
    m.remember("wb", claim("The budget review is on Friday.", speaker="wb", frame="f", compartment="b"), T(3))
    m.remember("wb", claim("Lunch is at noon.", speaker="wb", frame="f", compartment="b"), T(3))
    before = [h["score"] for h in m.recall("wb", "budget review")["hits"]]
    for i in range(30):
        m.remember("wa", claim(f"Budget line {i} is secret.", speaker="wa", frame="f", compartment="a"), T(4))
    after = [h["score"] for h in m.recall("wb", "budget review")["hits"]]
    return f"wb's scores before {before}, after 30 'budget' claims in A {after}", before != after


@probe("P23", "C6", "Auto mode: a session that read A (via MCP) and is recorded by the hook: where do its words go?",
       "n/a (sizes the road): every word lands in one compartment, the template's first unless --compartment")
def _(m):
    import subprocess
    base(m)
    m.register("o", "cl", party="anthropic", kind="model", model="m", operator="o", name="Claude",
               surface="claude-code", capabilities=["write", "spawn"], compartments=["b", "a"], record_time=T(3))
    tr = Path(m.path).with_name("t.jsonl")
    tr.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                               "content": "I looked it up: the answer to item 7 is 42."}}) + "\n")
    ev = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(tr)}
    subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", m.path, "--writer", "cl"], input=json.dumps(ev),
                   capture_output=True, text=True, env={"PYTHONPATH": "/home/claude/gnomon-full/python",
                                                        "PATH": "/usr/bin:/bin"}, timeout=60)
    eps = [e["body"]["compartment"] for e in m.state()["state"]["episodes"].values()
           if "item 7 is 42" in e["body"]["content"]]
    seen_by_wb = "item 7 is 42" in json.dumps(m.state(compartments=["b"]))
    return f"the session's words about A were recorded in {eps}; visible to B-only wb: {seen_by_wb}", seen_by_wb


if __name__ == "__main__":
    out = Path(__file__).with_name("probes.json")
    out.write_text(json.dumps(RESULTS, indent=2))
    for r in RESULTS:
        print(f"{r['id']:4} {r['channel']} crosses={r['crosses']!s:5} | {r['observed'][:150]}")
