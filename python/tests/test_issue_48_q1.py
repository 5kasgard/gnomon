"""Issue #48, arm Q1: from protocol version 7 a writer's events are numbered per compartment
(event id '<writer>/<compartment>/<n>'), so a reader's view of a writer has no gaps and a write's
returned id counts only that compartment. Citations, supersessions and handoffs still resolve."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def make(path, version=None):
    m = g.Memory(path)
    if version is None:
        m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    else:
        m.append("o", "register", {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner"],
                                   "compartments": ["a", "b"], "schema_version": version}, T(0))
    m.register("o", "x", party="px", kind="model", model="m", operator="o", capabilities=["write", "verify"],
               compartments=["a", "b"], record_time=T(1))
    return m


def say(m, comp, text, t, **kw):
    return m.append("x", "claim", g.claim(text, speaker="x", frame="f", compartment=comp, **kw), t)["event_id"]


class PerCompartmentNumbers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_ids_count_per_compartment(self):
        m = make(self.path)
        self.assertEqual(say(m, "b", "b1", T(2)), "x/b/1")
        self.assertEqual([say(m, "a", f"a{i}", T(3)) for i in range(3)], ["x/a/1", "x/a/2", "x/a/3"])
        self.assertEqual(say(m, "b", "b2", T(4)), "x/b/2")

    def test_a_b_reader_sees_no_gap_and_the_write_return_does_not_depend_on_a(self):
        outs = []
        for hidden in (True, False):
            with tempfile.TemporaryDirectory() as d:
                m = make(Path(d) / "m.jsonl")
                say(m, "b", "b1", T(2))
                if hidden:
                    for i in range(3):
                        say(m, "a", f"a{i}", T(3))
                ret = say(m, "b", "b2", T(4))
                evs = sorted(c["first_event"] for c in m.state(compartments=["b"])["state"]["claims"].values())
                outs.append((ret, evs))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0][1], ["x/b/1", "x/b/2"])

    def test_the_seq_error_names_a_per_compartment_number(self):
        m = make(self.path)
        say(m, "a", "a1", T(2))
        draft = {"writer": "x", "record_time": T(3), "kind": "claim",
                 "body": g.claim("b1", speaker="x", frame="f", compartment="b")}
        self.assertEqual(g.Memory(self.path).append("x", "claim", draft["body"], T(3))["writer_seq"], 1)

    def test_citations_supersessions_and_handoffs_resolve(self):
        m = make(self.path)
        c1 = m.remember("x", g.claim("first", speaker="x", frame="f", compartment="b"), T(2))
        say(m, "a", "elsewhere", T(3))
        c2 = m.remember("x", g.claim("second", speaker="x", frame="f", compartment="b",
                                     about=[{"claim": c1, "relation": "cites"}]), T(4))
        m.supersede("x", c1, c2, "generalised", [c2], "r", T(6))
        h1 = m.handoff("x", frame="f", summary="s1", compartment="b", refs=[c1, c2], record_time=T(7))
        h2 = m.handoff("x", frame="f", summary="s2", compartment="b", follows=h1, record_time=T(8))
        st = m.state(compartments=["b"])["state"]
        self.assertEqual(len(st["supersessions"]), 1)
        self.assertEqual({h1, h2} - set(st["handoffs"]), set())
        self.assertTrue(m.verify()["ok"])

    def test_events_with_no_single_compartment_keep_the_plain_counter(self):
        m = make(self.path)
        self.assertEqual(m.lease("o", "x", "other", "2099-01-01T00:00:00Z", record_time=T(2)), "o/3")
        self.assertEqual(say(m, "a", "a1", T(3)), "x/a/1")

    def test_version_6_logs_keep_one_counter(self):
        m = make(self.path, version=6)
        self.assertEqual([say(m, "a", "a1", T(2)), say(m, "b", "b1", T(3))], ["x/1", "x/2"])


if __name__ == "__main__":
    unittest.main()
