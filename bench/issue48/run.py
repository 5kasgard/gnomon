#!/usr/bin/env python3
"""Issue #48 harness: run the pre-registered measures (PREREG.md, Amendment 1) on the checked-out code.

    python3 bench/issue48/run.py                # everything, write results/<branch>.json
    python3 bench/issue48/run.py --harvest      # (main only) also collect the logs the Python suite builds
                                                # into replay_logs/ and write replay_main.json
    python3 bench/issue48/run.py --skip-suites  # skip cargo/unittest (fast loop while building an arm)

Stdlib only. Nothing here calls a model or a paid API.

A channel test builds two fresh logs that differ only in the hidden A-side action, runs the same B-side
script in both, and compares everything the B side got back (return values, GateError code AND text,
views, query `through`). crosses = outputs differ, or a non-GateError exception escaped.
Hash-chain values (`hash`, `prev`) and the log position `seq` of a returned event are dropped from what
B "gets back": with one chain they differ whenever anything upstream differs, and no arm in the plan
changes that. (Noted in the report: a writer is told the log position of its own event, which counts the
hidden events before it; the plan lists no channel or arm for it.)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
from gnomon import GateError, Memory, _native, claim  # noqa: E402

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731
FAR = "2099-01-01T00:00:00Z"  # a lease expiry far after every record_time used here, and after the gate's clock


# --------------------------------------------------------------------- world plumbing
def scrub(v):
    """Drop hash-chain values (see module docstring) from anything B gets back."""
    if isinstance(v, dict):
        envelope = {"event_id", "writer_seq", "hash"} <= set(v)  # a returned event: `seq` is its log position
        return {k: scrub(x) for k, x in v.items() if k not in ("hash", "prev") and not (envelope and k == "seq")}
    if isinstance(v, list):
        return [scrub(x) for x in v]
    return v


def cap(fn):
    """Run fn; a refusal is data, not an exception."""
    try:
        return {"ok": scrub(fn())}
    except GateError as e:
        return {"err": e.code, "msg": e.message}
    except Exception as e:  # noqa: BLE001 - anything else is reported as an escape
        return {"exception": f"{type(e).__name__}: {e}"}


class World:
    def __init__(self):
        self._d = tempfile.TemporaryDirectory()
        self.m = Memory(str(Path(self._d.name) / "m.jsonl"))
        self.a: list = []  # hidden A-side calls (kept for the record, never compared)
        self.b: list = []  # what the B side got back (compared)

    def A(self, label, fn):
        r = cap(fn)
        self.a.append([label, r])
        return r

    def B(self, label, fn):
        r = cap(fn)
        self.b.append([label, r])
        return r

    def close(self):
        self._d.cleanup()

    @property
    def escaped(self):
        return [x for x in self.a + self.b if "exception" in x[1]]


def diff(a, b, path=""):
    if type(a) is not type(b):
        return [path or "/"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}[len {len(a)} vs {len(b)}]"]
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [path or "/"]


def keyed(calls):
    """[[label, result], ...] -> {label: result}, labels made unique."""
    out = {}
    for label, r in calls:
        k, i = label, 2
        while k in out:
            k, i = f"{label} #{i}", i + 1
        out[k] = r
    return out


def two_worlds(build):
    """Run build(world, hidden) with hidden=True then False; compare B's outputs."""
    outs, a_side, esc = [], [], []
    for hidden in (True, False):
        w = World()
        try:
            build(w, hidden)
            outs.append(w.b)
            a_side.append(w.a)
            esc += w.escaped
        finally:
            w.close()
    outs = [keyed(o) for o in outs]
    d = diff(outs[0], outs[1])
    return {
        "crosses": bool(d) or bool(esc),
        "differs": bool(d),
        "exception_escaped": [e[0] for e in esc],
        "diff_paths": d[:10],
        "B_with_hidden": outs[0],
        "B_without_hidden": outs[1],
        "A_side_refused_with_hidden": {k: [v["err"], v["msg"]] for k, v in keyed(a_side[0]).items() if "err" in v},
        "A_side_refused_without_hidden": {k: [v["err"], v["msg"]] for k, v in keyed(a_side[1]).items() if "err" in v},
    }


