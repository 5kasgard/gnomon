#!/usr/bin/env python3
"""Issue #51 harness: does auto mode carry content between compartments?

Runs the eight channels, widening and the four use measures of PREREG.md
(with Amendment 1) against whatever gnomon code is checked out in this
repository, and prints one JSON object. No language model, no network.

    python3 bench/issue51/run.py [--base auto-one-compartment] [--tests]

How the session is simulated
  * Each scenario gets a fresh temp log: owner `o`; template writer `t`
    (kind model, 'write', 'verify', 'spawn') holding X and Y; reader `ry`
    holding only Y; reader `rx` holding only X. Canaries are planted only
    in X (and one Y-only canary, for widening).
  * Claude Code is simulated by writing a transcript in the format
    hook.read_turns parses, and calling hook.main([...]) with the event on
    stdin, always with --compartment Y. Tool calls go through the real MCP
    `Server` in per-session mode (as hook-driven sessions run it), through
    JSON-RPC `tools/call`; their results appear in the transcript as
    tool_use / tool_result blocks, the way Claude Code writes them.
  * The "model" is a deterministic echo (class Model). It knows only what
    it was given in this arm: the start context, per-message context, tool
    results and sub-agent results. In each text reply it restates, word for
    word, the canaries given since its last reply (or all it holds, or a
    named subset, where a scenario step says so; or none, in the one place a
    scenario needs a quiet turn). The same script runs for every arm: what
    differs is only what the arm's code let the model be given.
  * Leak: the X canary is findable by `ry` through the library's own read
    and search API (Memory.recall and Memory.state, as ry sees them) after
    the session. Widening: the Y-only canary is findable by `rx`.

Limits are listed in the report that accompanies results.json.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from gnomon import GateError, Memory, claim  # noqa: E402
from gnomon import hook  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from gnomon.mcp import Server  # noqa: E402

X, Y = "cx", "cy"

# Canaries: (token, sentence). The token is what the readers search for; the
# sentence is what the model restates.
CAN = {
    "x1": "The Orchid vault code is zebraquartz1101.",
    "x2": "The Falconer ledger password is zebraquartz2202.",
    "x3": "My team budget is frozen until July because of zebraquartz3303.",
    "x4": "The Basalt rollout deadline is zebraquartz4404.",
    "x5": "The Lantern escrow number is zebraquartz5505.",
    "x6": "The Marlin relay passphrase is zebraquartz6606.",
    "x7": "The Quarry gateway secret is zebraquartz7707.",
    "x8": "The Cobalt archive key is zebraquartz8808.",
    "y": "The Heron codeword is amberfalcon2204.",
    "u4": "The Tundra audit phrase is zebraquartz9909.",
}
TOKEN = {k: re.search(r"(zebraquartz\d+|amberfalcon\d+)", v).group(1) for k, v in CAN.items()}


def T(i: int) -> str:
    return f"2026-10-15T{9 + i // 60:02d}:{i % 60:02d}:00Z"


# ------------------------------------------------------------------ the world

def build(tmp: Path, template_comps=(X, Y), plant: tuple = ()) -> tuple:
    """A fresh log. `plant` lists which X canaries to plant (and how)."""
    log = str(tmp / "memory.jsonl")
    m = Memory(log)
    m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=[X, Y], name="Alex",
               record_time=T(0))
    m.register("o", "t", party="anthropic", kind="model", model="claude-test", operator="o", name="Claude",
               surface="claude-code", capabilities=["write", "verify", "spawn"], compartments=list(template_comps),
               record_time=T(1))
    m.register("o", "ry", party="o", kind="tool", operator="o", capabilities=["verify"], compartments=[Y],
               record_time=T(2))
    m.register("o", "rx", party="o", kind="tool", operator="o", capabilities=["verify"], compartments=[X],
               record_time=T(3))
    n = [10]

    def tick():
        n[0] += 1
        return T(n[0])

    def fact(key, comp, frame):
        m.remember("o", claim(CAN[key], speaker="o", frame=frame, compartment=comp, truth="true", origin="manual"),
                   record_time=tick())

    # X first, Y after, so Y is the most recent work and the default resume is Y's.
    for key in plant:
        if key == "x2":  # a frame in X, with a handoff (planted last, below, so it is the most recent)
            continue
        if key == "x3":  # the person's own sentence about their situation, recorded in X
            record_messages(m, "t", [("User", CAN["x3"]), ("Claude", "Understood.")], compartment=X,
                            bindings={"User": "o", "Claude": "t"}, record_time=tick())
            continue
        if key == "x4":  # per-message context draws on recorded turns
            record_messages(m, "t", [("Claude", CAN["x4"])], compartment=X, bindings={"Claude": "t"},
                            record_time=tick())
            continue
        fact(key, X, {"x1": "orchid", "x4": "basalt", "x5": "lantern", "x6": "marlin", "x7": "quarry",
                      "x8": "cobalt", "u4": "tundra"}[key])
    fact("y", Y, "heron")
    record_messages(m, "t", [("User", "Let us review the heron migration."),
                             ("Claude", "Sure, reviewing the heron migration notes.")], compartment=Y,
                    bindings={"User": "o", "Claude": "t"}, record_time=tick())
    m.handoff("o", frame="heron", summary="Tracking the heron migration notes for the main project.",
              compartment=Y, next=["review the migration notes"], record_time=tick())
    if "x2" in plant:
        m.handoff("o", frame="falconer", summary=CAN["x2"] + " Keep the ledger closed.", compartment=X,
                  next=["rotate the ledger password"], record_time=tick())
    return log, m


def findable(m: Memory, reader: str, token: str) -> bool:
    """Whether `reader` can find `token` through the library's own read and
    search API: recall over what it sees, or the state it sees."""
    comps = m.writers()[reader]["compartments"]
    try:
        hits = m.recall(reader, token, k=20)["hits"]
        if any(token in h.get("text", "") for h in hits):
            return True
    except GateError:
        pass
    if token in json.dumps(m.state(compartments=comps), ensure_ascii=False):
        return True
    try:
        frames = m.frames(reader)
        for f in frames:
            if token in json.dumps(m.resume(reader, f["frame"]), ensure_ascii=False):
                return True
    except GateError:
        pass
    return False


# ------------------------------------------------------------- the scripted model

class Model:
    """A deterministic echo. It knows only what it is given."""

    def __init__(self):
        self.known: dict = {}      # key -> sentence, in the order given
        self.via: dict = {}        # key -> how it first reached the model
        self.given: list = []      # keys given since the last reply

    def give(self, text: str, via: str = "?") -> list:
        got = []
        for key, sentence in CAN.items():
            if TOKEN[key] in text:
                if key not in self.known:
                    self.known[key] = sentence
                    self.via[key] = via
                if key not in self.given:
                    self.given.append(key)
                got.append(key)
        return got

    def reply(self, restate="given", quiet: bool = False, tag: str = "") -> str:
        if quiet:
            keys = []
        elif restate == "given":
            keys = list(self.given)
        elif restate == "all":
            keys = list(self.known)
        else:  # a named subset: only what it actually holds
            keys = [k for k in restate if k in self.known]
        self.given = []
        return tag + "Noted." + "".join(" " + self.known[k] for k in keys)


# --------------------------------------------------------- the simulated session

class Session:
    def __init__(self, tmp: Path, log: str, sid: str, template: str = "t", hook_args: tuple = (),
                 compartment: str = Y):
        self.log, self.sid, self.tmp = log, sid, tmp
        self.transcript = tmp / f"{sid}.jsonl"
        self.transcript.write_text("")
        self.args = ["--log", log, "--writer", template, "--compartment", compartment, *hook_args]
        self.model = Model()
        self.server = Server(log, template, per_session=True)
        self.server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                            "params": {"protocolVersion": "2025-11-25"}})
        self.n = 0
        self.token = None
        self.stderr: list = []
        self.exit_codes: list = []
        self.tool_log: list = []   # (name, isError, text)

    # -- the host
    def _append(self, entry: dict) -> None:
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def hook(self, event: str, **extra) -> str:
        payload = {"hook_event_name": event, "session_id": self.sid, "transcript_path": str(self.transcript),
                   "cwd": str(self.tmp), **extra}
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = hook.main(self.args)
        finally:
            sys.stdin = old
        self.exit_codes.append(rc)
        if err.getvalue().strip():
            self.stderr.append(f"{event}: {err.getvalue().strip()[:300]}")
        return out.getvalue()

    def _context_from(self, out: str) -> str:
        try:
            return json.loads(out)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError, TypeError):
            return ""

    # -- steps
    def start(self, source: str = "startup") -> str:
        ctx = self._context_from(self.hook("SessionStart", source=source))
        self.model.give(ctx, "start_context")
        m = re.search(r"token ([0-9a-f]{32})", ctx)
        self.token = m.group(1) if m else None
        return ctx

    def user(self, text: str, per_message: bool = False) -> None:
        if per_message:
            ctx = self._context_from(self.hook("UserPromptSubmit", prompt=text))
            self.model.give(ctx, "per_message_context")
        self._append({"type": "user", "message": {"role": "user", "content": text}})

    def call(self, name: str, args: dict, server: Server = None, transcript: Path = None, model: Model = None,
             sidechain: bool = False) -> str:
        """A tool call by the model: tool_use, the server's result, tool_result."""
        server = server or self.server
        model = model or self.model
        transcript = transcript or self.transcript
        self.n += 1
        tid = f"toolu_{self.n:04d}"
        extra = {"isSidechain": True} if sidechain else {}
        self._write(transcript, {"type": "assistant", "message": {"role": "assistant", "model": "claude-test",
                    "content": [{"type": "tool_use", "id": tid, "name": f"mcp__gnomon__{name}", "input": args}]},
                    **extra})
        reply = server.handle({"jsonrpc": "2.0", "id": 100 + self.n, "method": "tools/call",
                               "params": {"name": name, "arguments": args}})
        res = reply["result"]
        text = "\n".join(b["text"] for b in res["content"] if b.get("type") == "text")
        self.tool_log.append((name, bool(res.get("isError")), text))
        self._write(transcript, {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": bool(res.get("isError")),
             "content": [{"type": "text", "text": text}]}]}, **extra})
        model.give(text, f"tool:{name}")
        return text

    def _write(self, path: Path, entry: dict) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def attach(self) -> bool:
        """What the model does with a token in its context: attach with it."""
        if not self.token:
            return False
        text = self.call("attach", {"token": self.token})
        return text.startswith("Attached")

    def say(self, restate="given", quiet: bool = False, tag: str = "") -> str:
        text = self.model.reply(restate, quiet, tag)
        self._append({"type": "assistant", "message": {"role": "assistant", "model": "claude-test",
                                                       "content": [{"type": "text", "text": text}]}})
        return text

    def stop(self) -> None:
        self.hook("Stop")

    def end(self) -> None:
        self.hook("SessionEnd")

    def compact(self, summary: str) -> None:
        self.hook("PreCompact")
        self._append({"type": "user", "isCompactSummary": True,
                      "message": {"role": "user", "content": "This session is being continued from a previous "
                                                              "conversation. Summary: " + summary}})


