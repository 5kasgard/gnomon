"""Issue #48 follow-ups I2b and G (RESULTS.md, "Follow-ups I2b and G").

f1: an owner-registered `c-1`, hidden from `c`, does not stop `instance_for(c)` from starting an instance.
f2: two Memory objects on one log path both start an instance of the same parent, the second having
    read the writers before the first registered: the stale id gets E_DUPLICATE and instance_for recovers.
f3: on a version-6 log, the operator (P10), `through` (V) and session-id (P11) channels do not cross (G).
"""

import tempfile
import unittest
from pathlib import Path

import gnomon as g
from gnomon.session import instance_for

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731
FAR = "2099-01-01T00:00:00Z"


def code(fn):
    try:
        fn()
        return "accepted"
    except g.GateError as e:
        return e.code


def genesis(m, version=None):
    body = {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner", "govern"],
            "compartments": ["a", "b"]}
    if version is not None:
        body["schema_version"] = version
        m.append("o", "register", body, T(0))
    else:
        m.register("o", "o", party="o", kind="human", capabilities=["owner", "govern"], compartments=["a", "b"],
                   record_time=T(0))


def parent(m, comps=("a",)):
    m.register("o", "c", party="p", kind="model", model="m", operator="o", name="C",
               capabilities=["write", "spawn"], compartments=list(comps), record_time=T(1))


class F1HiddenIdDoesNotBlockTheParent(unittest.TestCase):
    def test_instance_for_starts_an_instance_past_a_hidden_id(self):
        with tempfile.TemporaryDirectory() as d:
            m = g.Memory(Path(d) / "m.jsonl")
            genesis(m)
            parent(m)
            # an owner registers c-1 in a compartment c cannot see
            m.register("o", "c-1", party="p", kind="model", model="m", operator="o", name="C",
                       capabilities=["write"], compartments=["b"], record_time=T(2))
            self.assertEqual(instance_for(m, "c", "s1"), "c-2")
            self.assertEqual(instance_for(m, "c", "s2"), "c-3")
            self.assertEqual(instance_for(m, "c", "s1"), "c-2")


class F2TwoMemoriesOneLog(unittest.TestCase):
    def test_a_race_loser_is_told_duplicate_and_instance_for_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.jsonl"
            m1, m2 = g.Memory(path), g.Memory(path)
            genesis(m1)
            parent(m1)
            stale = m2.writers()  # m2 reads the writers before m1 registers
            self.assertNotIn("c-1", stale)
            self.assertEqual(instance_for(m1, "c", "s1"), "c-1")
            # m2 acts on its stale view: the gate, not the library, decides
            lost = code(lambda: m2.register("c", "c-1", party="p", kind="model", model="m", operator="o",
                                            name="C", capabilities=["write"], compartments=["a"],
                                            session="s2"))
            self.assertEqual(lost, "E_DUPLICATE")  # not E_SPAWN: the loser can retry
            self.assertEqual(instance_for(m2, "c", "s2"), "c-2")
            self.assertEqual(sorted(w for w in m1.writers() if w.startswith("c-")), ["c-1", "c-2"])


class F3VersionSixLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def world(self, name, hidden):
        m = g.Memory(self.root / f"{name}.jsonl")
        genesis(m, version=6)
        self.assertEqual(m.state()["state"]["schema_version"], 6)
        m.register("o", "op", party="po", kind="human", capabilities=["write"], compartments=["a"], record_time=T(1))
        for w, c in (("wa", "a"), ("wb", "b"), ("rb", "b")):
            m.register("o", w, party="p" + w, kind="model", model="m", operator="op" if w == "wb" else "o",
                       capabilities=["write"], compartments=[c], record_time=T(1))
        m.remember("wb", g.claim("b fact", speaker="wb", frame="f", compartment="b"), T(2))
        return m

    def test_p10_operator_is_hidden(self):
        m = self.world("p10", True)
        self.assertNotIn("operator", m.writers(compartments=["b"])["wb"])
        ans = m.query("rb", '?- identity("wb", "operator", V).', proofs=0)["answers"]
        self.assertEqual(ans, [])

    def test_v_through_ignores_hidden_leases(self):
        m = self.world("v", True)
        through = lambda: (m.query("rb", "?- claim(C).", proofs=0)["through"] or {}).get("event")  # noqa: E731
        before = through()
        lid = m.lease("o", "wa", "fa", FAR, record_time=T(5))
        m.release("o", lid, record_time=T(6))
        self.assertEqual(through(), before)

    def test_p11_session_in_another_compartment_is_not_a_collision(self):
        outs = []
        for hidden in (True, False):
            m = self.world(f"p11{hidden}", hidden)
            if hidden:
                m.register("o", "sa", party="anthropic", kind="model", model="m", operator="o", name="Claude",
                           surface="cc", capabilities=["write"], compartments=["a"], session="s", record_time=T(3))
            outs.append(code(lambda: m.register(
                "o", "sb", party="anthropic", kind="model", model="m", operator="o", name="Claude", surface="cc",
                capabilities=["write"], compartments=["b"], session="s", record_time=T(4))))
        self.assertEqual(outs, ["accepted", "accepted"])


if __name__ == "__main__":
    unittest.main()
