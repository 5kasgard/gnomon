"""Issue #51: in auto mode a session's writer holds only the compartment it is
recorded in, so nothing it reads through Gnomon can come from another; an
per-session MCP server started with --require-attach refuses use until attached.
Standard library only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(HERE))

from gnomon import Memory, claim  # noqa: E402
from test_mcp import Proc  # noqa: E402

CANARY = "zephyrquartz-canary-in-x"
OKAY = "marmotlantern-fact-in-y"


def t(minute: int) -> str:
    return f"2026-10-16T10:{minute:02d}:00Z"


class OneCompartment(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.log = str(d / "memory.jsonl")
        self.transcript = d / "session.jsonl"
        self.transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Hello there."}}) + "\n")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                   compartments=["cx", "cy"], record_time=t(0))
        for w in ("claude", "other"):  # two templates, each holding both compartments
            m.register("john", w, party="anthropic", kind="model", model="claude-opus-5-5", operator="john",
                       name=w, surface="claude-code", capabilities=["write", "verify", "spawn"],
                       compartments=["cx", "cy"], record_time=t(1))
        m.register("john", "reader", party="rita", kind="human", name="Rita", capabilities=["write"],
                   compartments=["cy"], record_time=t(2))
        for comp, text in (("cx", f"The vault code is {CANARY}."), ("cy", f"The lab bench is {OKAY}.")):
            m.remember("john", claim(text, speaker="john", frame=f"f-{comp}", compartment=comp, stance="decided",
                                     truth="true", origin="manual"), record_time=t(3))

    def tearDown(self):
        self.dir.cleanup()

    def hook(self, event: str, session: str = "s-1", *flags: str, writer: str = "claude") -> subprocess.CompletedProcess:
        payload = {"hook_event_name": event, "session_id": session, "transcript_path": str(self.transcript),
                   "cwd": self.dir.name}
        return subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", writer, *flags],
                              input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, PYTHONPATH=str(PKG)))

    def instances(self, session: str = "s-1", parent: str = "claude") -> dict:
        w = Memory(self.log).writers()
        return {k: v for k, v in w.items() if v.get("instance_of") == parent and str(v.get("session")).split("~")[0] == session}

    def attach(self, started: subprocess.CompletedProcess, writer: str = "claude") -> Proc:
        ctx = json.loads(started.stdout)["hookSpecificOutput"]["additionalContext"]
        token = ctx.split("token ")[1].split(",")[0]
        s = Proc("--log", self.log, "--writer", writer, "--per-session", "--require-attach")
        self.addCleanup(s.close)
        self.assertFalse(s.call("attach", {"token": token})["isError"])
        return s

    # 1
    def test_the_session_holds_only_the_recorded_compartment(self):
        r = self.hook("SessionStart", "s-1", "--compartment", "cy")
        self.assertEqual(r.returncode, 0, r.stderr)
        inst = self.instances()
        self.assertEqual(len(inst), 1)
        self.assertEqual(list(inst.values())[0]["compartments"], ["cy"])
        self.assertNotIn(CANARY, r.stdout)
        s = self.attach(r)
        self.assertNotIn(CANARY, json.dumps(s.call("recall", {"query": "vault code"})))
        self.assertIn(OKAY, json.dumps(s.call("recall", {"query": "lab bench"})))
        r = self.hook("Stop", "s-1", "--compartment", "cy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        m = Memory(self.log)
        self.assertIn("Hello there", json.dumps(m.recall("reader", "Hello there")))  # recorded, and in Y
        for q in ("vault code", CANARY, "zephyrquartz"):
            self.assertNotIn(CANARY, json.dumps(m.recall("reader", q)))
        st = json.dumps(m.state(compartments=["cy"]))
        self.assertNotIn(CANARY, st)
        self.assertIn(OKAY, st)

    # 2
    def test_require_attach_refuses_unattached_use(self):
        self.assertEqual(self.hook("SessionStart", "s-1", "--compartment", "cy").returncode, 0)
        n = len(Memory(self.log).writers())
        s = Proc("--log", self.log, "--writer", "claude", "--per-session", "--require-attach")
        self.addCleanup(s.close)
        for tool, args in (("recall", {"query": "vault code"}), ("whoami", {}), ("frames", {})):
            r = s.call(tool, args)
            self.assertTrue(r["isError"], tool)
            self.assertIn("attach", json.dumps(r))
        self.assertEqual(len(Memory(self.log).writers()), n)  # no instance was started

    def test_a_standalone_per_session_server_still_works_after_the_hook_ran(self):
        self.assertEqual(self.hook("SessionStart", "s-1", "--compartment", "cy").returncode, 0)
        s = Proc("--log", self.log, "--writer", "claude", "--per-session")  # as on main: no flag
        self.addCleanup(s.close)
        r = s.call("whoami", {})
        self.assertFalse(r["isError"], r)
        w = r["structuredContent"]["writer"]
        self.assertNotIn(w, self.instances())  # its own instance, not the hook's
        self.assertEqual(Memory(self.log).writers()[w]["instance_of"], "claude")
        # the flag without --per-session changes nothing: one fixed writer
        f = Proc("--log", self.log, "--writer", "john", "--require-attach")
        self.addCleanup(f.close)
        self.assertFalse(f.call("whoami", {})["isError"])

    # 3
    def test_a_resumed_session_with_a_wider_instance_gets_a_narrow_one(self):
        from gnomon.session import instance_for
        old = instance_for(self.m, "claude", "s-old", surface="claude-code")  # the old way: holds cx and cy
        self.assertEqual(sorted(self.m.writers()[old]["compartments"]), ["cx", "cy"])
        r = self.hook("Stop", "s-old", "--compartment", "cy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("holds only 'cy'", r.stderr)
        inst = self.instances("s-old")
        self.assertEqual(len(inst), 2)
        new = [k for k, v in inst.items() if v["compartments"] == ["cy"]]
        self.assertEqual(len(new), 1)
        self.assertEqual(self.m.writers()[old]["compartments"], ["cx", "cy"])  # untouched
        self.assertIn("Hello there", json.dumps(Memory(self.log).recall("reader", "Hello there")))  # recorded, in cy
        for q in ("vault code", CANARY):
            self.assertNotIn(CANARY, json.dumps(Memory(self.log).recall("reader", q)))
        # later hooks reuse the narrow one, quietly
        r = self.hook("Stop", "s-old", "--compartment", "cy")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(len(self.instances("s-old")), 2)
        # the session start binds the narrow one
        r = self.hook("SessionStart", "s-old", "--compartment", "cy")
        self.assertIn(f"[{new[0]}]", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn(CANARY, r.stdout)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        srv = Proc("--log", self.log, "--writer", "claude", "--per-session", "--require-attach", "--session", "s-old")
        self.addCleanup(srv.close)  # a server that knows the host's session id can redeem the token
        self.assertFalse(srv.call("attach", {"token": ctx.split("token ")[1].split(",")[0]})["isError"])
        self.assertEqual(srv.call("whoami", {})["structuredContent"]["writer"], new[0])

    # 4
    def test_a_handoff_into_y_cannot_carry_x(self):
        s = self.attach(self.hook("SessionStart", "s-1", "--compartment", "cy"))
        seen = json.dumps(s.call("recall", {"query": "vault code"})) + json.dumps(s.call("context", {"query": "vault code"}))
        self.assertNotIn(CANARY, seen)  # all the session can know of X is nothing
        summary = "Wrote up what I could find about the vault."
        h = s.call("handoff", {"frame": "f-Y", "summary": summary, "compartment": "cy"})
        self.assertFalse(h["isError"], h)
        res = json.dumps(Memory(self.log).resume("reader", "f-Y"))
        self.assertIn(summary, res)
        self.assertNotIn(CANARY, res)
        x = s.call("handoff", {"frame": "f-X", "summary": "leak", "compartment": "cx"})
        self.assertTrue(x["isError"], x)  # and it cannot write into X either
        w = list(self.instances())[0]
        self.assertEqual(w, s.call("whoami", {})["structuredContent"]["writer"])


if __name__ == "__main__":
    unittest.main()
