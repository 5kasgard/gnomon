"""Auto mode: Claude Code hooks record every conversation and bind every
session to its own writer. Standard library only."""

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
from gnomon.hook import read_turns, settings  # noqa: E402
from test_mcp import Proc  # noqa: E402


def t(minute: int) -> str:
    return f"2026-10-16T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


def line(kind: str, content, **extra) -> str:
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}, **extra}) + "\n"


class Auto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.log = str(d / "memory.jsonl")
        self.transcript = d / "session.jsonl"
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                   compartments=["lab"], record_time=t(0))
        m.register("john", "claude", party="anthropic", kind="model", model="claude-opus-5-5", operator="john",
                   name="Claude", surface="claude-code", capabilities=["write", "verify", "spawn"],
                   compartments=["lab"], record_time=t(1))
        m.remember("john", claim("Use SQLite.", speaker="john", frame="db", compartment="lab", stance="decided",
                                 truth="true", origin="manual"), record_time=t(2))

    def tearDown(self):
        self.dir.cleanup()

    def hook(self, event: str, session: str = "s-1", **extra) -> subprocess.CompletedProcess:
        payload = {"hook_event_name": event, "session_id": session, "transcript_path": str(self.transcript),
                   "cwd": self.dir.name, **extra}
        return subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "claude"],
                              input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, PYTHONPATH=str(PKG)))

    def writers(self):
        return self.m.state()["state"]["writers"]

    def test_session_start_binds_and_resumes(self):
        self.transcript.write_text("")
        r = self.hook("SessionStart", source="startup")
        self.assertEqual(r.returncode, 0, r.stderr)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        w = [k for k, v in self.writers().items() if v.get("session") == "s-1"]
        self.assertEqual(len(w), 1)
        self.assertIn(f"[{w[0]}]", ctx)
        self.assertIn("Resuming 'db'", ctx)
        token = ctx.split("token ")[1].split(",")[0]
        s = Proc("--log", self.log, "--writer", "claude", "--per-session")
        try:
            r = s.call("attach", {"token": token})
            self.assertFalse(r["isError"], r)
            self.assertEqual(s.call("whoami", {})["structuredContent"]["writer"], w[0])
        finally:
            s.close()
        s = Proc("--log", self.log, "--writer", "claude", "--per-session")
        try:
            self.assertTrue(s.call("attach", {"token": token})["isError"])  # single use
        finally:
            s.close()
        # The same session, resumed later, keeps its writer.
        self.hook("SessionStart", source="resume")
        self.assertEqual([k for k, v in self.writers().items() if v.get("session") == "s-1"], w)

    def test_the_persons_situation_is_pushed_at_session_start(self):
        self.transcript.write_text(
            line("user", "Our team budget is frozen until July. Can you plan an offsite?") +
            line("assistant", "I can plan a low-cost one. I think a park picnic works."))
        self.assertEqual(self.hook("Stop").returncode, 0)
        r = self.hook("SessionStart", session="s-2", source="startup")
        self.assertEqual(r.returncode, 0, r.stderr)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        part = ctx.split("What the person has said about their own situation")[1].split("Resuming")[0]
        self.assertEqual(part.count("Our team budget is frozen until July."), 1)  # their words, once
        self.assertNotIn("park picnic", part)  # not the assistant's
        self.assertNotIn("Can you plan", part)  # not their requests

    def test_per_message_context_is_opt_in(self):
        self.transcript.write_text(line("user", "The deploy deadline is March 3.") + line("assistant", "Noted."))
        self.hook("Stop")
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s-3", "prompt": "When is the deploy deadline?",
                   "transcript_path": str(self.transcript), "cwd": self.dir.name}
        run = lambda *extra: subprocess.run(  # noqa: E731
            [sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "claude", *extra],
            input=json.dumps(payload), capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PYTHONPATH=str(PKG)))
        self.assertEqual(run().stdout.strip(), "")
        r = run("--per-message", "500")
        self.assertIn("March 3", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("UserPromptSubmit", settings(self.log, "claude", per_message=500)["hooks"])
        self.assertNotIn("UserPromptSubmit", settings(self.log, "claude")["hooks"])
        # The same span is not pushed twice in a session: the model already holds it.
        self.assertEqual(run("--per-message", "500").stdout.strip(), "")
        # After the context is compacted, it may not: it is pushed again.
        self.hook("PreCompact", session="s-3")
        r = run("--per-message", "500")
        self.assertIn("March 3", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_every_turn_is_recorded_once(self):
        self.transcript.write_text(
            line("user", "Which database?") +
            line("assistant", [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "SQLite."},
                               {"type": "tool_use", "name": "Bash", "input": {}}]) +
            line("user", [{"type": "tool_result", "content": "secret output"}]) +
            line("assistant", "Sidechain chatter.", isSidechain=True) +
            line("user", "note", isMeta=True))
        self.assertEqual(self.hook("Stop").returncode, 0)
        eps = self.m.state()["state"]["episodes"]
        self.assertEqual(len(eps), 1)
        ep = next(iter(eps.values()))
        self.assertEqual(ep["body"]["content"], "User: Which database?\nClaude: SQLite.")
        w = ep["writer"]
        self.assertEqual(ep["body"]["bindings"], {"User": "john", "Claude": w})
        self.assertNotIn("secret output", ep["body"]["content"])
        # Nothing new: nothing recorded. A half-written line waits.
        self.hook("Stop")
        self.assertEqual(len(self.m.state()["state"]["episodes"]), 1)
        with open(self.transcript, "a") as f:
            f.write(line("user", "And backups?") + '{"type": "assistant", "mess')
        self.hook("Stop")
        eps = self.m.state()["state"]["episodes"]
        self.assertEqual(len(eps), 2)
        with open(self.transcript, "a") as f:
            f.write('age": {"content": "Litestream."}}\n')
        self.hook("SessionEnd")
        contents = [e["body"]["content"] for e in self.m.state()["state"]["episodes"].values()]
        self.assertIn("Claude: Litestream.", contents)

    def test_long_turns_are_split_not_cut(self):
        big = "word " * 40_000
        self.transcript.write_text(line("assistant", big))
        self.assertEqual(self.hook("Stop").returncode, 0)
        eps = sorted(self.m.state()["state"]["episodes"].items(), key=lambda kv: int(kv[0].rsplit("/", 1)[1]))
        self.assertGreater(len(eps), 1)
        total = "".join(e["body"]["content"].replace("Claude: ", "").replace("\n", "") for _, e in eps)
        self.assertEqual(total, big.strip())

    def test_a_broken_hook_never_breaks_the_session(self):
        r = subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", "/nonexistent/x.jsonl", "--writer", "c"],
                           input="{not json", capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, PYTHONPATH=str(PKG)))
        self.assertEqual(r.returncode, 0)
        self.assertIn("gnomon hook", r.stderr)

    def test_settings_command(self):
        r = subprocess.run([sys.executable, "-m", "gnomon", "hooks", "--log", self.log, "--writer", "claude"],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONPATH=str(PKG)))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout)
        self.assertEqual(set(cfg["hooks"]), {"SessionStart", "Stop", "PreCompact", "SessionEnd"})
        self.assertIn("gnomon.hook", cfg["hooks"]["Stop"][0]["hooks"][0]["command"])

    def test_an_unnamed_model_is_recorded_under_its_id(self):
        self.m.register("john", "bot", party="x", kind="model", model="m-1", operator="john",
                        capabilities=["write", "spawn"], compartments=["lab"], record_time=t(3))
        self.transcript.write_text(line("assistant", "Hello."))
        payload = {"hook_event_name": "Stop", "session_id": "s-9", "transcript_path": str(self.transcript)}
        r = subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "bot"],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, PYTHONPATH=str(PKG)))
        self.assertEqual(r.stderr, "")
        ep = next(e for e in self.m.state()["state"]["episodes"].values() if e["writer"].startswith("bot-"))
        self.assertEqual(ep["body"]["content"], f"{ep['writer']}: Hello.")

    def test_read_turns_tolerates_junk(self):
        self.transcript.write_text("not json\n" + line("user", 5) + line("system", "x") + line("user", "hi"))
        turns, n = read_turns(str(self.transcript), 0)
        size = self.transcript.stat().st_size
        self.assertEqual((turns, n), ([("User", "hi", size)], size))


