"""Issue #54: from protocol version 6, leases are time-bounded holds on a frame,
with the lease's id as a fencing token (the tests listed in the issue)."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import gnomon as g
from gnomon.session import instance_for


def ts(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def world(path, version=None):
    m = g.Memory(path)
    body = {"party": "p-own", "kind": "human", "capabilities": ["owner", "govern"], "compartments": ["w"]}
    if version is None:
        m.register("own", "own", **body)
    else:
        m.append("own", "register", {"writer": "own", **body, "schema_version": version})
    m.register("own", "sched", party="p-sched", kind="tool", model="cron-1", operator="own",
               capabilities=["schedule", "write"], compartments=["w"])
    for r in ("run1", "run2"):
        m.register("own", r, party="p-" + r, kind="model", model="m", operator="own", surface="claude-code",
                   name=r, capabilities=["write", "spawn"], compartments=["w"])
    return m


def say(m, w, frame, lease=None, text="x"):
    body = g.claim(text, speaker=w, frame=frame, compartment="w")
    if lease:
        body["lease"] = lease
    return m.remember(w, body)


def code(fn):
    try:
        fn()
        return "OK"
    except g.GateError as e:
        return e.code


class Leases(unittest.TestCase):
    def setUp(self):
        self.m = world(f"{tempfile.mkdtemp()}/m.jsonl")
        self.l1 = self.m.lease("sched", "run1", "job", ts(minutes=30), "order 1")

    def test_the_holder_writes_naming_its_lease(self):
        m = self.m
        self.assertEqual(code(lambda: say(m, "run1", "job", self.l1)), "OK")
        self.assertEqual(code(lambda: m.handoff("run1", frame="job", summary="did A", compartment="w",
                                                lease=self.l1)), "OK")
        self.assertEqual(code(lambda: say(m, "run1", "job")), "E_LEASE")  # without naming it

    def test_others_are_kept_out_while_it_is_live(self):
        m = self.m
        self.assertEqual(code(lambda: say(m, "run2", "job")), "E_LEASE")
        self.assertEqual(code(lambda: say(m, "run2", "job", self.l1)), "E_LEASE")  # someone else's token
        self.assertEqual(code(lambda: say(m, "own", "job")), "OK")  # the owner needs no lease
        self.assertEqual(code(lambda: say(m, "run2", "other-frame")), "OK")

    def test_instances_of_the_holder(self):
        m = self.m
        w = instance_for(m, "run1", "sess-1")
        self.assertEqual(code(lambda: say(m, w, "job", self.l1)), "OK")

    def test_one_live_lease_per_frame(self):
        m = self.m
        self.assertEqual(code(lambda: m.lease("sched", "run2", "job", ts(minutes=30))), "E_LEASED")

    def test_after_release_the_stale_holder_is_fenced_off(self):
        m = self.m
        m.release("sched", self.l1, "run1 crashed")
        l2 = m.lease("sched", "run2", "job", ts(minutes=30), "order 1, retried")
        self.assertEqual(code(lambda: say(m, "run1", "job", self.l1)), "E_LEASE")  # stale token
        self.assertEqual(code(lambda: say(m, "run1", "job")), "E_LEASE")  # and no way round it
        self.assertEqual(code(lambda: say(m, "run2", "job", l2)), "OK")
        self.assertEqual(code(lambda: m.release("sched", self.l1)), "E_LEASE")  # already released

    def test_a_crashed_scheduler_cannot_leave_a_writer_holding_the_frame(self):
        m = self.m
        l = m.lease("sched", "run1", "short", ts(seconds=2))
        # Past expiry by the record's clock: the stale token is refused...
        late = dict(g.claim("late", speaker="run1", frame="short", compartment="w"), lease=l)
        self.assertEqual(code(lambda: m.remember("run1", late, record_time=ts(seconds=10))), "E_LEASE")
        # ...and the frame is free again, so a new lease can be granted.
        self.assertEqual(code(lambda: m.lease("sched", "run2", "short", ts(minutes=5), record_time=ts(seconds=11))), "OK")

    def test_the_gates_clock_beats_a_backdated_write(self):
        m = self.m
        l = m.lease("sched", "run1", "brief", ts(seconds=1))
        import time
        time.sleep(1.5)
        # Dated before expiry, but the gate's clock says the lease is over.
        body = dict(g.claim("sneaky", speaker="run1", frame="brief", compartment="w"), lease=l)
        self.assertEqual(code(lambda: m.remember("run1", body)), "E_LEASE")

    def test_who_may_lease_and_release(self):
        m = self.m
        self.assertEqual(code(lambda: m.lease("run1", "run2", "f2", ts(minutes=5))), "E_CAPABILITY")
        self.assertEqual(code(lambda: m.lease("own", "run1", "f2", ts(minutes=5))), "OK")  # the owner may
        self.assertEqual(code(lambda: m.lease("own", "own", "f3", ts(minutes=5))), "E_LEASE")
        self.assertEqual(code(lambda: m.lease("sched", "sched", "f4", ts(minutes=5))), "E_LEASE")  # never to itself
        self.assertEqual(code(lambda: m.release("run2", self.l1)), "E_CAPABILITY")
        self.assertEqual(code(lambda: m.release("run1", self.l1, "done early")), "OK")  # the holder may

    def test_bad_leases(self):
        m = self.m
        self.assertEqual(code(lambda: m.lease("sched", "run2", "f5", "tomorrow")), "E_BODY")
        self.assertEqual(code(lambda: m.lease("sched", "run2", "f6", ts(minutes=-5))), "E_LEASE")
        self.assertEqual(code(lambda: m.lease("sched", "nobody", "f7", ts(minutes=5))), "E_UNKNOWN_WRITER")
        self.assertEqual(code(lambda: say(m, "run1", "job", "sched/999")), "E_REF")

    def test_one_claim_whatever_the_lease(self):
        m = self.m
        a = say(m, "run1", "job", self.l1, "the same statement")
        self.assertEqual(a, g.claim_id(g.claim("the same statement", speaker="run1", frame="job", compartment="w")))

    def test_the_log_verifies(self):
        say(self.m, "run1", "job", self.l1)
        self.m.release("sched", self.l1)
        self.assertTrue(g.Memory(self.m.path).verify()["ok"])


class OlderLogs(unittest.TestCase):
    def test_needs_version_6(self):
        m = world(f"{tempfile.mkdtemp()}/v5.jsonl", version=5)
        self.assertEqual(code(lambda: m.lease("sched", "run1", "job", ts(minutes=5))), "E_SCHEMA_VERSION")
        self.assertEqual(code(lambda: say(m, "run1", "job", "x/1")), "E_SCHEMA_VERSION")
        m.append("own", "schema_change", {"version": 6, "description": "leases (#54)"})
        self.assertEqual(code(lambda: m.lease("sched", "run1", "job", ts(minutes=5))), "OK")


if __name__ == "__main__":
    unittest.main()