# --------------------------------------------------------------------- shared set-up and views
def reg(w, by, wid, comps, caps=("write",), party=None, kind="model", **kw):
    if kind == "model":
        kw.setdefault("model", "m")
        kw.setdefault("operator", "o")
    return w.A(f"register {wid}", lambda: w.m.register(
        by, wid, party=party or "p-" + wid, kind=kind, capabilities=list(caps), compartments=list(comps),
        record_time=kw.pop("record_time", T(1)), **kw))


def base(w, schedulers=False):
    """Owner o in A and B; x in A and B; wa in A only; wb in B only (all can spawn)."""
    w.A("register o", lambda: w.m.register(
        "o", "o", party="o", kind="human", capabilities=["owner", "schedule"], compartments=["a", "b"],
        record_time=T(0)))
    for wid, comps in (("x", ["a", "b"]), ("wa", ["a"]), ("wb", ["b"])):
        reg(w, "o", wid, comps, caps=["write", "spawn"])
    if schedulers:
        reg(w, "o", "sa", ["a"], caps=["write", "schedule"])
        reg(w, "o", "sb", ["b"], caps=["write", "schedule"])


def views(w, reader, comps, tag="views"):
    """The views a B-side reader has: scoped state, writers, claims query (with `through`), identity facts."""
    w.B(f"{tag}.state", lambda: w.m.state(compartments=comps)["state"])
    w.B(f"{tag}.writers", lambda: w.m.writers(compartments=comps))
    w.B(f"{tag}.claims", lambda: w.m.query(reader, "?- claim(C).", proofs=0))
    w.B(f"{tag}.identity", lambda: w.m.query(reader, "?- identity(W,K,V).", proofs=0))


def say(w, writer, text, comp, t, frame="f"):
    return lambda: w.m.append(writer, "claim", claim(text, speaker=writer, frame=frame, compartment=comp), t)


# --------------------------------------------------------------------- the channels
def ch_P10(w, hidden):
    # Pair chosen so the ONLY difference is hidden: bot (in B) has an operator who lives only in A, and
    # the two worlds differ in WHICH A-only person that is (secretop1 vs secretop2; same compartments, same
    # shape, different ids and names). Everything B can legitimately see is identical. Not chosen:
    # "operator in A only" vs "operator in A and B" (the operator becomes visible to B, so a difference
    # is legitimate), and "operator vs no operator" (under O2 that pair differs in whether the owner's
    # registration of bot is refused, so B sees bot or not for a reason that is not the hidden secret).
    w.A("register o", lambda: w.m.register("o", "o", party="o", kind="human", capabilities=["owner"],
                                           compartments=["a", "b"], record_time=T(0)))
    for i in (1, 2):
        w.A(f"register secretop{i}", lambda i=i: w.m.register(
            "o", f"secretop{i}", party=f"sp{i}", kind="human", capabilities=["write"], compartments=["a"],
            name=f"Secret Operator {i}", record_time=T(1)))
    w.A("register bot", lambda: w.m.register(
        "o", "bot", party="pb", kind="model", model="m", operator="secretop1" if hidden else "secretop2",
        capabilities=["write"], compartments=["b"], record_time=T(1)))
    w.A("register wb", lambda: w.m.register("o", "wb", party="pwb", kind="human", capabilities=["write"],
                                            compartments=["b"], record_time=T(1)))
    views(w, "wb", ["b"])


def _spawner_world(w, names):
    w.A("register o", lambda: w.m.register("o", "o", party="o", kind="human", capabilities=["owner"],
                                           compartments=["a", "b"], record_time=T(0)))
    for wid, comp in names:
        w.A(f"register {wid}", lambda wid=wid, comp=comp: w.m.register(
            "o", wid, party="anthropic", kind="model", model="m", operator="o", name="Claude",
            surface="claude-code", capabilities=["write", "spawn"], compartments=[comp], record_time=T(1)))


def ch_P11(w, hidden):
    _spawner_world(w, (("ca", "a"), ("cb", "b")))
    bits = [1, 0, 1, 1] if hidden else [0, 0, 0, 0]
    for i, bit in enumerate(bits):
        if bit:
            w.A(f"ca registers session sig-{i}", lambda i=i: w.m.register(
                "ca", f"ca-{i}", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                surface="claude-code", capabilities=["write"], compartments=["a"], session=f"sig-{i}",
                record_time=T(2)))
    for i in range(4):
        w.B(f"cb tries session sig-{i}", lambda i=i: w.m.register(
            "cb", f"cb-{i}", party="anthropic", kind="model", model="m", operator="o", name="Claude",
            surface="claude-code", capabilities=["write"], compartments=["b"], session=f"sig-{i}",
            record_time=T(3)))
    views(w, "cb", ["b"])


