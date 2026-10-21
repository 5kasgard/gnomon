"""The weak-signal ranker: without an embedder it is recall's order (no
dependencies); with one it ranks by the shipped weights, incrementally."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gnomon import Memory  # noqa: E402
from gnomon.consolidate import Tiers  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from gnomon.rank import WEIGHTS, Ranker  # noqa: E402

try:
    import numpy as np
except ImportError:  # CI runs the core with the standard library only
    np = None


def bag_embed(texts):
    """A deterministic stand-in for a meaning model: hashed bag of words."""
    out = []
    for t in texts:
        v = np.zeros(64, dtype=np.float32)
        for w in t.lower().split():
            v[zlib.crc32(w.strip(".,?!").encode()) % 64] += 1.0
        out.append(v)
    return np.stack(out)


class Ranking(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = Memory(Path(self.dir.name) / "log.jsonl")
        self.m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"],
                        compartments=["chat"], record_time="2024-01-01T00:00:00Z")
        record_messages(self.m, "owner", [
            ("user", "My knee surgery is scheduled for next Tuesday."),
            ("assistant", "I hope the surgery on your knee goes smoothly."),
            ("user", "The quarterly report is due on Friday."),
            ("assistant", "Noted, the report is due Friday."),
        ], compartment="chat", record_time="2024-01-02T00:00:00Z")

    def tearDown(self):
        self.dir.cleanup()

    def test_weights_are_the_eight_invariant_signals(self):
        self.assertEqual(set(WEIGHTS), {"word", "sim", "dense_rank", "own", "number", "surprise",
                                        "topic_surprise", "atypical"})
        self.assertTrue(all(v > 0 for v in WEIGHTS.values()))

    def test_without_an_embedder_it_is_recall_order(self):
        r = Ranker(self.m, "owner")
        hits = r.rank("knee surgery", k=2)
        self.assertEqual(hits[0]["pointer"].split("#")[1], "0")

    @unittest.skipIf(np is None, "numpy not installed")
    def test_with_an_embedder_the_person_ranks_ahead(self):
        r = Ranker(self.m, "owner", embed=bag_embed)
        hits = r.rank("When is my knee surgery?", k=4)
        self.assertEqual(hits[0]["speaker"], "user")
        self.assertIn("Tuesday", hits[0]["text"])

    @unittest.skipIf(np is None, "numpy not installed")
    def test_new_turns_are_added_without_recomputing_old_ones(self):
        r = Ranker(self.m, "owner", embed=bag_embed)
        r.rank("report", k=2)
        before = r._sur[:4].copy()
        record_messages(self.m, "owner", [("user", "I moved the report to Monday.")], compartment="chat",
                        record_time="2024-01-03T00:00:00Z")
        hits = r.rank("when is the report due", k=3)
        self.assertEqual(len(r._order), 5)
        self.assertTrue(np.array_equal(before, r._sur[:4]))
        self.assertTrue(any("Monday" in h["text"] for h in hits))

    @unittest.skipIf(np is None, "numpy not installed")
    def test_tiers_use_the_ranker(self):
        t = Tiers(self.m, "owner", ranker=Ranker(self.m, "owner", embed=bag_embed))
        c = t.context("When is my knee surgery?", budget=100)
        self.assertTrue(c["view"])
        self.assertIn("Tuesday", c["view"][0]["text"])


if __name__ == "__main__":
    unittest.main()
