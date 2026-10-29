"""Issue #48, arm T1: from protocol version 7 the clock rule ("record time never runs backwards")
compares a new event's time only with events touching the writer's compartments (and global
ones). A single-compartment log still refuses an out-of-order time; version 6 logs keep the
whole-log rule."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731


def make(path, version=None, single=False):
    m = g.Memory(path)
    comps = ["a"] if single else ["a", "b"]
    if version is None:
        m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=comps, record_time=T(0))
    else:
        m.append("o", "register", {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner"],
                                   "compartments": comps, "schema_version": version}, T(0))
    for w, c in (("wa", ["a"]), ("wa2", ["a"]), ("wb", ["b"] if not single else ["a"]), ("x", comps)):
        m.register("o", w, party="p" + w, kind="model", model="m", operator="o", capabilities=["write"],
                   compartments=c, record_time=T(1))
    return m


def say(m, w, comp, t):
    try:
        m.remember(w, g.claim(f"{w}@{t}", speaker=w, frame="f", compartment=comp), t)
        return "accepted"
    except g.GateError as e:
        return e.code


class ClockScope(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_hidden_later_event_no_longer_blocks_an_earlier_write(self):
        m = make(self.path)
        say(m, "wa", "a", T(9))
        self.assertEqual(say(m, "wb", "b", T(5)), "accepted")

    def test_the_two_worlds_agree(self):
        outs = []
        for hidden in (True, False):
            with tempfile.TemporaryDirectory() as d:
                m = make(Path(d) / "m.jsonl")
                if hidden:
                    say(m, "wa", "a", T(9))
                outs.append(say(m, "wb", "b", T(5)))
        self.assertEqual(outs[0], outs[1])

    def test_within_a_compartment_time_still_may_not_go_back(self):
        m = make(self.path)
        say(m, "wa", "a", T(9))
        self.assertEqual(say(m, "wa2", "a", T(5)), "E_CLOCK")
        say(m, "wb", "b", T(7))
        self.assertEqual(say(m, "wb", "b", T(6)), "E_CLOCK")

    def test_a_shared_writer_is_held_to_both_of_its_compartments(self):
        m = make(self.path)
        say(m, "wa", "a", T(9))
        self.assertEqual(say(m, "x", "b", T(5)), "E_CLOCK")

    def test_a_single_compartment_log_still_refuses_an_out_of_order_time(self):
        m = make(self.path, single=True)
        say(m, "wa", "a", T(9))
        self.assertEqual(say(m, "wb", "a", T(5)), "E_CLOCK")

    def test_version_6_logs_keep_the_whole_log_rule(self):
        m = make(self.path, version=6)
        say(m, "wa", "a", T(9))
        self.assertEqual(say(m, "wb", "b", T(5)), "E_CLOCK")

    def test_the_log_replays_and_verifies(self):
        m = make(self.path)
        say(m, "wa", "a", T(9))
        say(m, "wb", "b", T(5))
        self.assertTrue(m.verify()["ok"])

    def test_known_cost_as_of_stops_at_the_first_later_dated_event(self):
        """as_of replays a prefix of the log. With out-of-order times across compartments the prefix
        ends at the first event dated after the cut, so b's earlier-dated event is not in it."""
        m = make(self.path)
        say(m, "wa", "a", T(9))
        say(m, "wb", "b", T(5))
        st = m.state(as_of=T(6))["state"]
        self.assertEqual([c["body"]["statement"] for c in st["claims"].values()], [])


if __name__ == "__main__":
    unittest.main()