def _c_inst(w, by, wid, comp, t, who="B"):
    f = w.B if who == "B" else w.A
    return f(f"{by} registers {wid}", lambda: w.m.register(
        by, wid, party="p", kind="model", model="m", operator="o", name="C", capabilities=["write"],
        compartments=[comp], record_time=t))


def ch_P12(w, hidden):
    w.A("register o", lambda: w.m.register("o", "o", party="o", kind="human", capabilities=["owner"],
                                           compartments=["a", "b"], record_time=T(0)))
    for wid, comp in (("c", "a"), ("c-x", "b")):
        w.A(f"register {wid}", lambda wid=wid, comp=comp: w.m.register(
            "o", wid, party="p", kind="model", model="m", operator="o", name="C",
            capabilities=["write", "spawn"], compartments=[comp], record_time=T(1)))
    bits = [0, 1, 1] if hidden else [0, 0, 0]
    for i, bit in enumerate(bits):
        if bit:
            _c_inst(w, "c", f"c-x-{i}", "a", T(2), who="A")
    for i in range(3):
        _c_inst(w, "c-x", f"c-x-{i}", "b", T(3))
    views(w, "c-x", ["b"])


def ch_P12r(w, hidden):
    # Reversed order: c takes c-x-<i> BEFORE c-x exists; the owner then registers c-x in B. The owner's
    # registration of c-x is B's own identity being created, so its outcome is part of what B gets back.
    w.A("register o", lambda: w.m.register("o", "o", party="o", kind="human", capabilities=["owner"],
                                           compartments=["a", "b"], record_time=T(0)))
    w.A("register c", lambda: w.m.register(
        "o", "c", party="p", kind="model", model="m", operator="o", name="C",
        capabilities=["write", "spawn"], compartments=["a"], record_time=T(1)))
    bits = [0, 1, 1] if hidden else [0, 0, 0]
    for i, bit in enumerate(bits):
        if bit:
            _c_inst(w, "c", f"c-x-{i}", "a", T(2), who="A")
    w.B("owner registers c-x", lambda: w.m.register(
        "o", "c-x", party="p", kind="model", model="m", operator="o", name="C",
        capabilities=["write", "spawn"], compartments=["b"], record_time=T(3)))
    for i in range(3):
        _c_inst(w, "c-x", f"c-x-{i}", "b", T(4))
    views(w, "c-x", ["b"])


def ch_P14(w, hidden):
    base(w)
    w.B("x writes in b #1", say(w, "x", "in b 1", "b", T(3)))
    if hidden:
        for i in range(3):
            w.A(f"x writes in a {i}", say(w, "x", f"in a {i}", "a", T(4)))
    w.B("x writes in b #2", say(w, "x", "in b 2", "b", T(5)))
    views(w, "wb", ["b"])  # B's views only; the write returns are channel R


def ch_R(w, hidden):
    base(w)
    if hidden:
        for i in range(3):
            w.A(f"x writes in a {i}", say(w, "x", f"in a {i}", "a", T(2)))
    w.B("x claim in b", lambda: (lambda e: {"event_id": e["event_id"], "writer_seq": e["writer_seq"]})(
        w.m.append("x", "claim", claim("in b", speaker="x", frame="f", compartment="b"), T(3))))
    w.B("x episode in b", lambda: {"event_id": w.m.episode(
        "x", "hello", compartment="b", participants=["x"], record_time=T(4))["event_id"]})
    w.B("x handoff in b", lambda: {"event_id": w.m.handoff(
        "x", frame="f", summary="s", compartment="b", record_time=T(5))})


def ch_L(w, hidden):
    base(w, schedulers=True)
    # L-a: does a lease to a writer the scheduler cannot see tell it whether that writer exists?
    if hidden:
        w.A("wa spawns wa-1", lambda: w.m.register(
            "wa", "wa-1", party="p-wa", kind="model", model="m", operator="o", capabilities=["write"],
            compartments=["a"], record_time=T(2)))
    w.B("sb leases fb1 to wa-1", lambda: w.m.lease("sb", "wa-1", frame="fb1", expires=FAR, record_time=T(3)))
    # L-b: does a refusal name an A-side lease's holder and expiry?
    if hidden:
        w.A("sa leases shared to wa", lambda: w.m.lease("sa", "wa", frame="shared", expires=FAR, record_time=T(4)))
    w.B("sb leases shared to wb", lambda: w.m.lease("sb", "wb", frame="shared", expires=FAR, record_time=T(5)))