if __name__ == "__main__":
    unittest.main()


class Budget(unittest.TestCase):
    """Context is precious: resume fits a token budget, most useful first,
    and a new agent sees the end of the last agent's conversation."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "m.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", capabilities=["owner"], compartments=["lab"],
                   record_time=t(0))
        m.register("john", "claude", party="anthropic", kind="model", model="claude-opus-5-5", operator="john",
                   name="Claude", capabilities=["write", "spawn"], compartments=["lab"], record_time=t(1))
        from gnomon.ingest import record_messages
        record_messages(m, "claude", [("John", "Let's go with SQLite."), ("Claude", "Done; schema next.")],
                        compartment="lab", bindings={"Claude": "claude"}, record_time=t(2))
        m.ask("claude", "Do we need migrations?", frame="db", compartment="lab", record_time=t(4))
        m.handoff("claude", frame="db", compartment="lab", summary="Schema half done.", next=["finish the schema"],
                  record_time=t(5))
        for i in range(80):
            m.remember("claude", claim(f"Note number {i} about the schema design and its many details.",
                                       speaker="claude", frame="db", compartment="lab"), record_time=t(6))

    def tearDown(self):
        self.dir.cleanup()

    def test_the_budget_is_kept_and_priorities_hold(self):
        from gnomon.mcp import estimate_tokens, render_resume
        r = self.m.resume("john", "db")
        self.assertEqual([x["text"] for x in r["recent_turns"]], ["Let's go with SQLite.", "Done; schema next."])
        self.assertEqual(r["recent_turns"][1]["speaker_is"], "claude")
        full = render_resume(r)
        small = render_resume(r, 250)
        self.assertLess(estimate_tokens(small), 300)
        self.assertLess(len(small), len(full))
        self.assertIn("Next: finish the schema", small)          # the handoff first
        self.assertIn("Do we need migrations?", small)          # then open questions
        self.assertIn("left out to stay within about 250 tokens", small)
        self.assertNotIn("left out", full)

    def test_over_mcp_the_structured_part_stays_small(self):
        s = Proc("--log", self.log, "--writer", "claude", "--per-session")
        try:
            r = s.call("resume", {"frame": "db", "budget_tokens": 400})
        finally:
            s.close()
        self.assertLessEqual(r["structuredContent"]["text_tokens"], 450)
        self.assertLess(len(json.dumps(r["structuredContent"])), 2000)


class ReviewAuto(Auto):
    """Each confirmed finding of the auto-mode review, as a test."""

    def ctx_token(self, r):
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].split("token ")[1].split(",")[0]

    def test_1_one_outstanding_token_per_session_and_bound_to_it(self):
        self.transcript.write_text("")
        t1 = self.ctx_token(self.hook("SessionStart", source="startup"))
        t2 = self.ctx_token(self.hook("SessionStart", source="compact"))
        self.assertEqual(t1, t2)  # asking again returns the same, not a new one
        from gnomon.session import outstanding_tokens, redeem_token
        self.assertEqual(outstanding_tokens(self.log), [t1])
        self.assertIsNone(redeem_token(self.log, t1, session="another-session"))
        self.assertIsNotNone(redeem_token(self.log, t1, session="s-1"))
        self.assertEqual(outstanding_tokens(self.log), [])

    def test_1b_tokens_never_reach_the_record(self):
        self.transcript.write_text("")
        tok = self.ctx_token(self.hook("SessionStart"))
        self.transcript.write_text(line("assistant", f"I'll attach with token {tok} now."))
        self.hook("Stop")
        contents = " ".join(e["body"]["content"] for e in self.m.state()["state"]["episodes"].values())
        self.assertNotIn(tok, contents)
        self.assertIn("[attach token]", contents)

    def test_2_a_bad_turn_never_stops_recording(self):
        bad = '{"type": "user", "message": {"content": "x\\ud800y"}}\n'
        self.transcript.write_text(line("user", "first") + bad + line("assistant", "after"))
        self.assertEqual(self.hook("Stop").returncode, 0)
        content = " ".join(e["body"]["content"] for e in self.m.state()["state"]["episodes"].values())
        self.assertIn("after", content)
        self.assertIn("x�y", content)

    def test_2b_a_model_named_user_is_recorded(self):
        self.m.register("john", "bot", party="x", kind="model", model="m-2", operator="john", name="user",
                        capabilities=["write", "spawn"], compartments=["lab"], record_time=t(3))
        self.transcript.write_text(line("user", "hi") + line("assistant", "hello"))
        payload = {"hook_event_name": "Stop", "session_id": "s-u", "transcript_path": str(self.transcript)}
        subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "bot"],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=str(PKG)))
        ep = next(e for e in self.m.state()["state"]["episodes"].values() if e["writer"].startswith("bot-"))
        self.assertIn("hello", ep["body"]["content"])

    def test_3_4_no_duplicates_from_retries_or_overlap(self):
        self.transcript.write_text(line("user", "one") + line("assistant", "two"))
        env = dict(os.environ, PYTHONPATH=str(PKG))
        payload = json.dumps({"hook_event_name": "Stop", "session_id": "s-1",
                              "transcript_path": str(self.transcript)})
        procs = [subprocess.Popen([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "claude"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                 for _ in range(4)]
        for p in procs:
            p.communicate(payload.encode(), timeout=60)
        self.assertEqual(len(self.m.state()["state"]["episodes"]), 1)

    def test_7_host_entries_are_not_anyones_words(self):
        self.transcript.write_text(
            line("user", "A summary of the earlier conversation.", isCompactSummary=True) +
            line("user", "<command-name>/model</command-name>") +
            line("user", "<local-command-stdout>Set model</local-command-stdout>") +
            line("user", "[Request interrupted by user]") +
            json.dumps({"type": "assistant", "isApiErrorMessage": True,
                        "message": {"model": "<synthetic>", "content": "API Error: 529"}}) + "\n" +
            line("user", "Real question?"))
        self.hook("Stop")
        contents = [e["body"]["content"] for e in self.m.state()["state"]["episodes"].values()]
        self.assertEqual(contents, ["User: Real question?"])

    def test_8_instances_are_only_their_parents(self):
        self.m.register("john", "claudex", party="anthropic", kind="model", model="claude-opus-5-5",
                        operator="john", name="Claude", capabilities=["write", "spawn"], compartments=["lab"],
                        record_time=t(3))
        from gnomon.session import instance_for
        other = instance_for(self.m, "claudex", "s-1")
        mine = instance_for(self.m, "claude", "s-1")
        self.assertNotEqual(other, mine)
        self.assertEqual(self.writers()[mine]["instance_of"], "claude")

    def test_9_settings_are_shell_safe(self):
        from gnomon.hook import settings
        import shlex
        cmd = settings("/tmp/a $(touch /tmp/pwned)/m.jsonl", "claude", "/opt/my python/bin/python3",
                       "/x")["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn("'/tmp/a $(touch /tmp/pwned)/m.jsonl'", cmd)
        self.assertEqual(shlex.split(cmd)[1], "/opt/my python/bin/python3")

    def test_10_a_rewritten_transcript_is_read_again(self):
        self.transcript.write_text(line("user", "a") * 6)
        self.hook("Stop")
        self.transcript.unlink()
        self.transcript.write_text(line("user", "brand new"))
        self.hook("Stop")
        contents = " ".join(e["body"]["content"] for e in self.m.state()["state"]["episodes"].values())
        self.assertIn("brand new", contents)

    def test_11_splitting_is_exact(self):
        text = "start" + " " * 100_000 + "end"
        self.transcript.write_text(line("assistant", text))
        self.hook("Stop")
        eps = sorted(self.m.state()["state"]["episodes"].values(), key=lambda e: e["order"])
        pieces = []
        for e in eps:
            c = e["body"]["content"]
            for tr in e["body"]["turns"]:
                pieces.append(c[tr["start"]:tr["end"]])
        self.assertEqual("".join(pieces), text)

    def test_5_6_budget_keeps_the_handoff_and_the_latest_turns(self):
        from gnomon.mcp import render_resume
        from gnomon.ingest import record_messages
        record_messages(self.m, "claude", [("John" if i % 2 else "Claude", f"turn {i}") for i in range(30)],
                        compartment="lab", bindings={"Claude": "claude"}, record_time=t(4))
        self.m.handoff("john", frame="db", compartment="lab", summary="S" * 3000, next=["IMPORTANT NEXT STEP"],
                       record_time=t(5))
        r = self.m.resume("john", "db", turns=30)
        text = render_resume(r, 500)
        self.assertIn("Next: IMPORTANT NEXT STEP", text)
        self.assertIn("[cut:", text)
        self.assertNotIn("Since the handoff (newest first):\n(", text)
        r2 = self.m.resume("john", "nothing-here", turns=30)
        small = render_resume(r2, 120)
        self.assertIn("turn 29", small)
        self.assertNotIn("turn 0\n", small)

    def test_12_one_conversation_at_a_time(self):
        from gnomon.ingest import record_messages
        self.m.register("john", "gpt", party="o", kind="model", model="g", operator="john", name="GPT",
                        capabilities=["write"], compartments=["lab"], record_time=t(3))
        record_messages(self.m, "claude", [("Claude", "A1")], compartment="lab", bindings={"Claude": "claude"},
                        record_time=t(4))
        record_messages(self.m, "gpt", [("GPT", "B1")], compartment="lab", bindings={"GPT": "gpt"},
                        record_time=t(5))
        record_messages(self.m, "claude", [("Claude", "A2")], compartment="lab", bindings={"Claude": "claude"},
                        record_time=t(6))
        r = self.m.resume("john", "nothing-here")
        self.assertEqual([x["text"] for x in r["recent_turns"]], ["A1", "A2"])


class ShortIdsAndProofs(unittest.TestCase):
    """What a model reads is compact; what it sends back is resolved."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "m.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", capabilities=["owner"], compartments=["lab", "p"],
                   record_time=t(0))
        m.register("john", "claude", party="anthropic", kind="model", model="claude-opus-5-5", operator="john",
                   name="Claude", capabilities=["write", "verify"], compartments=["lab"], record_time=t(1))
        m.register("john", "gpt", party="openai", kind="model", model="gpt-6", operator="john",
                   capabilities=["write", "verify"], compartments=["lab"], record_time=t(1))
        self.c = m.remember("gpt", claim("The build is green.", speaker="gpt", frame="ci", compartment="lab",
                                         truth="true"), record_time=t(2))
        self.secret = m.remember("john", claim("Private.", speaker="john", frame="x", compartment="p"),
                                 record_time=t(2))

    def tearDown(self):
        self.dir.cleanup()

    def test_short_ids_are_shown_and_accepted(self):
        from gnomon.mcp import Server
        s = Server(self.log, "claude")
        text, _ = s.tool("discussion", {"claim": self.c[:12]})
        self.assertIn(self.c[:12], text)
        self.assertNotIn(self.c, text)
        s.tool("give_verdict", {"claim": self.c[:12], "result": "supports", "method": "test"})
        self.assertEqual(len(self.m.state()["state"]["claims"][self.c]["supports"]), 1)
        from gnomon.mcp import ToolError
        for bad in (self.secret[:12], "c:0000000000", "c:12"):
            with self.assertRaises(ToolError):
                s.tool("discussion", {"claim": bad})

    def test_proofs_can_be_limited(self):
        from gnomon import VIEWS, proofs
        for i in range(30):
            self.m.remember("gpt", claim(f"Fact {i}.", speaker="gpt", frame="ci", compartment="lab", truth="true"),
                            record_time=t(3))
        q = "?- claim(C), statement(C, S)."
        r = self.m.query("john", q, proofs=5)
        self.assertEqual(r["proved"], 5)
        self.assertEqual(sum(1 for a in r["answers"] if a["proof"] is not None), 5)
        self.assertEqual(len(r["answers"]), 32)
        # Unproven answers are never passed silently.
        self.assertTrue(proofs.check(r, q, VIEWS, self.log, require_hashes=True))
        self.assertEqual(proofs.check(r, q, VIEWS, self.log, require_hashes=True, proofs_requested=5), [])
        r0 = self.m.query("john", q, proofs=0)
        self.assertEqual((r0["proved"], r0["nodes"]), (0, {}))
        # Review: a forged answer with no proof, alone or appended, fails.
        import copy
        forged = {"answers": [{"values": ["c:" + "0" * 64, "We pay Evil Corp"], "proof": None}],
                  "nodes": {}, "rules": {}}
        self.assertTrue(proofs.check(forged, q, VIEWS, self.log, require_hashes=True))
        full = self.m.query("john", q)
        extra = copy.deepcopy(full)
        extra["answers"].append({"values": ["c:" + "1" * 64, "Fake"], "proof": None})
        self.assertTrue(proofs.check(extra, q, VIEWS, self.log, proofs_requested=10000))
        middle = copy.deepcopy(r)
        middle["answers"][1], middle["answers"][-1] = middle["answers"][-1], middle["answers"][1]
        self.assertTrue(proofs.check(middle, q, VIEWS, self.log, proofs_requested=5))
        self.assertTrue(proofs.check(r, q, VIEWS, self.log, proofs_requested=6))


