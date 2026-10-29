"""Issue #48, arm I2: from protocol version 7 an instance's id is exactly '<parent>-<n>', n one
more than the instances the parent has started: the sender chooses nothing. Version 6 logs keep
the old rule."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g
from gnomon.session import instance_for

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def code(fn):
    try:
        fn()
        return "accepted"
    except g.GateError as e:
        return e.code


def make(path, version=None):
    m = g.Memory(path)
    if version is None:
        m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    else:
        m.append("o", "register", {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner"],
                                   "compartments": ["a", "b"], "schema_version": version}, T(0))
    for w, c in (("c", "a"), ("c-x", "b")):
        m.register("o", w, party="p", kind="model", model="m", operator="o", name="C",
                   capabilities=["write", "spawn"], compartments=[c], record_time=T(1))
    return m


def inst(m, parent, wid, comp):
    return code(lambda: m.register(parent, wid, party="p", kind="model", model="m", operator="o", name="C",
                                   capabilities=["write"], compartments=[comp], record_time=T(2)))


class GateAssignedIds(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_the_next_number_is_accepted_and_nothing_else(self):
        m = make(self.path)
        self.assertEqual(inst(m, "c", "c-2", "a"), "E_SPAWN")
        self.assertEqual(inst(m, "c", "c-x-1", "a"), "E_SPAWN")
        self.assertEqual(inst(m, "c", "c-1", "a"), "accepted")
        self.assertEqual(inst(m, "c", "c-1", "a"), "E_SPAWN")
        self.assertEqual(inst(m, "c", "c-2", "a"), "accepted")
        self.assertEqual(inst(m, "c-x", "c-x-1", "b"), "accepted")

    def test_the_parent_counts_its_own_instances_only(self):
        m = make(self.path)
        inst(m, "c", "c-1", "a")
        self.assertEqual(inst(m, "c-x", "c-x-1", "b"), "accepted")  # c's count does not move c-x's

    def test_a_refusal_depends_only_on_the_senders_own_instances(self):
        outs = []
        for hidden in (True, False):
            with tempfile.TemporaryDirectory() as d:
                m = make(Path(d) / "m.jsonl")
                if hidden:
                    inst(m, "c", "c-1", "a")
                    inst(m, "c", "c-x-1", "a")  # refused
                outs.append([inst(m, "c-x", f"c-x-{i}", "b") for i in (0, 1, 2)])
        self.assertEqual(outs[0], outs[1])

    def test_version_6_logs_keep_the_old_rule(self):
        m = make(self.path, version=6)
        self.assertEqual(inst(m, "c", "c-7", "a"), "accepted")

    def test_session_instances_still_start(self):
        m = make(self.path)
        w1 = instance_for(m, "c-x", "s1")
        w2 = instance_for(m, "c-x", "s2")
        self.assertEqual((w1, w2), ("c-x-1", "c-x-2"))
        self.assertEqual(instance_for(m, "c-x", "s1"), "c-x-1")

    def test_session_instances_on_an_old_log_with_a_gap(self):
        m = make(self.path, version=6)
        inst(m, "c", "c-1", "a")
        inst(m, "c", "c-3", "a")
        self.assertEqual(instance_for(m, "c", "s9"), "c-2")


if __name__ == "__main__":
    unittest.main()