def _v(kind):
    def ch(w, hidden):
        base(w)
        w.B("wb writes", say(w, "wb", "b fact", "b", T(2)))
        if hidden:
            if kind == "lease":
                w.A("o leases fa to wa", lambda: w.m.lease("o", "wa", frame="fa", expires=FAR, record_time=T(5)))
            elif kind == "release":
                lid = w.A("o leases fa to wa", lambda: w.m.lease("o", "wa", frame="fa", expires=FAR, record_time=T(5)))
                lid = lid.get("ok")
                w.A("o releases it", lambda: w.m.release("o", lid, record_time=T(6)))
            elif kind == "revoke":
                w.A("o revokes wa from a", lambda: w.m.revoke("o", "wa", ["a"], "test", record_time=T(5)))
        w.B("wb query", lambda: w.m.query("wb", "?- claim(C).", proofs=0))
        views(w, "wb", ["b"])
    return ch


def ch_C(w, hidden):
    base(w)
    if hidden:
        w.A("wa writes late in a", say(w, "wa", "late a", "a", T(9)))
    w.B("wb writes earlier-dated in b", say(w, "wb", "b", "b", T(5)))
    views(w, "wb", ["b"])


def ch_control(w, hidden):
    """Negative control (must NOT cross, on any code): an A-only claim by an A-only writer, with B reading."""
    base(w)
    if hidden:
        w.A("wa writes in a", say(w, "wa", "hidden fact", "a", T(2)))
    w.B("wb writes in b", say(w, "wb", "b fact", "b", T(3)))
    views(w, "wb", ["b"])


REQUIRED = [("P10", ch_P10), ("P11", ch_P11), ("P12", ch_P12), ("P12r", ch_P12r), ("P14", ch_P14), ("R", ch_R),
            ("L", ch_L), ("V", _v("lease")), ("C", ch_C)]
EXTRA = [("control", ch_control), ("V-release", _v("release")), ("V-revoke", _v("revoke"))]  # same root as V; reported, not required


# --------------------------------------------------------------------- intended-rule checks (measure 5)
def rule(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    return {"rule": name, "holds": bool(ok), "detail": detail}


@contextlib.contextmanager
def tmpmem():
    d = tempfile.TemporaryDirectory()
    try:
        yield Memory(str(Path(d.name) / "m.jsonl")), Path(d.name)
    finally:
        d.cleanup()


def code_of(fn):
    try:
        fn()
        return "accepted"
    except GateError as e:
        return e.code


def owner(m, comps=("a", "b"), caps=("owner", "schedule")):
    m.register("o", "o", party="o", kind="human", capabilities=list(caps), compartments=list(comps), record_time=T(0))


def r_S1():
    with tmpmem() as (m, _):
        owner(m)
        kw = dict(party="anthropic", kind="model", model="m", operator="o", name="Claude", surface="claude-code",
                  capabilities=["write"], session="sess", record_time=T(1))
        m.register("o", "w1", compartments=["a"], **kw)
        same = code_of(lambda: m.register("o", "w2", compartments=["a"], **kw))
        overlap = code_of(lambda: m.register("o", "w3", compartments=["a", "b"], **kw))
        return same == "E_IDENTITY" and overlap == "E_IDENTITY", f"same compartment: {same}; overlapping: {overlap}"


def _spawn_ok():
    from gnomon.session import instance_for
    with tmpmem() as (m, d):
        owner(m)
        m.register("o", "sp", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                   surface="claude-code", capabilities=["write", "spawn"], compartments=["a", "b"], record_time=T(1))
        direct = code_of(lambda: m.register("sp", "sp-1", party="anthropic", kind="model", model="m", operator="o",
                                            name="Claude", surface="claude-code", capabilities=["write"],
                                            compartments=["a"], record_time=T(2)))
        i1 = instance_for(m, "sp", "sess-1", surface="claude-code", compartments=["a"])
        i2 = instance_for(m, "sp", "sess-2", surface="claude-code", compartments=["a"])
        i3 = instance_for(m, "sp", "sess-1", surface="claude-code", compartments=["a"])
        return direct, [i1, i2, i3]


def r_I():
    direct, ids = _spawn_ok()
    # the hook (a subprocess, as Claude Code runs it) and mcp.py --per-session
    with tmpmem() as (m, d):
        owner(m, comps=("lab",))
        m.register("o", "claude", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                   surface="claude-code", capabilities=["write", "verify", "spawn"], compartments=["lab"],
                   record_time=T(1))
        tr = d / "t.jsonl"
        tr.write_text("")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
        ev = {"hook_event_name": "SessionStart", "session_id": "s-1", "transcript_path": str(tr), "cwd": str(d)}
        p = subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", m.path, "--writer", "claude"],
                           input=json.dumps(ev), capture_output=True, text=True, env=env, timeout=120)
        hook_inst = [k for k, v in m.writers().items() if v.get("instance_of") == "claude" and v.get("session") == "s-1"]
        token = None
        with contextlib.suppress(Exception):
            token = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"].split("token ")[1].split(",")[0]
        mcp_who = None
        if token:
            sys.path.insert(0, str(ROOT / "python" / "tests"))
            from test_mcp import Proc  # noqa: E402
            s = Proc("--log", m.path, "--writer", "claude", "--per-session")
            try:
                r = s.call("attach", {"token": token})
                mcp_who = None if r.get("isError") else s.call("whoami", {})["structuredContent"]["writer"]
            finally:
                s.close()
    ok = (direct == "accepted" and len(set(ids)) == 2 and ids[0] == ids[2] and len(hook_inst) == 1
          and mcp_who == hook_inst[0])
    return ok, (f"spawner registers sp-1 directly: {direct}; instance_for ids {ids}; hook instance {hook_inst}"
                f" (rc={p.returncode}); mcp --per-session attached as {mcp_who}")