class ReviewPerf(ShortIdsAndProofs):
    """Findings of the performance review, as tests."""

    def test_query_programs_take_short_ids(self):
        from gnomon.mcp import Server
        s = Server(self.log, "claude")
        text, out = s.tool("query", {"program": f'?- statement("{self.c[:18]}", S).'})
        self.assertEqual(out["answers"], [["The build is green."]])
        self.assertTrue(out["proofs_checked"])

    def test_unproven_answers_are_never_reported_as_checked(self):
        from gnomon.mcp import Server
        s = Server(self.log, "claude")
        text, out = s.tool("query", {"program": "?- claim(C).", "proofs": 0})
        self.assertFalse(out["proofs_checked"])
        self.assertIn("unproven", text)

    def test_recall_is_the_same_from_many_threads(self):
        import threading
        want = json.dumps(self.m.recall("john", "build green"))
        got, errs = [], []

        def work():
            try:
                for _ in range(20):
                    got.append(json.dumps(self.m.recall("john", "build green")))
            except Exception as e:  # pragma: no cover
                errs.append(e)
        ts = [threading.Thread(target=work) for _ in range(4)]
        for th in ts:
            th.start()
        for th in ts:
            th.join()
        self.assertEqual(errs, [])
        self.assertEqual(set(got), {want})
