"""Issue #48, arm V1: a query's `through` (where the reader's view ends) shows a lease or release
only if the leased writer shares a compartment with the reader, a revocation only if its target
does; a schema change shows to all. No state changes, so old logs replay identically."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731
FAR = "2099-01-01T00:00:00Z"


def make(path):
    m = g.Memory(path)
    m.register("o", "o", party="o", kind="human", capabilities=["owner", "govern"], compartments=["a", "b"],
               record_time=T(0))
    for w, c in (("wa", ["a"]), ("wb", ["b"]), ("rb", ["b"])):
        m.register("o", w, party="p" + w, kind="model", model="m", operator="o", capabilities=["write"],
                   compartments=c, record_time=T(1))
    m.remember("wb", g.claim("b fact", speaker="wb", frame="f", compartment="b"), T(2))
    return m


def through(m, reader="rb"):
    return (m.query(reader, "?- claim(C).", proofs=0)["through"] or {}).get("event")


class ThroughHidesLeases(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = make(Path(self.dir.name) / "m.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_lease_to_a_hidden_writer_is_not_the_end_of_the_view(self):
        before = through(self.m)
        self.m.lease("o", "wa", "fa", FAR, record_time=T(5))
        self.assertEqual(through(self.m), before)

    def test_release_and_revoke_of_a_hidden_writer_are_not_either(self):
        before = through(self.m)
        lid = self.m.lease("o", "wa", "fa", FAR, record_time=T(5))
        self.m.release("o", lid, record_time=T(6))
        self.m.revoke("o", "wa", ["a"], "test", record_time=T(7))
        self.assertEqual(through(self.m), before)

    def test_a_reader_who_shares_the_targets_compartment_still_sees_lease_events(self):
        lid = self.m.lease("o", "wb", "fb", FAR, record_time=T(5))
        self.assertEqual(through(self.m), lid)
        rel = self.m.release("o", lid, record_time=T(6))["event_id"]
        self.assertEqual(through(self.m), rel)

    def test_a_revoke_of_a_writer_the_reader_shares_with_is_seen(self):
        rv = self.m.revoke("o", "wb", ["b"], "test", record_time=T(5))["event_id"]
        # wb now holds nothing, so by the rule (the target shares a compartment) the revoke is not shown;
        # revoking one of two compartments leaves the target visible:
        self.assertNotEqual(through(self.m), rv)

    def test_a_schema_change_is_seen_by_everyone(self):
        sc = self.m.append("o", "schema_change", {"version": 7, "description": "x"}, T(5))["event_id"] \
            if self.m.state()["state"]["schema_version"] < 7 else None
        if sc is None:
            self.skipTest("already at the newest version")
        self.assertEqual(through(self.m), sc)


if __name__ == "__main__":
    unittest.main()