def r_O1():
    with tmpmem() as (m, _):
        owner(m, comps=("a", "b"), caps=("owner",))
        m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=["a", "b"], record_time=T(1))
        m.register("o", "bot", party="pb", kind="model", model="m", operator="op", capabilities=["write"],
                   compartments=["b"], record_time=T(1))
        m.register("o", "wb", party="pwb", kind="human", capabilities=["write"], compartments=["b"], record_time=T(1))
        shown = m.writers(compartments=["b"]).get("bot", {}).get("operator")
        q = [a["values"] for a in m.query("wb", '?- identity("bot","operator",V).', proofs=0)["answers"]]
        return shown == "op" and q == [["op"]], f"writers(b) bot.operator={shown!r}; datalog identity answers {q}"


def r_O2():
    with tmpmem() as (m, _):
        owner(m, comps=("a", "b"), caps=("owner",))
        m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=["a", "b"], record_time=T(1))
        r1 = code_of(lambda: m.register("o", "bot", party="pb", kind="model", model="m", operator="op",
                                        capabilities=["write"], compartments=["b"], record_time=T(1)))
        r2 = code_of(lambda: m.register("o", "bot2", party="pb2", kind="model", model="m", operator="op",
                                        capabilities=["write"], compartments=["a", "b"], record_time=T(1)))
        return r1 == "accepted" and r2 == "accepted", f"operator in all of the writer's compartments: {r1}, {r2}"


def r_Q1():
    with tmpmem() as (m, _):
        owner(m, caps=("owner",))
        m.register("o", "x", party="px", kind="model", model="m", operator="o", capabilities=["write", "verify"],
                   compartments=["a", "b"], record_time=T(1))
        c1 = m.remember("x", claim("first", speaker="x", frame="f", compartment="b"), T(2))
        m.remember("x", claim("elsewhere", speaker="x", frame="f", compartment="a"), T(3))
        c2 = m.remember("x", claim("second cites first", speaker="x", frame="f", compartment="b",
                                   about=[{"claim": c1, "relation": "cites"}]), T(4))
        m.remember("x", claim("elsewhere 2", speaker="x", frame="f", compartment="a"), T(5))
        sup = code_of(lambda: m.supersede("x", c1, c2, "generalised", [c2], "r", T(6)))
        h = m.handoff("x", frame="f", summary="s", compartment="b", refs=[c1, c2], record_time=T(7))
        st = m.state(compartments=["b"])["state"]
        ok = (sup == "accepted" and len(st["supersessions"]) == 1 and h in st["handoffs"]
              and c1 in json.dumps(st["handoffs"][h]) and c2 in json.dumps(st["handoffs"][h]))
        return ok, f"cite accepted; supersede {sup}; handoff {h} holds both refs in B's view"


