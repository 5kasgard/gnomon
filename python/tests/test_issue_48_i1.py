"""Issue #48, arm I1: from protocol version 7 an instance's id starts with its parent's id and
with no other spawner's id (the longest registered spawner prefix wins), so two spawners can never
name the same id. Logs at version 6 or lower replay as before."""

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


class DisjointIds(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_new_memories_start_at_version_7(self):
        self.assertEqual(g.SCHEMA_VERSION, 7)
        self.assertEqual(make(self.path).state()["state"]["schema_version"], 7)

    def test_a_spawner_still_registers_its_own_instances(self):
        m = make(self.path)
        self.assertEqual(inst(m, "c", "c-1", "a"), "accepted")
        self.assertEqual(inst(m, "c-x", "c-x-1", "b"), "accepted")

    def test_the_parent_cannot_take_an_id_under_another_spawner(self):
        m = make(self.path)
        self.assertEqual(inst(m, "c", "c-x-1", "a"), "E_SPAWN")
        self.assertEqual(inst(m, "c", "c-x-1-2", "a"), "E_SPAWN")

    def test_the_refusal_does_not_depend_on_what_the_other_spawner_has(self):
        outs = []
        for hidden in (True, False):
            with tempfile.TemporaryDirectory() as d:
                m = make(Path(d) / "m.jsonl")
                if hidden:
                    inst(m, "c", "c-x-1", "a")  # refused, so nothing is recorded
                outs.append(inst(m, "c-x", "c-x-1", "b"))
        self.assertEqual(outs, ["accepted", "accepted"])

    def test_version_6_logs_keep_the_old_rule(self):
        m = make(self.path, version=6)
        self.assertEqual(inst(m, "c", "c-x-1", "a"), "accepted")

    def test_session_instances_still_start(self):
        m = make(self.path)
        w = instance_for(m, "c-x", "sess-1", surface=None)
        self.assertTrue(w.startswith("c-x-"))
        self.assertEqual(instance_for(m, "c-x", "sess-1"), w)


if __name__ == "__main__":
    unittest.main()
