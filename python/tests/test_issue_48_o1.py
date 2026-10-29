"""Issue #48, arm O1: from protocol version 7 a restricted view (writers, state, Datalog identity
facts) names a writer's operator only to a reader who shares a compartment with the operator."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def make(path, version=None, op_comps=("a",)):
    m = g.Memory(path)
    if version is None:
        m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    else:
        m.append("o", "register", {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner"],
                                   "compartments": ["a", "b"], "schema_version": version}, T(0))
    m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=list(op_comps), record_time=T(1))
    m.register("o", "bot", party="pb", kind="model", model="m", operator="op", capabilities=["write"],
               compartments=["b"], record_time=T(1))
    m.register("o", "wb", party="pwb", kind="human", capabilities=["write"], compartments=["b"], record_time=T(1))
    return m


def ident(m):
    return [a["values"] for a in m.query("wb", '?- identity("bot", "operator", V).', proofs=0)["answers"]]


class OperatorVisibility(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_hidden_from_a_reader_who_shares_nothing_with_the_operator(self):
        m = make(self.path)
        self.assertNotIn("operator", m.writers(compartments=["b"])["bot"])
        self.assertNotIn("operator", m.state(compartments=["b"])["state"]["writers"]["bot"])
        self.assertEqual(ident(m), [])

    def test_shown_to_a_reader_who_shares_a_compartment_with_it(self):
        m = make(self.path, op_comps=("a", "b"))
        self.assertEqual(m.writers(compartments=["b"])["bot"]["operator"], "op")
        self.assertEqual(ident(m), [["op"]])

    def test_the_full_view_still_shows_it(self):
        m = make(self.path)
        self.assertEqual(m.writers()["bot"]["operator"], "op")

    def test_two_worlds_differing_only_in_which_hidden_operator_look_alike(self):
        outs = []
        for who in ("op", "op2"):
            with tempfile.TemporaryDirectory() as d:
                m = make(Path(d) / "m.jsonl")
                m.register("o", "op2", party="po2", kind="human", capabilities=["write"], compartments=["a"],
                           record_time=T(1))
                m.register("o", "bot2", party="pb2", kind="model", model="m", operator=who, capabilities=["write"],
                           compartments=["b"], record_time=T(2))
                outs.append((m.writers(compartments=["b"])["bot2"], ident(m)))
        self.assertEqual(outs[0], outs[1])

    def test_version_6_logs_keep_showing_it(self):
        m = make(self.path, version=6)
        self.assertEqual(m.writers(compartments=["b"])["bot"]["operator"], "op")
        self.assertEqual(ident(m), [["op"]])


if __name__ == "__main__":
    unittest.main()
