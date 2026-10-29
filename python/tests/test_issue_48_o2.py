"""Issue #48, arm O2: from protocol version 7 every registration (the owner's too) of a writer with an
operator is refused (E_IDENTITY) unless the operator holds every compartment the writer holds. The
refusal does not name the operator's compartments. Version 6 logs keep the old rule."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def make(path, version=None):
    m = g.Memory(path)
    if version is None:
        m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b", "zq-secret"],
                   record_time=T(0))
    else:
        m.append("o", "register", {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner"],
                                   "compartments": ["a", "b", "zq-secret"], "schema_version": version}, T(0))
    m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=["zq-secret", "a"],
               record_time=T(1))
    return m


def bot(m, wid, comps):
    try:
        m.register("o", wid, party="p" + wid, kind="model", model="m", operator="op", capabilities=["write"],
                   compartments=comps, record_time=T(2))
        return "accepted", ""
    except g.GateError as e:
        return e.code, e.message


class OperatorOnHand(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_an_owner_registered_writer_with_the_operator_in_all_its_compartments_is_accepted(self):
        m = make(self.path)
        self.assertEqual(bot(m, "bot1", ["a"])[0], "accepted")
        self.assertEqual(bot(m, "bot2", ["a", "zq-secret"])[0], "accepted")

    def test_refused_when_the_operator_lacks_one_and_the_refusal_names_no_compartment(self):
        m = make(self.path)
        code, msg = bot(m, "bot3", ["b"])
        self.assertEqual(code, "E_IDENTITY")
        for name in ("zq-secret", "'a'", "'b'"):
            self.assertNotIn(name, msg)

    def test_the_refusal_is_the_same_whatever_the_operator_holds(self):
        outs = []
        for comps in (["zq-secret", "a"], ["a"]):  # differ only in a compartment the writer's reader cannot see
            with tempfile.TemporaryDirectory() as d:
                m = g.Memory(Path(d) / "m.jsonl")
                m.register("o", "o", party="o", kind="human", capabilities=["owner"],
                           compartments=["a", "b", "zq-secret"], record_time=T(0))
                m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=comps,
                           record_time=T(1))
                outs.append(bot(m, "bot", ["b"]))
        self.assertEqual(outs[0], outs[1])

    def test_version_6_logs_keep_the_old_rule(self):
        m = make(self.path, version=6)
        self.assertEqual(bot(m, "bot4", ["b"])[0], "accepted")


if __name__ == "__main__":
    unittest.main()
