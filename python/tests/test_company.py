"""A company of models: founded with powers kept apart, staffed by hiring
instances into titles, given a client's goal split across levels, judged
by an independent assurance function, and reviewed from the record."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from gnomon import GateError, Memory  # noqa: E402
from gnomon.company import Company, Policy, check_preregistration, preregister  # noqa: E402


class CompanyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        m = self.m = Memory(str(Path(self.dir.name) / "m.jsonl"))
        m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                   compartments=["acme"], record_time="2026-01-01T00:00:00Z")
        tiers = {}
        for tier, model in (("haiku", "claude-haiku-5-5"), ("sonnet", "claude-sonnet-5-5"), ("opus", "claude-opus-5-5")):
            m.register("john", f"acme-{tier}", party="acme", kind="model", model=model, operator="john",
                       capabilities=["write", "spawn"], compartments=["acme"])
            tiers[tier] = f"acme-{tier}"
        # The test runner: a tool of another party, so its verdicts count.
        m.register("john", "tests", party="test-runner", kind="tool", model="runner-1", operator="john",
                   capabilities=["write", "verify"], compartments=["acme"])
        # The client: Claude in a chat, outside the company.
        m.register("john", "client", party="anthropic-chat", kind="model", model="claude-opus-5-5",
                   operator="john", capabilities=["write"], compartments=["acme"])
        self.c = Company.found(m, "john", "acme", "acme", tiers)

    def tearDown(self):
        self.dir.cleanup()

    def staff(self):
        c = self.c
        ceo = c.hire("john", "CEO", "opus", "founding chief executive")
        c.appoint("john", "Head of Assurance", "tests")
        cos = c.hire(ceo, "Chief of Staff", "sonnet", "runs allocation")
        c.department(ceo, "Engineering Lead", "CEO", "Owns engineering delivery.")
        lead = c.hire(ceo, "Engineering Lead", "sonnet", "engineering")
        c.department(lead, "Engineer", "Engineering Lead", "Builds what a work order asks.")
        engs = [c.hire(lead, "Engineer", "haiku", "ground work") for _ in range(3)]
        return ceo, cos, lead, engs

    def test_founding_keeps_the_powers_apart(self):
        ceo, cos, lead, engs = self.staff()
        pos = self.c.positions()
        self.assertIsNone(pos["Head of Assurance"]["reports_to"])
        self.assertEqual(pos["Chief of Staff"]["reports_to"], "CEO")
        # The CEO cannot replace or dismiss assurance; only the owners can.
        with self.assertRaises(GateError):
            self.c.appoint(ceo, "Head of Assurance", engs[0])
        a = [k for k, v in self.c.appointments().items() if v["title"] == "Head of Assurance"][0]
        with self.assertRaises(GateError):
            self.m.dismiss(ceo, a, "inconvenient findings")
        # Engineers cannot hire, and the Lead cannot hire a CEO.
        with self.assertRaises(GateError):
            self.c.hire(engs[0], "Engineer", "haiku", "a friend")
        with self.assertRaises(GateError):
            self.c.hire(lead, "CEO", "opus", "a coup")
        # Reopening finds the same company.
        again = Company.found(self.m, "john", "acme", "acme", self.c.templates)
        self.assertEqual(sorted(again.positions()), sorted(self.c.positions()))

    def test_a_preregistered_protocol_cannot_be_swapped_or_backdated(self):
        proto = "Floor protocol v1\nshare chosen on one half, scored on the other"
        reg = preregister(self.m, "john", proto, compartment="acme")
        result = self.c.note("john", "Floor held out: 0.925", channel="results")
        self.assertTrue(check_preregistration(self.m, reg["claim"], proto, result, "acme")["ok"])
        self.assertFalse(check_preregistration(self.m, reg["claim"], proto + " (edited)", result, "acme")["ok"])
        # A protocol whose text embeds another's hash does not pass for it.
        other = "sha256:" + __import__("hashlib").sha256(proto.encode()).hexdigest() + " decoy"
        reg2 = preregister(self.m, "john", other, compartment="acme")
        self.assertFalse(check_preregistration(self.m, reg2["claim"], proto, result, "acme")["ok"])
        # A plain note holding a hash is not a registration.
        note = self.c.note("john", "Preregistered protocol sha256:"
                           + __import__("hashlib").sha256(proto.encode()).hexdigest(), channel="misc")
        self.assertFalse(check_preregistration(self.m, note, proto, result, "acme")["ok"])
        late = preregister(self.m, "john", "Protocol written after the fact", compartment="acme")
        r = check_preregistration(self.m, late["claim"], "Protocol written after the fact", result, "acme")
        self.assertFalse(r["ok"])
        self.assertFalse(r["registered_first"])

    def test_refused_personnel_actions_leave_no_trace(self):
        ceo, cos, lead, engs = self.staff()
        c = self.c
        before = len(self.m.writers())
        with self.assertRaises(GateError):
            c.hire(engs[0], "Engineer", "haiku", "a friend")
        self.assertEqual(len(self.m.writers()), before)  # no orphan instance
        a = [k for k, v in c.appointments().items() if v["holder"] == engs[1]][0]
        handoffs = len(self.m.state(compartments=["acme"])["state"]["handoffs"])
        with self.assertRaises(GateError):
            c.offboard(engs[0], a, "a peer's grudge", "their work")
        self.assertEqual(len(self.m.state(compartments=["acme"])["state"]["handoffs"]), handoffs)
        self.assertIn(a, c.appointments())
        # Work is spread without a load map: each delivery moves the next order on.
        first = c.assignee("Engineer")
        c.deliver(first, c.order("client", "One thing.", "code"), "code", "Done.")
        self.assertNotEqual(c.assignee("Engineer"), first)

    def test_a_goal_split_across_levels_and_judged_independently(self):
        ceo, cos, lead, engs = self.staff()
        c = self.c
        goal = c.order("client", "Build a CSV-to-JSON converter with tests.", "code")
        c.structure(ceo, goal, "One lead, three engineers in parallel (parser, writer, CLI); assurance runs the "
                               "tests. Considered: one engineer alone (slower), a reviewing tier (no exact check gap).")
        load: dict = {}
        delivered = []
        for part in ("parser", "writer", "CLI"):
            sub = c.order(lead, f"Implement the {part}.", "code", parent=goal)
            w = c.assignee("Engineer", load)
            load[w] = load.get(w, 0) + 1
            delivered.append(c.deliver(w, sub, "code", f"The {part} is implemented and its tests pass."))
        self.assertEqual(len(set(load)), 3, "work spread over the engineers")
        for r in delivered:
            self.m.verdict("tests", r, "supports", "test")
        # Same party: an engineer cannot verify another engineer's work.
        with self.assertRaises(GateError):
            self.m.verdict(engs[0], delivered[1], "supports", "cross_model_review")
        review = {x["writer"]: x for x in c.review("john", Policy(min_judged=1))}
        self.assertEqual(sum(review[e]["right"] for e in engs), 3)
        self.assertEqual(review[ceo]["judged"], 0)

    def test_review_and_offboarding(self):
        ceo, cos, lead, engs = self.staff()
        c = self.c
        weak, strong = engs[0], engs[1]
        for i in range(6):
            o = c.order(lead, f"Task {i}.", "code")
            self.m.verdict("tests", c.deliver(weak, o, "code", f"Task {i} done."), "refutes", "test")
            o2 = c.order(lead, f"Task {i}b.", "code")
            self.m.verdict("tests", c.deliver(strong, o2, "code", f"Task {i}b done."), "supports", "test")
        # Asking for help is never held against anyone.
        c.note(engs[2], "I could not finish task 7; escalating to the Lead.", channel="escalations")
        r = {x["writer"]: x for x in c.review("john", Policy(min_judged=5))}
        self.assertEqual(r[weak]["proposal"], "dismiss")
        self.assertEqual(r[strong]["proposal"], "keep")
        self.assertEqual(r[engs[2]]["proposal"], "keep")
        appt = [k for k, v in c.appointments().items() if v["holder"] == weak][0]
        c.offboard(lead, appt, "Six results in a row failed their tests.", "Tasks 0-5 failed; nothing in progress.")
        self.assertNotIn(weak, c.holders("Engineer"))
        handoffs = self.m.state(compartments=["acme"])["state"]["handoffs"]
        self.assertTrue(any(h["writer"] == weak for h in handoffs.values()))

    def test_roles_from_agent_files_and_journals(self):
        from gnomon.company import roles_from_agent_files
        d = Path(self.dir.name) / "agents"
        d.mkdir()
        (d / "code-reviewer.md").write_text("---\nname: code-reviewer\ndescription: Reviews code for quality.\n"
                                             "tools: Read, Grep\nmodel: sonnet\n---\nBody.\n")
        (d / "planner.md").write_text("---\nname: planner\ndescription: Plans the work.\nmodel: opus\n---\n")
        (d / "broken.md").write_text("no front matter")
        roles = roles_from_agent_files(str(d))
        self.assertEqual([(r.title, r.tier) for r in roles], [("Code Reviewer", "sonnet"), ("Planner", "opus")])
        self.assertEqual(roles[0].tools, ("Read", "Grep"))
        ceo, cos, lead, engs = self.staff()
        self.assertEqual(self.c.adopt_roles(ceo, "CEO", roles), ["Code Reviewer", "Planner"])
        self.assertEqual(self.c.adopt_roles(ceo, "CEO", roles), [])
        self.c.journal(engs[0], "Started on the parser; the spec is unclear about quoted newlines.")
        st = self.m.state(compartments=["acme"])["state"]
        self.assertTrue(any(c["body"]["frame"] == f"org/acme/journal/{engs[0]}" for c in st["claims"].values()))


if __name__ == "__main__":
    unittest.main()