def instance_of(m: Memory, sid: str, parent: str = "t") -> str:
    for wid, w in m.writers().items():
        if w.get("instance_of") == parent and w.get("session") == sid:
            return wid
    return ""


# ---------------------------------------------------------------- the channels

def ch1(s: Session):
    s.start(); s.attach()
    s.user("What is the Orchid vault code? Look it up.")
    s.call("recall", {"query": "Orchid vault code"})
    s.say(); s.stop(); s.end()


def ch2(s: Session):
    s.start(); s.attach()
    s.user("Continue the work from where we left off.")
    s.say(); s.stop(); s.end()


def ch3(s: Session):
    s.start(); s.attach()
    s.user("Plan the next quarter for me.")
    s.say(); s.stop(); s.end()


def ch4(s: Session):
    s.start(); s.attach()
    s.user("When is the Basalt rollout deadline?", per_message=True)
    s.say(); s.stop(); s.end()


def ch5(s: Session):
    s.start(); s.attach()
    s.user("Check the Lantern escrow notes quietly and say nothing yet.")
    s.call("recall", {"query": "Lantern escrow number"})
    s.say(quiet=True); s.stop()
    s.compact("The model looked up the Lantern escrow notes: " + CAN["x5"])
    s.user("Recap what you found earlier.")
    s.say(restate="all"); s.stop(); s.end()


