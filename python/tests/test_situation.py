"""The situation model: the person's own sentences about themselves, exact, with pointers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gnomon import Memory  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from gnomon.situation import Situation, candidates  # noqa: E402

try:
    import numpy as np
except ImportError:  # the core needs no numpy; the learnt selector does
    np = None


def toy_embed(texts):
    """Deterministic 4-d vectors: enough to exercise the learnt path."""
    out = []
    for t in texts:
        t = t.lower()
        out.append([1.0 + ("marathon" in t), 1.0 + ("budget" in t), float(len(t) % 7), 1.0])
    return out


class Candidates(unittest.TestCase):
    def test_self_statements_kept_requests_dropped(self):
        got = [s for _, _, s in candidates(
            "I'm training for a marathon in March. Can you plan my week? The weather is nice. "
            "My budget is frozen until July.")]
        self.assertEqual(got, ["I'm training for a marathon in March.", "My budget is frozen until July."])

    def test_offsets_are_the_records_characters(self):
        text = "Hello there. I hate early meetings."
        (a, b, s), = candidates(text)
        self.assertEqual(text[a:b], s)


class Model(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = Memory(Path(self.dir.name) / "log.jsonl")
        self.m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"],
                        compartments=["chat"], record_time="2024-01-01T00:00:00Z")
        record_messages(self.m, "owner", [
            ("user", "I'm training for a marathon in March. What should I eat before a long run?"),
            ("assistant", "I would suggest oats. I think you will do well."),
            ("user", "My team budget is frozen until July. The office is cold."),
        ], compartment="chat", record_time="2024-01-02T00:00:00Z")

    def tearDown(self):
        self.dir.cleanup()

    def test_without_embedder_every_candidate_kept_in_order_with_pointers(self):
        s = Situation(self.m, "owner")
        lines = s.model()
        self.assertEqual([c["text"] for c in lines],
                         ["I'm training for a marathon in March.", "My team budget is frozen until July."])
        for c in lines:  # the assistant's "I would suggest" is not the person's situation
            self.assertNotEqual(c["speaker"], "assistant")
            turn = self.m.state()["state"]["episodes"][c["pointer"].rpartition("#")[0]]["body"]
            self.assertEqual(turn["content"][c["start"]:c["end"]], c["text"])

    def test_render_asks_the_reader_to_select_first(self):
        r = Situation(self.m, "owner").render()
        self.assertIn("pick the lines", r["text"])
        self.assertIn("marathon", r["text"])
        self.assertGreater(r["tokens"], 0)

    def test_budget_keeps_the_most_recent(self):
        r = Situation(self.m, "owner").render(budget=15)
        self.assertEqual([c["text"] for c in r["lines"]], ["My team budget is frozen until July."])

    def test_new_sentences_appear_after_the_log_grows(self):
        s = Situation(self.m, "owner")
        self.assertEqual(len(s.model()), 2)
        record_messages(self.m, "owner", [("user", "We moved to Lisbon last week.")],
                        compartment="chat", record_time="2024-01-03T00:00:00Z")
        self.assertEqual(s.model()[-1]["text"], "We moved to Lisbon last week.")

    @unittest.skipIf(np is None, "numpy not installed")
    def test_learnt_weights_keep_the_top_share(self):
        from gnomon.situation import train
        w = train(["I'm training for a marathon.", "My budget is frozen.", "I had toast.", "I saw a bird."],
                  [True, False, False, False], toy_embed)
        self.assertAlmostEqual(w["share"], 0.25)
        s = Situation(self.m, "owner", embed=toy_embed, weights={**w, "share": 0.5})
        self.assertEqual([c["text"] for c in s.model()], ["I'm training for a marathon in March."])

    @unittest.skipIf(np is None, "numpy not installed")
    def test_learnt_weights_are_saved_beside_the_log_and_reloaded(self):
        s = Situation(self.m, "owner", embed=toy_embed)
        self.assertIsNone(s.weights)
        sample = s.sample(n=10)
        self.assertEqual(len(sample), 2)
        s.learn([c["text"] for c in sample] + ["I had toast.", "I saw a bird."], [True, False, False, False])
        again = Situation(self.m, "owner", embed=toy_embed)
        self.assertEqual(again.weights["share"], 0.25)
        self.assertEqual(len(again.model()), 1)  # a share rounds up: never nothing
        self.assertEqual(len(again.candidates()), 2)

    def test_learning_needs_an_embedder(self):
        with self.assertRaises(ValueError):
            Situation(self.m, "owner").learn(["I am."], [True])

    @unittest.skipIf(np is None, "numpy not installed")
    def test_mismatched_weights_are_refused(self):
        with self.assertRaises(ValueError):
            Situation(self.m, "owner", embed=toy_embed, weights={"w": [0.0] * 3, "share": 0.5}).model()


if __name__ == "__main__":
    unittest.main()
