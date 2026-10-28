"""Issue #52 (a future-dated write locked the log) and #53 (handoff chains forked silently)."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gnomon as g


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fresh(path, version=None):
    m = g.Memory(path)
    body = {"party": "p-own", "kind": "human", "capabilities": ["owner"], "compartments": ["w"]}
    if version is None:
        m.register("own", "own", **body)
    else:  # an older protocol version, written by hand as an older Gnomon would have
        m.append("own", "register", {"writer": "own", **body, "schema_version": version})
    for w in ("r1", "r2"):
        m.register("own", w, party="p-" + w, kind="model", capabilities=["write"], compartments=["w"],
                   model="claude-haiku-5-5", operator="own")
    return m


class FutureWrites(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.m = fresh(f"{self.d}/m.jsonl")

    def claim(self, writer, when=None):
        return self.m.remember(writer, g.claim("x", speaker=writer, frame="f", compartment="w"), record_time=when)

    def test_far_future_write_is_refused(self):
        with self.assertRaises(g.GateError) as e:
            self.claim("r1", "2099-01-01T00:00:00Z")
        self.assertEqual(e.exception.code, "E_FUTURE")

    def test_the_log_stays_writable_for_everyone(self):
        with self.assertRaises(g.GateError):
            self.claim("r1", "2099-01-01T00:00:00Z")
        self.claim("own")  # before the fix: E_CLOCK for every later writer
        self.claim("r2")

    def test_small_skew_is_allowed(self):
        self.claim("r1", ts(datetime.now(timezone.utc) + timedelta(seconds=60)))

    def test_past_times_are_still_allowed(self):
        # Historical logs (tests, benches, imports) are written with past times.
        m = g.Memory(f"{self.d}/old.jsonl")
        t = "2026-01-01T00:00:00Z"
        m.register("own", "own", party="p-own", kind="human", capabilities=["owner"], compartments=["w"], record_time=t)
        m.remember("own", g.claim("old", speaker="own", frame="f", compartment="w"), record_time="2026-01-02T00:00:00Z")

    def test_a_log_already_holding_a_future_event_still_verifies(self):
        # Written before the fix: replay must not depend on today's date.
        src = Path(f"{self.d}/m.jsonl").read_text().splitlines()
        path = f"{self.d}/poisoned.jsonl"
        m = g.Memory(path)
        for line in src:
            env = json.loads(line)
            m.append(env["writer"], env["kind"], env["body"], env["record_time"])
        lines = Path(path).read_text().splitlines()
        # Hand-build one future-dated event exactly as an older gate accepted it.
        last = json.loads(lines[-1])
        env = {"seq": last["seq"] + 1, "event_id": "r1/1", "writer": "r1", "writer_seq": 1,
               "record_time": "2099-01-01T00:00:00Z", "prev": last["hash"], "kind": "claim",
               "body": g.claim("from the future", speaker="r1", frame="f", compartment="w")}
        env["hash"] = g.sha256_hex(g.canonicalize(env).encode())
        # Exact bytes: write_text would turn "\n" into "\r\n" on Windows, which is not canonical.
        Path(path).write_bytes(("\n".join(lines + [g.canonicalize(env)]) + "\n").encode())
        self.assertTrue(g.Memory(path).verify()["ok"])


class HandoffForks(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def chain(self, m):
        h0 = m.handoff("own", frame="job", summary="start", compartment="w")
        h1 = m.handoff("r1", frame="job", summary="r1 did A", compartment="w", follows=h0)
        return h0, h1

    def test_a_second_successor_is_refused(self):
        m = fresh(f"{self.d}/m.jsonl")
        h0, _ = self.chain(m)
        with self.assertRaises(g.GateError) as e:
            m.handoff("r2", frame="job", summary="r2 did B", compartment="w", follows=h0)
        self.assertEqual(e.exception.code, "E_FORK")
        self.assertEqual(m.resume("own", frame="job")["handoff"]["summary"], "r1 did A")

    def test_following_the_latest_works(self):
        m = fresh(f"{self.d}/m.jsonl")
        _, h1 = self.chain(m)
        m.handoff("r2", frame="job", summary="r2 did B", compartment="w", follows=h1)

    def test_a_new_frame_may_start_freely(self):
        m = fresh(f"{self.d}/m.jsonl")
        self.chain(m)
        m.handoff("r2", frame="job", summary="no follows", compartment="w")

    def test_version_2_logs_keep_their_old_rule(self):
        m = fresh(f"{self.d}/v2.jsonl", version=2)
        h0, _ = self.chain(m)
        m.handoff("r2", frame="job", summary="r2 did B", compartment="w", follows=h0)  # allowed in v2
        self.assertTrue(g.Memory(f"{self.d}/v2.jsonl").verify()["ok"])


if __name__ == "__main__":
    unittest.main()
