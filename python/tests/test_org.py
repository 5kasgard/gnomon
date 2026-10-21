"""Organisations from Python: positions, appointments, dismissals, and a
query about them whose proofs pass the independent checker."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from gnomon import VIEWS, GateError, Memory, proofs  # noqa: E402


class Org(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "m.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", capabilities=["owner"], compartments=["acme", "other"],
                   record_time="2026-01-01T00:00:00Z")
        for w, model in (("ceo", "claude-opus-5-5"), ("lead", "claude-sonnet-5-5"), ("eng1", "claude-haiku-5-5"),
                         ("eng2", "claude-haiku-5-5")):
            m.register("john", w, party=w, kind="model", model=model, operator="john", capabilities=["write"],
                       compartments=["acme"])
        m.position("john", "acme", "CEO", compartment="acme", duties="Sets direction and answers to the owner.")
        m.appoint("john", "acme", "CEO", "ceo", compartment="acme")
        m.position("ceo", "acme", "Lead", compartment="acme", duties="Runs engineering.", reports_to="CEO")
        self.lead = m.appoint("ceo", "acme", "Lead", "lead", compartment="acme")
        m.position("lead", "acme", "Engineer", compartment="acme", duties="Builds what is asked.", reports_to="Lead")
        m.appoint("lead", "acme", "Engineer", "eng1", compartment="acme")
        m.appoint("lead", "acme", "Engineer", "eng2", compartment="acme")

    def tearDown(self):
        self.dir.cleanup()

    def test_authority_and_proofs(self):
        with self.assertRaises(GateError) as e:
            self.m.appoint("eng1", "acme", "Engineer", "eng2", compartment="acme")
        self.assertEqual(e.exception.code, "E_ORG")
        q = '?- oversees(B, "eng1").'
        r = self.m.query("john", q)
        self.assertEqual(sorted(a["values"][0] for a in r["answers"]), ["ceo", "lead"])
        self.assertEqual(proofs.check(r, q, VIEWS, self.log, require_hashes=True), [])
        self.m.dismiss("ceo", self.lead, "Restructuring.")
        r = self.m.query("john", q)
        self.assertEqual([a["values"][0] for a in r["answers"]], ["ceo"])
        self.assertEqual(proofs.check(r, q, VIEWS, self.log, require_hashes=True), [])
        st = self.m.state(compartments=["acme"])["state"]
        self.assertEqual(len(st["positions"]), 3)
        self.assertEqual(sum(a["ended"] is not None for a in st["appointments"].values()), 1)
        self.assertEqual(self.m.state(compartments=["other"])["state"]["positions"], {})

    def test_a_forged_holds_fact_fails_the_checker(self):
        q = '?- holds(W, "acme", "Lead").'
        r = self.m.query("john", q)
        self.m.dismiss("ceo", self.lead, "Restructuring.")
        # The proof was valid when made; against the log now, the
        # appointment has ended, so the same proof no longer checks.
        self.assertTrue(proofs.check(r, q, VIEWS, self.log, require_hashes=True))


if __name__ == "__main__":
    unittest.main()
