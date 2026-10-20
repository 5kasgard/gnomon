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
        t = Task("k", 10_000, 1_000, stakes_usd=3.0, check="exact", detected_usd=0.7)
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
                self.assertAlmostEqual(c.loss, reach * 0.7)  # caught: known undone, never unnoticed

    def test_every_option_by_enumerating_outcomes(self):
        # Walk the tree of outcomes (right or wrong; caught, missed or
        # wrongly flagged) for every option and compare.
        rec = Record()
        rec.add("haiku", "k", True, 3)
        rec.add("sonnet", "k", False, 2)
        for task in (Task("k", 5_000, 500, stakes_usd=10.0, check="exact", detected_usd=2.0),
                     Task("k", 120_000, 900, stakes_usd=4.0, check="review", detected_usd=1.5, urgent=False)):
            for c in firm.choices(task, rec):
                spend = loss = right = rounds = 0.0

                def walk(i, prob):
                    nonlocal spend, loss, right, rounds
                    t = c.workers[i]
                    spend += prob * TIERS[t].cost(task.input_tokens, task.output_tokens, batch=not task.urgent)
                    rounds += prob
                    p = rec.p(t, "k")
                    if c.reviewer is None:
                        right += prob * p
                        loss += prob * (1 - p) * task.stakes_usd
                        return
                    catch, fa = (1.0, 0.0) if c.reviewer == "exact" else firm.REVIEW[c.reviewer]
                    if c.reviewer != "exact":
                        spend += prob * TIERS[c.reviewer].cost(task.input_tokens + task.output_tokens, 200,
                                                                batch=not task.urgent)
                        rounds += prob
                    for ok, q in ((True, p), (False, 1 - p)):
                        flag = (fa if ok else catch)
                        kept = prob * q * (1 - flag)
                        if ok:
                            right += kept
                        else:
                            loss += kept * task.stakes_usd
                        if i + 1 < len(c.workers):
                            walk(i + 1, prob * q * flag)
                        else:
                            loss += prob * q * flag * task.detected_usd
                walk(0, 1.0)
                self.assertAlmostEqual(c.spend, spend, places=12, msg=c.describe())
                self.assertAlmostEqual(c.loss, loss, places=12, msg=c.describe())
                self.assertAlmostEqual(c.p_right, right, places=12, msg=c.describe())
                self.assertAlmostEqual(c.rounds, rounds, places=12, msg=c.describe())

    def test_a_check_is_never_worth_less_than_none(self):
        # Catching a wrong result costs only what an undone part costs.
        t = Task("k", 5_000, 500, stakes_usd=1000.0, check="exact", detected_usd=100.0)
        best = decide(t)
        self.assertEqual(best.reviewer, "exact")
        alone = price_choice(t, ("opus",), None, Record())
        checked = price_choice(t, ("opus",), "exact", Record())
        self.assertLess(checked.loss, alone.loss)

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
        task = Task("k", 1_000, 100, 0.0, "exact", parts=3)
        p = plan(task)
        opus = TIERS["opus"]
        unit = firm.choices(task, Record(), ["opus"])[0]
        self.assertEqual(p.baseline.reviewer, unit.reviewer)
        want = sum(unit.spend + sum(unit.reach) * opus.cost(0, 0, cached_tokens=i * 1_100, written_tokens=1_000)
                   for i in range(3))
        self.assertAlmostEqual(p.baseline_spend, want)

    def test_the_baseline_compacts_at_its_desk(self):
        p = plan(Task("k", 20_000, 2_000, 0.0, "exact", parts=1000))
        # Never more than a desk of context re-read per attempt.
        opus = TIERS["opus"]
        most = 1000 * (p.baseline.spend + sum(p.baseline.reach) * opus.cost(
            0, 0, cached_tokens=opus.desk - 20_000, written_tokens=20_000))
        self.assertLessEqual(p.baseline_spend, most + 1e-9)

    def test_parts_too_big_for_any_window_are_refused(self):
        with self.assertRaises(ValueError):
            plan(Task("k", 2_000_000, 10, 1.0))
        # Too big for Haiku's window: Haiku is not given it.
        self.assertNotIn("haiku", decide(Task("k", 300_000, 10, 0.0, "exact")).workers)

    def test_inputs_are_checked(self):
        for bad in (dict(check="bogus"), dict(stakes_usd=-1), dict(stakes_usd=float("inf")), dict(parts=0),
                    dict(input_tokens=True), dict(step_usd=-5), dict(input_tokens=10**400), dict(kind=" ")):
            args = dict(kind="k", input_tokens=10, output_tokens=10, stakes_usd=1.0)
            args.update(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                Task(**args)
        self.assertEqual(Task("Code", 1, 1, 0).kind, "code")

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

    def order(self, by="john", text="Write the parser."):
        return self.m.ask(by, text, frame="firm/code", compartment="lab")

    def result(self, writer, text, order):
        return self.m.remember(writer, claim(text, speaker=writer, frame="firm/code", compartment="lab",
                                             stance="asserted", truth="true", origin="derived", confidence=6000,
                                             about=[{"claim": order, "relation": "answers"}]))

    def done(self, writer, text, ok, order=None):
        c = self.result(writer, text, order or self.order(text=f"Order for: {text}"))
        self.m.verdict("checker", c, "supports" if ok else "refutes", "test")
        return c

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

    def test_the_record_cannot_be_farmed(self):
        m = self.m
        m.register("john", "staff2", party="other-co", kind="model", model="claude-haiku-5-5", operator="john",
                   capabilities=["write", "verify"], compartments=["lab"])
        m.register("john", "linter", party="lint-co", kind="tool", model="magnum-opus-linter",
                   operator="john", capabilities=["write", "verify"], compartments=["lab"])
        # Its own work order: not counted.
        own = self.order(by="staff", text="Something easy.")
        c = self.result("staff", "Easy thing done.", own)
        m.verdict("checker", c, "supports", "test")
        # Judged only by its own model (another party's Haiku): not counted.
        c = self.result("staff", "Second thing done.", self.order(text="Second thing."))
        m.verdict("staff2", c, "supports", "cross_model_review")
        # A tool that merely has "opus" in its model's name: not a tier.
        c = self.result("linter", "Lint clean.", self.order(text="Lint it."))
        m.verdict("checker", c, "supports", "test")
        # Not an answer to any work order: not counted.
        c = m.remember("staff", claim("Free-standing claim.", speaker="staff", frame="firm/code", compartment="lab",
                                      stance="asserted", truth="true", origin="derived", confidence=6000))
        m.verdict("checker", c, "supports", "test")
        self.assertEqual(record_from(m, "john").counts, {})
        # Many verdicts on one result count once, by group majority.
        c = self.done("staff", "Third thing done.", ok=True)
        m.verdict("john", c, "supports", "human")
        self.assertEqual(record_from(m, "john").counts[("haiku", "code")], (1, 0))

    def test_the_record_is_only_what_the_reader_sees(self):
        m = self.m
        m.register("john", "outsider", party="out-co", kind="human", capabilities=["write"],
                   compartments=["elsewhere"])
        self.done("staff", "Seen work.", ok=True)
        self.assertEqual(record_from(m, "outsider").counts, {})

    def test_the_mcp_tool_plans_from_the_record(self):
        for i in range(30):
            self.done("staff", f"Task {i} done.", ok=False)
        bad = srv_bad = None  # noqa: F841
        srv = Server(self.log, "john")
        text, out = srv.tool("plan_team", {"kind": "code", "input_tokens": 20000, "output_tokens": 3000,
                                           "stakes_usd": 5, "check": "exact", "parts": 4, "step_usd": 0.05})
        self.assertIn("one opus agent alone", text)
        self.assertNotIn("haiku", out["each_part"]["workers"])
        self.assertEqual(out["record"][0]["on_record"], 30)
        for args in ({"stakes_usd": "abc"}, {"stakes_usd": [1]}, {"stakes_usd": True}, {"stakes_usd": -1000},
                     {"step_usd": -5}, {"input_tokens": 10**400}, {"parts": 0}):
            full = {"kind": "code", "input_tokens": 1, "output_tokens": 1, "stakes_usd": 1, **args}
            msg = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                              "params": {"name": "plan_team", "arguments": full}})
            res = msg.get("result", {})
            self.assertTrue(res.get("isError") or "error" in msg, (args, msg))
            self.assertEqual(msg["id"], 7)


if __name__ == "__main__":
    unittest.main()