def ch6(s: Session):
    s.start(); s.attach()
    s.user("Delegate: ask a helper to look up the Marlin relay passphrase.")
    # The sub-agent shares the session's Gnomon connection (and so its attachment).
    agent = Model()
    atr = s.tmp / f"{s.sid}-agent.jsonl"
    atr.write_text("")
    s.call("recall", {"query": "Marlin relay passphrase"}, server=s.server, transcript=atr, model=agent,
           sidechain=True)
    final = agent.reply()
    s._write(atr, {"type": "assistant", "isSidechain": True,
                   "message": {"role": "assistant", "content": [{"type": "text", "text": final}]}})
    s.hook("SubagentStop", agent_transcript_path=str(atr), agent_id="a1", agent_type="general-purpose")
    s.n += 1
    tid = f"toolu_task{s.n}"
    s._append({"type": "assistant", "message": {"role": "assistant", "model": "claude-test", "content": [
        {"type": "tool_use", "id": tid, "name": "Task", "input": {"prompt": "look it up"}}]}})
    s._append({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": final}]}]}})
    s.model.give(final, "subagent_result")
    s.say(); s.stop(); s.end()


def ch7(s: Session):
    s.start()  # the model does not call `attach`
    s.user("What is the Quarry gateway secret? Look it up.")
    s.call("recall", {"query": "Quarry gateway secret"})
    s.say(); s.stop(); s.end()


def ch8(s: Session):
    s.start(); s.attach()
    s.user("Read the Cobalt archive notes, then hand off before you stop.")
    s.call("recall", {"query": "Cobalt archive key"})
    summary = "Notes for the next session." + "".join(" " + v for v in s.model.known.values())
    s.call("handoff", {"frame": "heron", "summary": summary, "compartment": Y})
    s.say(quiet=True); s.stop(); s.end()   # the only road here is the write itself


CHANNELS = {
    1: ("MCP recall of X content", ch1, ("x1",), "x1", ()),
    2: ("start context: resume packet of a frame in X", ch2, ("x2",), "x2", ()),
    3: ("start context: the person's situation recorded in X", ch3, ("x3",), "x3", ()),
    4: ("per-message context drawn from X", ch4, ("x4",), "x4", ("--per-message", "800")),
    5: ("restated after compaction", ch5, ("x5",), "x5", ()),
    6: ("sub-agent quotes X content", ch6, ("x6",), "x6", ()),
    7: ("MCP used without attach", ch7, ("x7",), "x7", ()),
    8: ("write-back through handoff", ch8, ("x8",), "x8", ()),
}


def run_channel(n: int) -> dict:
    name, fn, plant, key, extra = CHANNELS[n]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log, m = build(tmp, plant=plant)
        s = Session(tmp, log, f"s-{n}", hook_args=extra)
        err = None
        try:
            fn(s)
        except Exception as e:  # a harness or arm failure must be visible, not silent
            err = f"{type(e).__name__}: {e}"
        leak = findable(m, "ry", TOKEN[key])
        widening = findable(m, "rx", TOKEN["y"])
        return {"name": name, "leak": leak, "canary_given_to_model": key in s.model.known, "canary_via": s.model.via.get(key),
                "widening": widening, "hook_exit_codes": sorted(set(s.exit_codes)),
                "hook_stderr": s.stderr[:3], "error": err,
                "episodes_in_y": sum(1 for e in m.state(compartments=[Y])["state"]["episodes"].values()),
                "instance": instance_of(m, s.sid)}


# ------------------------------------------------------------- widening, use

def run_widening() -> dict:
    """One session that draws on both X and Y, and restates both."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log, m = build(tmp, plant=("x1",))
        s = Session(tmp, log, "s-w")
        s.start(); s.attach()
        s.user("What are the Orchid vault code and the Heron codeword?")
        s.call("recall", {"query": "Orchid vault code"})
        s.call("recall", {"query": "Heron codeword"})
        s.say(); s.stop(); s.end()
        return {"widening": findable(m, "rx", TOKEN["y"]), "rx_sees_y_canary": findable(m, "rx", TOKEN["y"]),
                "ry_sees_x_canary": findable(m, "ry", TOKEN["x1"]), "hook_stderr": s.stderr[:3]}


def recorded_in_y(m: Memory, text: str) -> bool:
    """Whether `text` is recorded word for word in a Y episode, in a turn."""
    for e in m.state(compartments=[Y])["state"]["episodes"].values():
        c = e["body"]["content"]
        for t in e["body"].get("turns", []):
            if c[t["start"]:t["end"]] == text:
                return True
    return False


def run_u1() -> dict:
    """A single-compartment template (Y only) records as before."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log, m = build(tmp, template_comps=(Y,))
        s = Session(tmp, log, "s-u1")
        ctx = s.start()
        attached = s.attach()
        s.user("U1 question: what is the Heron codeword?")
        s.call("recall", {"query": "Heron codeword"})
        reply = s.say()
        s.stop(); s.end()
        ok_user = recorded_in_y(m, "U1 question: what is the Heron codeword?")
        ok_model = recorded_in_y(m, reply)
        return {"u1": bool(ctx and attached and ok_user and ok_model and TOKEN["y"] in reply),
                "context_given": bool(ctx), "attached": attached, "user_turn_recorded": ok_user,
                "model_turn_recorded": ok_model, "u1_hook_stderr": s.stderr[:3]}


def run_u2_u3() -> dict:
    """A two-compartment template; a session of which some turns draw on X.
    u2: the share of the turns that draw only on Y that are recorded word for
    word in Y. u3: the session can recall Y content."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log, m = build(tmp, plant=("x1",))
        s = Session(tmp, log, "s-u2")
        s.start(); s.attach()
        only_y: list = []
        # T1: draws only on Y
        q = "T1 question: what is the Heron codeword?"
        s.user(q); only_y.append(q)
        rec = s.call("recall", {"query": "Heron codeword"})
        u3 = TOKEN["y"] in rec
        only_y.append(s.say(restate=["y"], tag="T1: ")); s.stop()
        # T2: draws on X
        s.user("T2 question: what is the Orchid vault code?")
        s.call("recall", {"query": "Orchid vault code"})
        s.say(); s.stop()
        # T3, T4: draw only on Y again
        q = "T3 question: say the Heron codeword again."
        s.user(q); only_y.append(q)
        only_y.append(s.say(restate=["y"], tag="T3: ")); s.stop()
        q = "T4 question: anything else?"
        s.user(q); only_y.append(q)
        only_y.append(s.say(quiet=True, tag="T4: ")); s.stop()
        s.end()
        got = [recorded_in_y(m, t) for t in only_y]
        # The pure case: nothing from X at all.
        (tmp / "p").mkdir()
        log2, m2 = build(tmp / "p", plant=("x1",))
        s2 = Session(tmp, log2, "s-u2p")
        s2.start(); s2.attach()
        pure: list = []
        for i in (1, 2, 3):
            q = f"P{i} question: say the Heron codeword."
            s2.user(q); pure.append(q)
            s2.call("recall", {"query": "Heron codeword"})
            pure.append(s2.say(restate=["y"], tag=f"P{i}: ")); s2.stop()
        s2.end()
        got_p = [recorded_in_y(m2, t) for t in pure]
        return {"u2": round(sum(got) / len(got), 3), "u2_turns": f"{sum(got)}/{len(got)}",
                "u2_pure": round(sum(got_p) / len(got_p), 3), "u2_pure_turns": f"{sum(got_p)}/{len(got_p)}",
                "u3": u3, "u2_hook_stderr": (s.stderr + s2.stderr)[:3]}


def run_u4() -> dict:
    """The owner, outside auto mode (an MCP server run as the template),
    writes a sentence quoting X content into Y."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log, m = build(tmp, plant=("u4",))
        srv = Server(log, "t")   # not per-session: it acts as the template
        srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        def call(name, args):
            r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": args}})["result"]
            return bool(r.get("isError")), "\n".join(b["text"] for b in r["content"])
        err1, text = call("recall", {"query": "Tundra audit phrase"})
        saw = TOKEN["u4"] in text
        err2, _ = call("remember", {"statement": "Copied on purpose: " + CAN["u4"], "frame": "heron",
                                    "compartment": Y, "truth": "true"})
        ok = saw and not err2 and findable(m, "ry", TOKEN["u4"])
        return {"u4": ok, "read_x": saw, "write_y_refused": err2}


def run_u5() -> dict:
    """A template used by auto mode (the hook has run for it) and also by a
    standalone per-session MCP server with no token: does the standalone
    server's first tool call succeed?"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log, m = build(tmp)
        s = Session(tmp, log, "s-u5-auto")
        s.start()  # the hook runs for template `t`
        srv = Server(log, "t", per_session=True)   # another process, no token
        srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "recall", "arguments": {"query": "Heron codeword"}}})["result"]
        text = "\n".join(b["text"] for b in r["content"] if b.get("type") == "text")
        return {"u5": not r.get("isError"), "u5_first_call": text[:120]}


# ------------------------------------------------------------------ size, tests

def git(*a, check=False):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=check)


def size(base: str) -> dict:
    head = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    r = git("diff", "--numstat", f"{base}..HEAD", "--", ".", ":(exclude)python/tests", ":(exclude)bench")
    added = deleted = 0
    files = {}
    for ln in r.stdout.splitlines():
        a, d, f = ln.split("\t", 2)
        if a == "-":
            continue
        added += int(a); deleted += int(d); files[f] = [int(a), int(d)]
    return {"base": base, "head": head, "added": added, "deleted": deleted, "changed": added + deleted,
            "net": added - deleted, "files": files}


def tests() -> dict:
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "python/tests"], cwd=ROOT,
                       capture_output=True, text=True, timeout=3000,
                       env=dict(os.environ, PYTHONPATH=str(ROOT / "python")))
    out = r.stderr
    m = re.search(r"Ran (\d+) tests? in", out)
    ran = int(m.group(1)) if m else 0
    fm = re.search(r"FAILED \((.*)\)", out)
    failures = errors = 0
    if fm:
        for part in fm.group(1).split(","):
            k, _, v = part.strip().partition("=")
            if k == "failures":
                failures = int(v)
            elif k == "errors":
                errors = int(v)
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", out, re.M)))
    return {"ran": ran, "failures": failures, "errors": errors, "passed": ran - failures - errors,
            "ok": r.returncode == 0, "failed": sorted({b for _, b in failed})}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="auto-one-compartment")
    ap.add_argument("--tests", action="store_true", help="also run the unittest suite")
    ap.add_argument("--no-size", action="store_true")
    a = ap.parse_args()
    out: dict = {"branch": git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                 "commit": git("rev-parse", "--short", "HEAD").stdout.strip()}
    out["channels"] = {str(n): run_channel(n) for n in CHANNELS}
    out["widening_scenario"] = run_widening()
    out["widening"] = bool(out["widening_scenario"]["widening"] or any(c["widening"] for c in out["channels"].values()))
    use = {}
    use.update(run_u1()); use.update(run_u2_u3()); use.update(run_u4()); use.update(run_u5())
    out["use"] = use
    out["leaks"] = [n for n, c in out["channels"].items() if c["leak"]]
    if not a.no_size:
        out["size"] = size(a.base)
    if a.tests:
        out["tests"] = tests()
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