def r_L1():
    with tmpmem() as (m, _):
        owner(m)
        m.register("o", "sb", party="psb", kind="model", model="m", operator="o", capabilities=["write", "schedule"],
                   compartments=["b"], record_time=T(1))
        m.register("o", "wb", party="pwb", kind="model", model="m", operator="o", capabilities=["write"],
                   compartments=["b"], record_time=T(1))
        r = code_of(lambda: m.lease("sb", "wb", frame="fb", expires=FAR, record_time=T(2)))
        return r == "accepted", f"scheduler in b leases to writer in b: {r}"


def r_V1():
    with tmpmem() as (m, _):
        owner(m)
        m.register("o", "wb", party="pwb", kind="model", model="m", operator="o", capabilities=["write"],
                   compartments=["b"], record_time=T(1))
        m.register("o", "rb", party="prb", kind="model", model="m", operator="o", capabilities=["write"],
                   compartments=["b"], record_time=T(1))
        lid = m.lease("o", "wb", frame="fb", expires=FAR, record_time=T(2))
        t1 = m.query("rb", "?- claim(C).", proofs=0)["through"]
        rel = m.release("o", lid, record_time=T(3))
        t2 = m.query("rb", "?- claim(C).", proofs=0)["through"]
        ok = (t1 or {}).get("event") == lid and (t2 or {}).get("event") == rel["event_id"]
        return ok, f"reader sharing the target's compartment: through after lease {t1 and t1.get('event')} (lease {lid}); after release {t2 and t2.get('event')}"


def r_T1():
    with tmpmem() as (m, _):
        owner(m, comps=("a",), caps=("owner",))
        m.register("o", "w", party="pw", kind="model", model="m", operator="o", capabilities=["write"],
                   compartments=["a"], record_time=T(1))
        m.remember("w", claim("late", speaker="w", frame="f", compartment="a"), T(5))
        r = code_of(lambda: m.remember("w", claim("early", speaker="w", frame="f", compartment="a"), T(3)))
        return r == "E_CLOCK", f"single-compartment log, earlier-dated write after a later one: {r}"


def r_O2_refusal_text():
    """For O2: the refusal must not name the operator's compartments (informational on other code)."""
    with tmpmem() as (m, _):
        owner(m, comps=("zq-alpha", "zq-beta"), caps=("owner",))
        m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=["zq-alpha"], record_time=T(1))
        try:
            m.register("o", "bot", party="pb", kind="model", model="m", operator="op", capabilities=["write"],
                       compartments=["zq-beta"], record_time=T(1))
            return True, "accepted (no refusal on this code)"
        except GateError as e:
            return "zq-alpha" not in e.message, f"{e.code}: {e.message}"


RULES = [("S1", r_S1), ("I1/I2", r_I), ("O1", r_O1), ("O2", r_O2), ("O2-text", r_O2_refusal_text), ("Q1", r_Q1),
         ("L1", r_L1), ("V1", r_V1), ("T1", r_T1)]


# --------------------------------------------------------------------- regression: probes.py in-process
def run_probes():
    src = (HERE / "probes.py").read_text()
    old = "/home/claude/gnomon-full/python"
    substituted = False
    if old in src and not Path(old).exists():
        # P21/P23 run subprocesses with this hard-coded path; it is absent here, so they would test nothing.
        src = src.replace(old, str(ROOT / "python"))
        substituted = True
    mod = types.ModuleType("probes_inproc")
    mod.__file__ = str(HERE / "probes.py")
    exec(compile(src, str(HERE / "probes.py"), "exec"), mod.__dict__)  # noqa: S102
    return {r["id"]: r["crosses"] for r in mod.RESULTS}, substituted, {r["id"]: r["observed"][:160] for r in mod.RESULTS}


