"""Standing directives: what is pulled out, and what is loaded within budget."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gnomon.consolidate import directive_sentences, directives, standing  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
