"""What a model has already been shown: a recall hit shown in full earlier in
the same session is referred to, not paid for again, until the session's
context is compacted or the result is too far back to rely on."""

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

from gnomon import Memory  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from gnomon.mcp import Server  # noqa: E402
from gnomon.session import instance_for  # noqa: E402


def t(minute: int) -> str:
    return f"2026-10-16T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


class Seen(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "m.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                   compartments=["lab"], record_time=t(0))
        m.register("john", "claude", party="anthropic", kind="model", model="claude-opus-5-5", operator="john",
                   name="Claude", capabilities=["write", "verify", "spawn"], compartments=["lab"], record_time=t(1))
        record_messages(m, "claude", [("John", "The database migration failed at midnight."),
                                      ("Claude", "I rolled the migration back and kept a backup."),
                                      ("John", "Lunch was a sandwich.")],
                        compartment="lab", bindings={"Claude": "claude"}, record_time=t(2))

    def tearDown(self):
        self.dir.cleanup()

    def recall(self, srv, **extra):
        text, r = srv.tool("recall", {"query": "database migration rollback", "k": 2, **extra})
        return text

    def test_a_repeat_is_referred_to_not_repeated(self):
        srv = Server(self.log, "claude")
        first = self.recall(srv)
        self.assertIn("rolled the migration back", first)
        self.assertNotIn("^", first)
        second = self.recall(srv)
        self.assertNotIn("rolled the migration back", second)
        self.assertIn(": ^ <", second)
        self.assertIn("repeat=true", second)
        self.assertLess(len(second), len(first))
        # Asked for, it is shown again in full.
        self.assertIn("rolled the migration back", self.recall(srv, repeat=True))

    def test_each_connection_has_its_own_context(self):
        a, b = Server(self.log, "claude"), Server(self.log, "claude")
        self.recall(a)
        self.assertIn("rolled the migration back", self.recall(b))

    def test_too_far_back_is_shown_again(self):
        srv = Server(self.log, "claude")
        self.recall(srv)
        for _ in range(Server.SEEN_WINDOW):
            srv.tool("frames", {})
        self.assertIn("rolled the migration back", self.recall(srv))

    def test_off_switch(self):
        srv = Server(self.log, "claude", dedup=False)
        self.recall(srv)
        self.assertIn("rolled the migration back", self.recall(srv))

    def test_compaction_forgets_what_was_shown(self):
        w = instance_for(self.m, "claude", "sess-9")
        srv = Server(self.log, w)
        self.recall(srv)
        self.assertNotIn("rolled the migration back", self.recall(srv))
        transcript = Path(self.dir.name) / "t.jsonl"
        transcript.write_text("")
        r = subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "claude"],
                           input=json.dumps({"hook_event_name": "PreCompact", "session_id": "sess-9",
                                             "transcript_path": str(transcript)}),
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONPATH=str(PKG)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rolled the migration back", self.recall(srv))
        # Another session's compaction is not this one's.
        self.assertNotIn("rolled the migration back", self.recall(srv))
        subprocess.run([sys.executable, "-m", "gnomon.hook", "--log", self.log, "--writer", "claude"],
                       input=json.dumps({"hook_event_name": "PreCompact", "session_id": "other",
                                         "transcript_path": str(transcript)}),
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONPATH=str(PKG)))
        self.assertNotIn("rolled the migration back", self.recall(srv))


if __name__ == "__main__":
    unittest.main()