# --------------------------------------------------------------------- suites
def run_suites():
    out = {}
    t0 = time.time()
    p = subprocess.run(["cargo", "test", "--release", "--offline"], cwd=ROOT, capture_output=True, text=True)
    txt = p.stdout + p.stderr
    res = re.findall(r"test result: (\w+)\. (\d+) passed; (\d+) failed", txt)
    out["cargo"] = {"returncode": p.returncode, "passed": sum(int(r[1]) for r in res),
                    "failed": sum(int(r[2]) for r in res), "result_lines": len(res),
                    "failing": re.findall(r"^test (\S+) \.\.\. FAILED", txt, re.M)[:20],
                    "seconds": round(time.time() - t0, 1)}
    if p.returncode and not res:
        out["cargo"]["error_tail"] = txt[-800:]
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "python/tests"], cwd=ROOT,
                       capture_output=True, text=True)
    txt = p.stderr + p.stdout
    ran = re.search(r"Ran (\d+) tests?", txt)
    bad = re.search(r"FAILED \((.*?)\)", txt)
    nums = dict((k, int(v)) for k, v in re.findall(r"(\w+)=(\d+)", bad.group(1))) if bad else {}
    out["python"] = {"returncode": p.returncode, "ran": int(ran.group(1)) if ran else None,
                     "failures": nums.get("failures", 0), "errors": nums.get("errors", 0),
                     "skipped": int(m.group(1)) if (m := re.search(r"skipped=(\d+)", txt)) else 0,
                     "seconds": round(time.time() - t0, 1)}
    if p.returncode:
        out["python"]["failing"] = re.findall(r"^(?:FAIL|ERROR): (\S+ \(\S+\))", txt, re.M)[:20]
        if not ran:
            out["python"]["error_tail"] = txt[-800:]
    return out


def harvest_python_logs(dest: Path):
    """Run the Python suite once with shutil.rmtree wrapped so every log dir is copied before it is deleted."""
    hd = Path(tempfile.mkdtemp(prefix="gn-harvest-"))
    sc = hd / "_sc"
    sc.mkdir()
    (sc / "sitecustomize.py").write_text(
        "import hashlib, json, os, shutil\n"
        "_hd = os.environ.get('GN_HARVEST')\n"
        "if _hd:\n"
        "    _orig = shutil.rmtree\n"
        "    def _is_log(p):\n"
        "        try:\n"
        "            with open(p, 'rb') as f:\n"
        "                first = f.readline()\n"
        "            e = json.loads(first)\n"
        "            return isinstance(e, dict) and 'hash' in e and 'writer_seq' in e\n"
        "        except Exception:\n"
        "            return False\n"
        "    def rmtree(path, *a, **k):\n"
        "        try:\n"
        "            for root, _, files in os.walk(path):\n"
        "                for n in files:\n"
        "                    p = os.path.join(root, n)\n"
        "                    if n.endswith('.jsonl') and _is_log(p):\n"
        "                        data = open(p, 'rb').read()\n"
        "                        open(os.path.join(_hd, hashlib.sha256(data).hexdigest()[:16] + '.jsonl'), 'wb').write(data)\n"
        "        except Exception:\n"
        "            pass\n"
        "        return _orig(path, *a, **k)\n"
        "    shutil.rmtree = rmtree\n")
    out = hd / "logs"
    out.mkdir()
    env = dict(os.environ, GN_HARVEST=str(out), PYTHONPATH=str(sc) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "python/tests"], cwd=ROOT,
                       capture_output=True, text=True, env=env)
    dest.mkdir(exist_ok=True)
    for old in dest.glob("*.jsonl"):
        old.unlink()
    n = 0
    for f in sorted(out.glob("*.jsonl")):
        shutil.copy(f, dest / f.name)
        n += 1
    shutil.rmtree(hd, ignore_errors=True)
    return n, p.returncode


def replay(path: Path):
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "m.jsonl"
        shutil.copy(path, c)
        r = _native.call("gn_state", str(c), None, None)
        if not r.get("ok"):
            return {"error": r.get("code"), "message": r.get("message", "")[:120]}
        return {"count": r["count"], "head": r["head"], "digest": r["digest"]}


def replay_all():
    logs = sorted((HERE / "replay_logs").glob("*.jsonl"))
    return {f.name: replay(f) for f in logs}


# --------------------------------------------------------------------- size
def classify(path: str) -> str:
    if path.startswith("bench/"):
        return "bench"
    if path.startswith("spec/") or path.startswith("docs/"):
        return "spec"
    if path.startswith("python/tests/") or path.startswith("tests/") or "/tests/" in path:
        return "tests"
    if path.startswith("crates/") or path in ("Cargo.toml", "Cargo.lock"):
        return "rust"
    if path.startswith("python/"):
        return "python"
    return "other"


