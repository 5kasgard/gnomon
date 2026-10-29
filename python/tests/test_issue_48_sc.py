"""Issue #48 (SC): cross-compartment channels, protocol version 7."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import gnomon as g


def ts(**d):
    return (datetime.now(timezone.utc) + timedelta(**d)).strftime("%Y-%m-%dT%H:%M:%SZ")


def code(fn):
    try:
        fn()
        return "OK"
    except g.GateError as e:
        return e.code


def world(version=None):
    m = g.Memory(f"{tempfile.mkdtemp()}/m.jsonl")
    body = {"party": "po", "kind": "human", "capabilities": ["owner", "govern"], "compartments": ["a", "b"]}
    if version is None:
        m.register("own", "own", **body)
    else:
        m.append("own", "register", {"writer": "own", **body, "schema_version": version})
    return m


def reg(m, w, comps, caps=("write",), **kw):
    kind = kw.pop("kind", "tool")
    if kind != "human":
        kw.setdefault("operator", "own")
        kw.setdefault("model", "t1")
    return m.register("own", w, party=kw.pop("party", "p-" + w), kind=kind, capabilities=list(caps), compartments=comps, **kw)


class Operator(unittest.TestCase):
    def test_hidden_operator_is_omitted(self):
        m = world()
        reg(m, "boss", ["b"], kind="human")
        reg(m, "bot", ["a", "b"], kind="model", model="m", operator="boss")
        self.assertNotIn("operator", m.writers(["a"])["bot"])
        self.assertEqual(m.writers(["b"])["bot"]["operator"], "boss")
        self.assertEqual(m.writers()["bot"]["operator"], "boss")

    def test_datalog_hides_operator_from_a(self):
        m = world()
        reg(m, "boss", ["b"], kind="human")
        reg(m, "bot", ["a", "b"], kind="model", model="m", operator="boss")
        reg(m, "ra", ["a"])
        reg(m, "rb", ["b"])
        prog = '?- identity("bot", "operator", O).'
        self.assertNotIn('"boss"', str(m.query("ra", prog)))
        self.assertIn("boss", str(m.query("rb", prog)))


class Namespaces(unittest.TestCase):
    def test_session_ids_only_clash_within_a_compartment(self):
        m = world()
        reg(m, "x1", ["a"], session="s1", surface="cli", party="px")
        self.assertEqual(code(lambda: reg(m, "x2", ["b"], session="s1", surface="cli", party="px")), "OK")
        self.assertEqual(code(lambda: reg(m, "x3", ["a"], session="s1", surface="cli", party="px")), "E_IDENTITY")
        self.assertEqual(code(lambda: reg(m, "x4", ["a", "b"], caps=("owner",), session="s1", surface="cli", party="px")),
                         "E_IDENTITY")

    def test_old_logs_keep_the_global_session_rule(self):
        m = world(6)
        reg(m, "x1", ["a"], session="s1", surface="cli", party="px")
        self.assertEqual(code(lambda: reg(m, "x2", ["b"], session="s1", surface="cli", party="px")), "E_IDENTITY")

    def test_spawner_namespace_is_exclusive(self):
        m = world()
        reg(m, "sp", ["a"], caps=("write", "spawn"))
        self.assertEqual(code(lambda: reg(m, "sp-1", ["b"])), "E_DUPLICATE")
        self.assertEqual(code(lambda: m.register("sp", "sp-1", party="p-sp", kind="tool", capabilities=["write"],
                                                 compartments=["a"], operator="own", model="t1")), "OK")
        reg(m, "zz-1", ["b"])
        self.assertEqual(code(lambda: reg(m, "zz", ["b"], caps=("write", "spawn"))), "E_DUPLICATE")

    def test_old_logs_may_overlap(self):
        m = world(6)
        reg(m, "sp", ["a"], caps=("write", "spawn"))
        self.assertEqual(code(lambda: reg(m, "sp-1", ["b"])), "OK")


class Returns(unittest.TestCase):
    def test_non_owner_return_has_no_seq(self):
        m = world()
        reg(m, "w1", ["a"])
        e = m.episode("w1", "hello", compartment="a")
        self.assertIn("event_id", e)
        self.assertNotIn("seq", e)
        self.assertIn("seq", m.episode("own", "hi", compartment="a"))

    def test_restricted_views_have_no_seq(self):
        m = world()
        reg(m, "w1", ["a"])
        m.remember("w1", g.claim("x", speaker="w1", frame="f", compartment="a"))
        self.assertNotIn('"seq"', str(m.state(compartments=["a"])).replace("'", '"'))
        r = m.query("w1", "?- claim(C).")
        self.assertNotIn("seq", str(r).replace("last_seq", ""))


class Leases(unittest.TestCase):
    def setUp(self):
        m = self.m = world()
        reg(m, "sched", ["a"], caps=("schedule", "write"))
        reg(m, "sched2", ["b"], caps=("schedule", "write"))
        reg(m, "bridge", ["a", "b"], caps=("write",))
        reg(m, "ob", ["b"], caps=("write",))
        reg(m, "oa", ["a"], caps=("write",))

    def say(self, w, comp, lease=None):
        body = g.claim("x", speaker=w, frame="job", compartment=comp)
        if lease:
            body["lease"] = lease
        return self.m.remember(w, body)

    def test_scope_is_the_granters_share(self):
        m = self.m
        lease = m.lease("sched", "bridge", "job", ts(minutes=30))
        # a write in b is outside the scope: unrestricted
        self.assertEqual(code(lambda: self.say("ob", "b")), "OK")
        self.assertEqual(code(lambda: self.say("oa", "a")), "E_LEASE")
        self.assertEqual(code(lambda: self.say("bridge", "a", lease)), "OK")
        # the b scheduler can lease the same frame in b: no conflict
        self.assertEqual(code(lambda: m.lease("sched2", "bridge", "job", ts(minutes=30))), "OK")
        self.assertEqual(code(lambda: m.lease("sched", "bridge", "job", ts(minutes=30))), "E_LEASED")
        # b cannot see or release a's lease
        e = code(lambda: m.release("sched2", lease))
        self.assertEqual(e, code(lambda: m.release("sched2", "nobody/1")))
        self.assertNotEqual(e, "OK")

    def test_hidden_target_looks_unregistered(self):
        m = self.m
        with self.assertRaises(g.GateError) as hidden:
            m.lease("sched", "ob", "job", ts(minutes=30))
        with self.assertRaises(g.GateError) as missing:
            m.lease("sched", "nobody", "job", ts(minutes=30))
        self.assertEqual(hidden.exception.code, "E_UNKNOWN_WRITER")
        self.assertEqual(hidden.exception.code, missing.exception.code)
        self.assertEqual(str(hidden.exception).replace("ob", "X"), str(missing.exception).replace("nobody", "X"))

    def test_old_leases_have_no_scope(self):
        m = world(6)
        reg(m, "sched", ["a"], caps=("schedule", "write"))
        reg(m, "ob", ["b"], caps=("write",))
        reg(m, "oa", ["a"], caps=("write",))
        m.lease("own", "oa", "job", ts(minutes=30))
        self.assertEqual(code(lambda: m.lease("sched", "oa", "job", ts(minutes=30))), "E_LEASED")
        self.assertEqual(code(lambda: m.lease("own", "ob", "job", ts(minutes=30))), "E_LEASED")


class Through(unittest.TestCase):
    def test_lease_and_revoke_events_follow_their_compartments(self):
        m = world()
        reg(m, "sched", ["a"], caps=("schedule", "write"))
        reg(m, "ra", ["a"])
        reg(m, "rb", ["b"])
        reg(m, "rab", ["a", "b"])
        m.lease("sched", "ra", "job", ts(minutes=30))
        a = m.query("ra", "?- claim(C).")["through"]["kind"]
        b = m.query("rb", "?- claim(C).")["through"]
        self.assertEqual(a, "lease")
        self.assertNotEqual(b and b.get("kind"), "lease")
        m.revoke("own", "rab", ["a"], "test")
        self.assertEqual(m.query("rb", "?- claim(C).")["through"]["kind"], "register")


class Clock(unittest.TestCase):
    def test_non_owner_cannot_date_ahead(self):
        m = world()
        reg(m, "w1", ["a"])
        far = ts(minutes=4)
        self.assertEqual(code(lambda: m.episode("w1", "ok", compartment="a", record_time=far)), "OK")
        m2 = g.Memory(m.path, skew=1)
        self.assertEqual(code(lambda: m2.episode("w1", "no", compartment="a", record_time=ts(minutes=2))),
                         "E_CLOCK_AHEAD")
        self.assertEqual(code(lambda: m2.episode("own", "owner", compartment="a", record_time=ts(minutes=4, seconds=30))), "OK")

    def test_default_time_is_at_least_the_head(self):
        m = world()
        reg(m, "w1", ["a"])
        m.episode("own", "ahead", compartment="a", record_time=ts(minutes=3))
        e = m.episode("w1", "after", compartment="a")
        self.assertGreaterEqual(e["record_time"], ts(minutes=3)[:-4])


if __name__ == "__main__":
    unittest.main()
