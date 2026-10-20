"""The firm: teams of models shaped to the work by price and track record.
Exact arithmetic over stated inputs, checked by hand and by brute force."""

from __future__ import annotations

import itertools
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from gnomon import Memory, claim  # noqa: E402
from gnomon import firm  # noqa: E402
from gnomon.firm import ORDER, TIERS, Record, Task, decide, plan, price_choice, record_from  # noqa: E402
from gnomon.mcp import Server  # noqa: E402


class Prices(unittest.TestCase):
    def test_published_prices_and_the_haiku_cliff(self):
        self.assertAlmostEqual(TIERS["opus"].cost(1_000_000, 0), 4.0)
        self.assertAlmostEqual(TIERS["sonnet"].cost(0, 1_000_000), 10.0)
        self.assertAlmostEqual(TIERS["haiku"].cost(100_000, 0), 0.01)
        # One token over 100,000 and every token is priced higher.
        self.assertAlmostEqual(TIERS["haiku"].cost(100_001, 0), 100_001 * 0.50 / 1e6)
        self.assertAlmostEqual(TIERS["opus"].cost(0, 0, cached_tokens=1_000_000), 0.20)
        self.assertAlmostEqual(TIERS["opus"].cost(1_000_000, 0, batch=True), 2.0)


class Decisions(unittest.TestCase):
    def test_an_exact_cascade_by_its_closed_form(self):
        rec = Record()
        t = Task("k", 10_000, 1_000, stakes_usd=3.0, check="exact")
        for n in range(1, 4):
            for ws in itertools.combinations(ORDER, n):
                c = price_choice(t, ws, "exact", rec)
                ps = [rec.p(w, "k") for w in ws]
                costs = [TIERS[w].cost(10_000, 1_000) for w in ws]
                reach, spend = 1.0, 0.0
                for p, cost in zip(ps, costs):
                    spend += reach * cost
                    reach *= 1 - p
                self.assertAlmostEqual(c.spend, spend)
                self.assertAlmostEqual(c.p_right, 1 - reach)
                self.assertAlmostEqual(c.loss, reach * 3.0)

    def test_a_reviewed_step_by_enumeration(self):
        rec = Record()
        t = Task("k", 5_000, 500, stakes_usd=10.0, check="review")
        c = price_choice(t, ("haiku",), "sonnet", rec)
        p = rec.p("haiku", "k")
        catch, fa = firm.REVIEW["sonnet"]
        # Outcomes: right & passed (kept, fine); wrong & missed (loss);
        # anything flagged at the top (loss: nothing better to try).
        self.assertAlmostEqual(c.p_right, p * (1 - fa))
        self.assertAlmostEqual(c.loss, ((1 - p) * (1 - catch) + p * fa + (1 - p) * catch) * 10.0)
        self.assertAlmostEqual(c.spend, TIERS["haiku"].cost(5_000, 500) + TIERS["sonnet"].cost(5_500, 200))

    def test_low_stakes_go_cheap_high_stakes_go_up(self):
        cheap = decide(Task("k", 2_000, 300, stakes_usd=0.001, check="none"))
        dear = decide(Task("k", 2_000, 300, stakes_usd=100.0, check="none"))
        self.assertEqual(cheap.workers, ("haiku",))
        self.assertEqual(dear.workers, ("opus",))

    def test_the_record_changes_who_is_hired(self):
        t = Task("code", 20_000, 3_000, stakes_usd=5.0, check="exact", parts=4, step_usd=0.02)
        self.assertEqual(decide(t).workers[0], "haiku")
        rec = Record()
        rec.add("haiku", "code", False, 200)  # haiku keeps getting this wrong
        self.assertNotIn("haiku", decide(t, rec).workers)
        rec2 = Record()
        rec2.add("haiku", "code", True, 500)  # and here it is nearly always right
        self.assertEqual(decide(t, rec2).workers[0], "haiku")
        self.assertGreater(rec2.p("haiku", "code"), 0.99)


class Shapes(unittest.TestCase):
    def test_floors_grow_with_the_work(self):
        small = plan(Task("k", 1_000, 200, 0.01, "exact", parts=3))
        big = plan(Task("k", 1_000, 200, 0.01, "exact", parts=small.span ** 2 + 1))
        floors = lambda p: [r for r, _, _ in p.floors if r.startswith("managers")]  # noqa: E731
        self.assertEqual(floors(small), [])
        self.assertEqual(len(floors(big)), 2)
        self.assertEqual(small.floors[0][1], "opus")  # the planner
        one = plan(Task("k", 1_000, 200, 0.01, "exact", parts=1))
        self.assertFalse(any(r.startswith("executive") for r, _, _ in one.floors))

    def test_the_single_agent_baseline_rereads_its_context(self):
        p = plan(Task("k", 1_000, 100, 0.0, "exact", parts=3))
        opus = TIERS["opus"]
        want = sum(opus.cost(1_000, 100, cached_tokens=i * 1_100) for i in range(3))
        self.assertAlmostEqual(p.baseline_spend, want)

    def test_night_shift_halves(self):
        a = plan(Task("k", 1_000, 200, 0.0, "exact", parts=10))
        b = plan(Task("k", 1_000, 200, 0.0, "exact", parts=10, urgent=False))
        self.assertAlmostEqual(b.spend, a.spend / 2)


class FromTheRecord(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "m.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                   compartments=["lab"], record_time="2026-01-01T00:00:00Z")
        for w, model, party in (("staff", "claude-haiku-5-5", "anthropic"), ("boss", "claude-opus-5-5", "anthropic"),
                                ("checker", "gnomon-checker-1", "gnomon")):
            m.register("john", w, party=party, kind="tool" if w == "checker" else "model", model=model,
                       operator="john", capabilities=["write", "verify"], compartments=["lab"])

    def tearDown(self):
        self.dir.cleanup()

    def done(self, writer, text, ok):
        c = self.m.remember(writer, claim(text, speaker=writer, frame="firm/code", compartment="lab",
                                          stance="asserted", truth="true", origin="derived", confidence=6000))
        self.m.verdict("checker", c, "supports" if ok else "refutes", "test")

    def test_verdicts_on_firm_work_are_the_track_record(self):
        for i in range(6):
            self.done("staff", f"Task {i} done: tests pass.", ok=i < 2)
        self.done("boss", "Task 9 done: tests pass.", ok=True)
        rec = record_from(self.m, "john")
        self.assertEqual(rec.counts[("haiku", "code")], (2, 4))
        self.assertEqual(rec.counts[("opus", "code")], (1, 0))
        # Work outside firm frames does not count.
        c = self.m.remember("staff", claim("Unrelated.", speaker="staff", frame="other", compartment="lab",
                                           stance="asserted", truth="true", origin="derived", confidence=6000))
        self.m.verdict("checker", c, "refutes", "test")
        self.assertEqual(record_from(self.m, "john").counts[("haiku", "code")], (2, 4))

    def test_the_mcp_tool_plans_from_the_record(self):
        for i in range(30):
            self.done("staff", f"Task {i} done.", ok=False)
        srv = Server(self.log, "john")
        text, out = srv.tool("plan_team", {"kind": "code", "input_tokens": 20000, "output_tokens": 3000,
                                           "stakes_usd": 5, "check": "exact", "parts": 4, "step_usd": 0.05})
        self.assertIn("one opus agent alone", text)
        self.assertNotIn("haiku", out["each_part"]["workers"])
        self.assertEqual(out["record"][0]["on_record"], 30)


if __name__ == "__main__":
    unittest.main()