def size():
    p = subprocess.run(["git", "diff", "--numstat", "main...HEAD"], cwd=ROOT, capture_output=True, text=True)
    out = {k: {"added": 0, "deleted": 0, "net": 0} for k in ("rust", "python", "spec", "tests", "bench", "other")}
    for line in p.stdout.splitlines():
        a, d, path = line.split("\t", 2)
        if a == "-":  # binary
            continue
        k = classify(path)
        out[k]["added"] += int(a)
        out[k]["deleted"] += int(d)
        out[k]["net"] += int(a) - int(d)
    return out


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-suites", action="store_true")
    ap.add_argument("--harvest", action="store_true", help="collect the Python suite's logs as the replay set")
    ap.add_argument("--out", help="results file (default results/<branch>.json)")
    a = ap.parse_args()

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    res = {"branch": branch, "commit": git("rev-parse", "--short", "HEAD"), "python": sys.version.split()[0]}

    ch = {}
    for name, fn in REQUIRED + EXTRA:
        ch[name] = two_worlds(fn)
        ch[name]["required"] = (name, fn) in REQUIRED
    res["channels"] = ch
    res["control_ok"] = not ch["control"]["crosses"]
    if branch == "issue-48":  # main's code: every required channel must cross, or the harness is wrong
        res["main_expectation_met"] = all(ch[n]["crosses"] for n, _ in REQUIRED) and res["control_ok"]

    crosses, subst, observed = run_probes()
    res["probes"] = {"crosses": crosses, "path_substituted": subst, "observed": observed}
    main_file = HERE / "results" / "issue-48.json"
    if branch != "issue-48" and main_file.exists():
        base_p = json.loads(main_file.read_text())["probes"]["crosses"]
        res["probes"]["changed_vs_main"] = {k: [base_p.get(k), v] for k, v in crosses.items() if base_p.get(k) != v}

    res["rules"] = [rule(n, f) for n, f in RULES]
    res["tests_vectors_logs"] = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests" / "vectors").iterdir()
                                       if p.suffix in (".jsonl", ".log"))

    if a.harvest:
        n, rc = harvest_python_logs(HERE / "replay_logs")
        res["harvest"] = {"logs": n, "unittest_returncode": rc}
    replayed = replay_all()
    res["replay"] = {"n": len(replayed), "hashes": replayed}
    mp = HERE / "replay_main.json"
    if a.harvest:
        mp.write_text(json.dumps(replayed, indent=1, sort_keys=True))
        res["replay"]["identical_to_main"] = True
    elif mp.exists():
        mainh = json.loads(mp.read_text())
        bad = [k for k in sorted(set(mainh) | set(replayed)) if mainh.get(k) != replayed.get(k)]
        res["replay"]["identical_to_main"] = not bad
        res["replay"]["differing"] = bad[:20]
    else:
        res["replay"]["identical_to_main"] = None

    if not a.skip_suites:
        res["suites"] = run_suites()
    res["size"] = size()

    outp = Path(a.out) if a.out else HERE / "results" / f"{branch}.json"
    outp.parent.mkdir(exist_ok=True)
    outp.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")

    # ---- short table
    print(f"\nbranch {branch} @ {res['commit']}")
    print("channel      crosses  differs  escaped  first differing paths")
    for k, v in ch.items():
        print(f"{k:12} {str(v['crosses']):8} {str(v['differs']):8} {str(bool(v['exception_escaped'])):8} "
              f"{', '.join(v['diff_paths'][:3])[:70]}")
    print("control ok:", res["control_ok"], "| main expectation met:", res.get("main_expectation_met", "n/a"))
    print("probes crosses:", " ".join(f"{k}={'?' if v is None else int(v)}" for k, v in crosses.items()))
    if "changed_vs_main" in res["probes"]:
        print("probes changed vs main:", res["probes"]["changed_vs_main"] or "none")
    print("rules:", "; ".join(f"{r['rule']}={'ok' if r['holds'] else 'FAIL'}" for r in res["rules"]))
    print(f"replay: {res['replay']['n']} logs, identical to main: {res['replay']['identical_to_main']}")
    if "suites" in res:
        s = res["suites"]
        print(f"cargo: {s['cargo']['passed']} passed, {s['cargo']['failed']} failed (rc {s['cargo']['returncode']}); "
              f"python: ran {s['python']['ran']}, failures {s['python']['failures']}, errors {s['python']['errors']}")
    print("size net:", {k: v["net"] for k, v in res["size"].items()})
    print("wrote", outp)


if __name__ == "__main__":
    main()
