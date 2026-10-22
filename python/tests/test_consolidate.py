"""Standing directives: what is pulled out, and what is loaded within budget."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile  # noqa: E402

from gnomon import Memory  # noqa: E402
from gnomon.consolidate import Tiers, directive_sentences, directives, standing  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402


class Directives(unittest.TestCase):
    def test_instructions_and_preferences_are_found(self):
        self.assertEqual(directive_sentences("Always use 24-hour time when I ask about meetings."),
                         ["Always use 24-hour time when I ask about meetings."])
        self.assertEqual(directive_sentences("Thanks! I prefer Redis pub/sub over emitters. Show me how?"),
                         ["I prefer Redis pub/sub over emitters."])
        self.assertEqual(directive_sentences("Never send me a summary longer than five lines"),
                         ["Never send me a summary longer than five lines"])

    def test_ordinary_sentences_are_not(self):
        for s in ("It always rains here.", "What do you prefer?", "We never met.", "Can you help me plan?"):
            self.assertEqual(directive_sentences(s), [], s)

    def test_only_the_named_speakers_count(self):
        found = directives([(1, "user", "Always cite sources."), (2, "assistant", "Always happy to help.")],
                           {"user"})
        self.assertEqual([d["source"] for d in found], [1])

    def test_everything_loads_when_it_fits(self):
        found = [{"source": i, "speaker": "u", "text": f"Always do thing {i}."} for i in range(5)]
        self.assertEqual(standing(found, "anything"), found)

    def test_over_budget_keeps_the_relevant_then_recent_in_order(self):
        found = [{"source": i, "speaker": "u", "text": f"Always mention topic{i} details " + "x" * 40}
                 for i in range(10)]
        cost = (len(found[0]["text"]) + 3) // 4
        kept = standing(found, "tell me about topic2", budget=3 * cost)
        self.assertEqual([d["source"] for d in kept], [2, 8, 9])


class TieredContext(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = Memory(Path(self.dir.name) / "log.jsonl")
        self.m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"],
                        compartments=["chat"], record_time="2024-01-01T00:00:00Z")
        record_messages(self.m, "owner", [
            ("user", "Always use 24-hour time when I ask about meetings. Thanks."),
            ("assistant", "Always happy to help. Noted."),
            ("user", "The deploy deadline is March 1, 2024. The budget is $1,200 for hosting."),
            ("assistant", "Got it. I will plan around March 1."),
        ], compartment="chat", record_time="2024-01-02T00:00:00Z")
        self.t = Tiers(self.m, "owner")

    def tearDown(self):
        self.dir.cleanup()

    def test_hot_tier_holds_the_users_directives_only(self):
        c = self.t.context("When is the next meeting?")
        self.assertEqual([d["text"] for d in c["standing"]],
                         ["Always use 24-hour time when I ask about meetings."])
        self.assertTrue(c["text"].startswith("Standing instructions"))

    def test_openers_show_how_each_conversation_began_in_order(self):
        record_messages(self.m, "owner", [
            ("user", "Let's plan the hosting migration to the new provider."),
            ("assistant", "Sure. First, inventory the services."),
        ], compartment="chat", record_time="2024-02-01T00:00:00Z")
        record_messages(self.m, "owner", [
            ("user", "Back to the hosting migration: the DNS cutover is next."),
            ("assistant", "Then lower the TTLs first."),
        ], compartment="chat", record_time="2024-03-01T00:00:00Z")
        o = self.t.openers("In what order did I work on the hosting migration?")
        self.assertEqual([x["time"][:7] for x in o["openers"]], ["2024-01", "2024-02", "2024-03"][-len(o["openers"]):])
        self.assertTrue(all(x["speaker"] == "user" for x in o["openers"]))
        self.assertIn("DNS cutover", o["text"].splitlines()[-2])
        self.assertTrue(o["text"].index("plan the hosting") < o["text"].index("DNS cutover"))

    def test_warm_tier_points_at_the_exact_words(self):
        c = self.t.context("What is the deploy deadline?", budget=200)
        self.assertTrue(c["view"])
        s = c["view"][0]
        self.assertIn("March 1, 2024", s["text"])
        whole = self.t.expand(s["pointer"])
        self.assertIn(s["text"].split(" The budget")[0], whole["text"])
        state = self.m.state()["state"]["episodes"]
        content = state[s["pointer"].rsplit("#", 1)[0]]["body"]["content"]
        self.assertEqual(content[s["start"]:s["end"]], s["text"])
        self.assertLessEqual(c["tokens"]["view"], 200)

    def test_new_directives_are_seen(self):
        self.t.context("anything")
        record_messages(self.m, "owner", [("user", "Never use bullet points.")], compartment="chat",
                        record_time="2024-01-03T00:00:00Z")
        texts = [d["text"] for d in self.t.context("anything")["standing"]]
        self.assertIn("Never use bullet points.", texts)


if __name__ == "__main__":
    unittest.main()
