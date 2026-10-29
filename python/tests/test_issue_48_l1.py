"""Issue #48, arm L1: from protocol version 7 a scheduler leases only to a writer it shares a
compartment with (one refusal, E_UNKNOWN_WRITER, for hidden and missing alike), and E_LEASED names
the holder and expiry only if the holder shares a compartment with the scheduler."""

import tempfile
import unittest
from pathlib import Path

import gnomon as g

T = lambda n: f"2026-01-01T00:{n:02d}:00Z"  # noqa: E731
FAR = "2099-01-01T00:00:00Z"


def make(path, version=None):
    m = g.Memory(path)
    if version is None:
        m.register("o", "o", party="o", kind="human", capabilities=["owner"], compartments=["a", "b"], record_time=T(0))
    else:
        m.append("o", "register", {"writer": "o", "party": "o", "kind": "human", "capabilities": ["owner"],
                                   "compartments": ["a", "b"], "schema_version": version}, T(0))
    for w, caps, c in (("sa", ["write", "schedule"], ["a"]), ("sb", ["write", "schedule"], ["b"]),
                       ("wa", ["write"], ["a"]), ("wb", ["write"], ["b"]), ("wb2", ["write"], ["b"])):
        m.register("o", w, party="p" + w, kind="model", model="m", operator="o", capabilities=caps, compartments=c,
                   record_time=T(1))
    return m


def attempt(fn):
    try:
        return "accepted", str(fn())
    except g.GateError as e:
        return e.code, e.message


class SchedulerScope(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "m.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_scheduler_can_still_lease_to_a_writer_in_its_compartment(self):
        m = make(self.path)
        self.assertEqual(attempt(lambda: m.lease("sb", "wb", "f", FAR, record_time=T(2)))[0], "accepted")

    def test_hidden_and_missing_get_one_answer(self):
        m = make(self.path)
        hidden = attempt(lambda: m.lease("sb", "wa", "f", FAR, record_time=T(2)))
        missing = attempt(lambda: m.lease("sb", "wa-nope", "f", FAR, record_time=T(2)))
        self.assertEqual(hidden[0], missing[0])
        self.assertEqual(hidden[1].replace("wa", "X"), missing[1].replace("wa-nope", "X"))

    def test_leased_does_not_name_a_hidden_holder_or_expiry(self):
        m = make(self.path)
        m.lease("sa", "wa", "shared", FAR, record_time=T(2))
        code, msg = attempt(lambda: m.lease("sb", "wb", "shared", FAR, record_time=T(3)))
        self.assertEqual(code, "E_LEASED")
        self.assertNotIn("wa", msg.replace("shared", ""))
        self.assertNotIn("2099", msg)

    def test_leased_still_names_a_visible_holder(self):
        m = make(self.path)
        m.lease("sb", "wb", "shared", FAR, record_time=T(2))
        code, msg = attempt(lambda: m.lease("sb", "wb2", "shared", FAR, record_time=T(3)))
        self.assertEqual(code, "E_LEASED")
        self.assertIn("wb", msg)
        self.assertIn("2099", msg)

    def test_version_6_logs_keep_the_old_rule(self):
        m = make(self.path, version=6)
        self.assertEqual(attempt(lambda: m.lease("sb", "wa", "f", FAR, record_time=T(2)))[0], "accepted")


if __name__ == "__main__":
    unittest.main()
