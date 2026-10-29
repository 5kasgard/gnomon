"""M6: who is who. Standard library only.

Every session or agent is its own writer; the model it runs and the person
it acts for are on record; a name in a recording is only a name until it is
bound to a writer; agreement between sessions of one model counts once.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from gnomon import VIEWS, GateError, Memory, claim, proofs, title  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from test_mcp import Proc  # noqa: E402

OPUS = "claude-opus-5-5"


def t(minute: int) -> str:
    return f"2026-10-16T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "memory.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", capabilities=["owner"], compartments=["lab"],
                   name="John Doe", record_time=t(0))
        m.register("john", "claude", party="anthropic", kind="model", model=OPUS, operator="john", name="Claude",
                   surface="claude-code", capabilities=["write", "verify", "spawn"], compartments=["lab"],
                   record_time=t(1))
        for i, sess in ((1, "aaaa"), (2, "bbbb")):
            m.register("claude", f"claude-{i}", party="anthropic", kind="model", model=OPUS, operator="john",
                       name="Claude", surface="claude-code", session=sess, capabilities=["write"],
                       compartments=["lab"], record_time=t(2))
        m.register("john", "gpt", party="openai", kind="model", model="gpt-6", operator="john", name="GPT",
                   capabilities=["write"], compartments=["lab"], record_time=t(3))

    def tearDown(self):
        self.dir.cleanup()

    def ask(self, program: str) -> set:
        r = self.m.query("john", program)
        self.assertEqual(proofs.check(r, program, VIEWS, self.log), [], program)
        return {tuple(a["values"]) for a in r["answers"]}


class Identity(Fixture):
    def test_titles_tell_sessions_apart(self):
        ws = self.m.state()["state"]["writers"]
        a, b = title(ws, "claude-1"), title(ws, "claude-2")
        self.assertEqual(a, "Claude (claude-opus-5-5, claude-code, session aaaa) [claude-1] for John Doe [john]")
        self.assertNotEqual(a, b)
        self.assertEqual(title(ws, "john"), "John Doe [john]")
        self.assertIn("not a registered writer", title(ws, "nobody"))

    def test_views_with_checked_proofs(self):
        self.assertEqual(self.ask("?- same_model(\"claude-1\", W)."), {("claude",), ("claude-2",)})
        self.assertEqual(self.ask("?- instance_of(W, \"claude\")."), {("claude-1",), ("claude-2",)})
        self.assertEqual(self.ask("?- acts_for(\"claude-2\", H)."), {("john",)})
        self.assertEqual(self.ask("?- identity(\"claude-1\", \"session\", S)."), {("aaaa",)})

    def test_agreement_counts_once_per_model(self):
        body = claim("The build is green", speaker="claude-1", frame="ci", compartment="lab", truth="true",
                     origin="observed", confidence=6000)
        cid = self.m.remember("claude-1", body, record_time=t(4))
        self.m.remember("claude-2", body, record_time=t(5))
        q = "?- independently_asserted(C)."
        self.assertEqual(self.ask(q), set())
        self.assertEqual(self.ask(f"?- asserted_group(\"{cid}\", G)."), {("model:claude-opus-5-5",)})
        self.m.remember("gpt", body, record_time=t(6))
        self.assertEqual(self.ask(q), {(cid,)})
        self.assertEqual(self.m.state()["state"]["claims"][cid]["independent_groups"],
                         ["model:claude-opus-5-5", "model:gpt-6"])

    def test_bound_names_resolve_and_unbound_ones_do_not(self):
        ep = record_messages(self.m, "claude-1", [("John", "Use Rust."), ("Claude", "Rust it is.")],
                             compartment="lab", bindings={"Claude": "claude-1", "John": "john"}, record_time=t(4))
        loose = record_messages(self.m, "claude-2", [("Claude", "Rust it is.")], compartment="lab",
                                record_time=t(5))
        content = self.m.state()["state"]["episodes"][ep]["body"]["content"]
        start = content.index("Rust it is.")

        def quote(e, s, conf):
            return claim("Rust it is.", speaker="Claude", frame="f", compartment="lab", truth="true",
                         origin="direct", confidence=conf, source_episode=e, quote="Rust it is.", quote_start=s)
        mine = self.m.remember("claude-1", quote(ep, start, 9500), record_time=t(6))
        with self.assertRaises(GateError) as e:
            self.m.remember("claude-2", quote(ep, start, 9500), record_time=t(7))
        self.assertEqual(e.exception.code, "E_ATTRIBUTION")
        lc = self.m.state()["state"]["episodes"][loose]["body"]["content"]
        nobody = self.m.remember("claude-2", quote(loose, lc.index("Rust"), 7000), record_time=t(8))
        self.assertEqual(self.ask(f"?- speaks_as(\"{mine}\", W)."), {("claude-1",)})
        self.assertEqual(self.ask(f"?- speaks_as(\"{nobody}\", W)."), set())
        hits = self.m.recall("john", "rust", kinds=["turns"], k=5)["hits"]
        self.assertEqual({(h["episode"], h["speaker_is"]) for h in hits if h["speaker"] == "Claude"},
                         {(ep, "claude-1"), (loose, None)})


class PerSessionServer(Fixture):
    """Two sessions configured identically still become two writers."""

    def test_each_process_is_its_own_writer(self):
        a = Proc("--log", self.log, "--writer", "claude", "--per-session")
        b = Proc("--log", self.log, "--writer", "claude", "--per-session", "--session", "host-session-42")
        try:
            wa = a.call("whoami", {})["structuredContent"]
            wb = b.call("whoami", {})["structuredContent"]
            self.assertNotEqual(wa["writer"], wb["writer"])
            self.assertEqual((wa["model"], wa["operator"], wa["group"]), (OPUS, "john", f"model:{OPUS}"))
            self.assertEqual(wb["session"], "host-session-42")
            self.assertNotIn("spawn", wa["capabilities"])
            r = a.call("record_conversation", {
                "messages": [{"speaker": "John", "text": "Let's use Rust for the core."},
                             {"speaker": "Claude", "text": "We will use Rust for the core."}],
                "compartment": "lab", "frame": "stack", "me": "Claude", "operator_name": "John"})
            self.assertFalse(r["isError"], r)
            ep = r["structuredContent"]["episode"]
            self.assertEqual(self.m.state()["state"]["episodes"][ep]["body"]["bindings"],
                             {"Claude": wa["writer"], "John": "john"})
            self.assertEqual(self.ask("?- instance_of(W, \"claude\")."),
                             {("claude-1",), ("claude-2",), (wa["writer"],), (wb["writer"],)})
        finally:
            a.close()
            b.close()

    def test_per_session_needs_spawn(self):
        import subprocess
        import os
        env = dict(os.environ, PYTHONPATH=str(HERE.parent))
        r = subprocess.run([sys.executable, "-m", "gnomon.mcp", "--log", self.log, "--writer", "claude-1",
                            "--per-session"], capture_output=True, text=True, env=env, input="", timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("spawn", r.stderr)


if __name__ == "__main__":
    unittest.main()


class ReviewM6(unittest.TestCase):
    """Confirmed findings of the M6 review that need the whole stack."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "memory.jsonl")
        m = self.m = Memory(self.log)
        # Version 6: these tests spawn instances whose operator the parent cannot see.
        m.append("root", "register", {"writer": "root", "party": "root", "kind": "human", "capabilities": ["owner"],
                                      "name": "Root Admin", "compartments": ["private", "lab"],
                                      "schema_version": 6}, t(0))
        # A reader in lab only, and an owner-registered model it can see.
        m.register("root", "reader", party="reader", kind="human", capabilities=["write"], compartments=["lab"],
                   record_time=t(1))
        m.register("root", "op", party="op", kind="human", capabilities=["write"], compartments=["private"],
                   name="Secret Operator", record_time=t(1))
        m.register("root", "claude", party="anthropic", kind="model", model=OPUS, operator="op", name="Claude",
                   capabilities=["write", "spawn"], compartments=["lab"], record_time=t(2))

    def tearDown(self):
        self.dir.cleanup()

    def test_instance_of_is_only_for_instances(self):
        # Finding 7: an owner-registered writer is nobody's instance, even
        # for a reader who cannot see the owner.
        q = "?- instance_of(W, P)."
        r = self.m.query("reader", q)
        self.assertEqual(r["answers"], [])
        self.m.register("claude", "claude-1", party="anthropic", kind="model", model=OPUS, operator="op",
                        name="Claude", capabilities=["write"], compartments=["lab"], session="s1", record_time=t(3))
        r = self.m.query("reader", q)
        self.assertEqual([a["values"] for a in r["answers"]], [["claude-1", "claude"]])
        self.assertEqual(proofs.check(r, q, VIEWS, self.log), [])

    def test_forged_identity_facts_are_caught(self):
        self.m.register("claude", "claude-1", party="anthropic", kind="model", model=OPUS, operator="op",
                        name="Claude", capabilities=["write"], compartments=["lab"], session="s1", record_time=t(3))
        q = "?- group(W, G)."
        r = self.m.query("reader", q)
        self.assertEqual(proofs.check(r, q, VIEWS, self.log), [])
        for node in r["nodes"].values():
            if node["fact"][:2] == ["group", "claude-1"]:
                node["fact"][2] = "model:independent-looking"
        self.assertTrue(proofs.check(r, q, VIEWS, self.log))

    def test_whoami_names_only_what_the_writer_can_see(self):
        s = Proc("--log", self.log, "--writer", "claude", "--per-session", "--session", "resume-me")
        try:
            out = s.call("whoami", {})
            self.assertNotIn("Secret Operator", out["content"][0]["text"])
            first = out["structuredContent"]["writer"]
            r = s.call("record_conversation", {"messages": [{"speaker": "Claude", "text": "Hi."}],
                                               "compartment": "lab", "frame": "f", "me": "Claude",
                                               "operator_name": "Claude"})
            self.assertTrue(r["isError"])
        finally:
            s.close()
        # Finding 9: the same session, started again, carries on as itself.
        s = Proc("--log", self.log, "--writer", "claude", "--per-session", "--session", "resume-me")
        try:
            again = s.call("whoami", {})
            self.assertFalse(again["isError"], again)
            self.assertEqual(again["structuredContent"]["writer"], first)
        finally:
            s.close()
