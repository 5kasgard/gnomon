"""The alphabet of organisational forms: each rule follows its evidence,
and the learner moves off the prior when the record says another form
pays better."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gnomon.forms import ALPHABET, SOURCES, Dials, Learner, Profile, amdahl, candidates, compose, reward, workers_for  # noqa: E402


def letters(d):
    return [s.letter for s in d.stages]


class FormsTest(unittest.TestCase):
    def test_every_letter_cites_known_sources(self):
        for L in ALPHABET.values():
            self.assertTrue(L.evidence, L.code)
            for e in L.evidence:
                self.assertIn(e.source, SOURCES)
                self.assertIn(e.strength, ("measured", "theory", "hypothesis"))
        for s in compose(Profile(parts=50, check="exact", locatable=True, families=3)).stages:
            for k in s.sources:
                self.assertIn(k, SOURCES)

    def test_amdahl_bounds_the_workforce(self):
        self.assertAlmostEqual(amdahl(0.5, 10**9), 2.0, places=6)
        self.assertEqual(workers_for(0.0, 100, 1.0), 1)
        slow, fast = workers_for(0.95, 100, 0.0), workers_for(0.95, 100, 1.0)
        self.assertLess(slow, fast)
        self.assertLessEqual(fast, 100)
        # Mostly serial work gets few workers however urgent.
        self.assertLessEqual(workers_for(0.3, 100, 1.0), 25)

    def test_an_exact_check_means_independent_proposals_selected_not_debate(self):
        d = compose(Profile(check="exact", families=3), Dials(effort=0.8))
        self.assertEqual(letters(d)[:2], ["P", "V"])
        self.assertNotIn("B", letters(d))
        self.assertIn("I", letters(d))
        self.assertGreater(d.stages[0].size, 1)
        # A fault the check can name earns a repair round; otherwise not.
        self.assertIn("K", letters(compose(Profile(check="exact", locatable=True))))
        self.assertNotIn("K", letters(compose(Profile(check="exact"))))

    def test_a_known_expert_is_deferred_to(self):
        d = compose(Profile(expert=True, open_ended=True))
        self.assertIn("R", letters(d))
        self.assertNotIn("Q", letters(d))

    def test_strong_models_take_turns_weak_ones_get_structure(self):
        strong = compose(Profile(open_ended=True, tier="opus"), Dials(effort=1.0))
        weak = compose(Profile(open_ended=True, tier="haiku"), Dials(effort=1.0))
        self.assertIn("Q", letters(strong))
        self.assertNotIn("Q", letters(weak))
        self.assertIn("C", letters(weak))
        self.assertTrue(weak.warnings)
        # Never more than four voices in one deliberation.
        self.assertLessEqual(max(s.size for s in strong.stages if s.letter == "Q"), 4)

    def test_coupled_work_stays_in_one_context_for_strong_models(self):
        d = compose(Profile(parts=6, coupling="high", tier="opus"))
        self.assertEqual(letters(d)[0], "S")
        self.assertNotIn("D", letters(d))
        w = compose(Profile(parts=6, coupling="high", tier="haiku"))
        self.assertIn("D", letters(w))
        self.assertTrue(any("exact state" in x for x in w.warnings))

    def test_many_parts_grow_floors(self):
        self.assertNotIn("H", letters(compose(Profile(parts=30))))
        d = compose(Profile(parts=5000, context_parts=40))
        self.assertIn("H", letters(d))
        self.assertEqual([s.size for s in d.stages if s.letter == "H"], [3])

    def test_dials_move_the_form(self):
        p = Profile(check="review")
        lo, hi = compose(p, Dials(effort=0, scrutiny=0.2)), compose(p, Dials(effort=1, scrutiny=0.9))
        self.assertLess(lo.calls, hi.calls)
        self.assertEqual([s.size for s in hi.stages if s.letter == "I"], [3])
        self.assertNotIn("X", letters(compose(p, Dials(thrift=0.1))))
        self.assertIn("X", letters(compose(p, Dials(thrift=0.9))))

    def test_a_workload_gets_a_toolmaker_a_floor_and_batches(self):
        d = compose(Profile(repeats=1_000_000, check="review"))
        self.assertEqual(letters(d)[:3], ["T", "F", "N"])
        self.assertEqual([s.size for s in d.stages if s.letter == "F"], [2])  # two tiers, never more
        self.assertTrue(any("probe" in w.lower() for w in d.warnings))
        small = compose(Profile(repeats=2000))
        self.assertNotIn("T", letters(small))
        self.assertIn("F", letters(small))
        # Open-ended or not narrow: no floor.
        self.assertNotIn("F", letters(compose(Profile(repeats=10**6, open_ended=True))))
        self.assertNotIn("F", letters(compose(Profile(repeats=10**6, narrow=False))))
        for k in ("T", "F", "N"):
            self.assertIn(k, ALPHABET)

    def test_bad_input_is_refused(self):
        for bad in ({"parts": 0}, {"parallel": 1.5}, {"coupling": "medium"}, {"check": "maybe"}, {"tier": "gpt"}):
            with self.assertRaises(ValueError):
                Profile(**bad)
        with self.assertRaises(ValueError):
            Dials(effort=2)
        with self.assertRaises(ValueError):
            reward(1, 1, 0)

    def test_learner_starts_on_the_prior_and_learns_from_the_record(self):
        p = Profile(check="exact", families=2)
        cands = candidates(p)
        self.assertGreaterEqual(len(cands), 2)
        lr = Learner()
        self.assertEqual(lr.choose(p, cands).word, cands[0].word)
        # The record says the last candidate pays and the rest do not.
        rng = random.Random(1)
        best = cands[-1].word
        for _ in range(60):
            for c in cands:
                lr.update(p, c, (0.9 if c.word == best else 0.3) + rng.uniform(-0.05, 0.05))
        self.assertEqual(lr.choose(p, cands).word, best)
        again = Learner.from_dict(lr.to_dict())
        self.assertEqual(again.choose(p, cands).word, best)


    def test_the_mcp_tool(self):
        import tempfile
        from gnomon import Memory
        from gnomon.mcp import Server
        with tempfile.TemporaryDirectory() as t:
            log = str(Path(t) / "m.jsonl")
            Memory(log).register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                                 compartments=["lab"], record_time="2026-01-01T00:00:00Z")
            srv = Server(log, "john")
            text, out = srv.tool("design_team", {"check": "exact", "families": 3, "effort": 0.8})
            self.assertTrue(out["word"].startswith("P[") and "V" in out["word"], out["word"])
            self.assertIn("Interaction Tax", " ".join(out["stages"][0]["sources"]))
            for bad in ({"parallel": True}, {"effort": 3}, {"tier": "gpt"}, {"expert": "yes"}, {"repeats": 0}, {"nonsense": 1}):
                msg = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "design_team", "arguments": bad}})
                self.assertTrue(msg.get("result", {}).get("isError") or "error" in msg, (bad, msg))


if __name__ == "__main__":
    unittest.main()
