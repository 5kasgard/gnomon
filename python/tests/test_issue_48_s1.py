"""Issue #48, arm S1: the one-session-one-writer check looks only at writers that share a
compartment with the one being registered, so its refusal says nothing about hidden compartments."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def code(fn):
    try:
        fn()
        return "accepted"
    except g.GateError as e:
        return e.code


class SessionScope(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = g.Memory(Path(self.dir.name) / "m.jsonl")
        self.m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"],
                        record_time=T(0))

    def tearDown(self):
        self.dir.cleanup()

    def reg(self, wid, comps, session="sess"):
        return code(lambda: self.m.register(
            "o", wid, party="anthropic", kind="model", model="m", operator="o", name="Claude",
            surface="claude-code", capabilities=["write"], compartments=comps, session=session, record_time=T(1)))

    def test_same_compartment_is_still_refused(self):
        self.assertEqual(self.reg("w1", ["a"]), "accepted")
        self.assertEqual(self.reg("w2", ["a"]), "E_IDENTITY")

    def test_overlapping_compartments_are_still_refused(self):
        self.assertEqual(self.reg("w1", ["a", "b"]), "accepted")
        self.assertEqual(self.reg("w2", ["b"]), "E_IDENTITY")

    def test_disjoint_compartments_do_not_collide(self):
        self.assertEqual(self.reg("w1", ["a"]), "accepted")
        self.assertEqual(self.reg("w2", ["b"]), "accepted")

    def test_a_refusal_in_b_does_not_depend_on_a(self):
        """Two logs differing only in an A-side session: B's attempt gets the same answer."""
        outs = []
        for hidden in (True, False):
            with tempfile.TemporaryDirectory() as d:
                m = g.Memory(Path(d) / "m.jsonl")
                m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"],
                           record_time=T(0))
                if hidden:
                    m.register("o", "wa", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                               surface="claude-code", capabilities=["write"], compartments=["a"], session="s",
                               record_time=T(1))
                outs.append(code(lambda: m.register(
                    "o", "wb", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                    surface="claude-code", capabilities=["write"], compartments=["b"], session="s",
                    record_time=T(2))))
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
