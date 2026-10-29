"""Issue #48: from protocol version 7, compartment-restricted writers learn
nothing about hidden writers, sessions, ids, leases or positions."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import gnomon as g


def ts(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def world(version=None):
    m = g.Memory(f"{tempfile.mkdtemp()}/m.jsonl")
    own = {"party": "p-own", "kind": "human", "capabilities": ["owner", "govern"], "compartments": ["a", "b"]}
    if version is None:
        m.register("own", "own", **own)
    else:
        m.append("own", "register", {"writer": "own", **own, "schema_version": version})
    m.register("own", "opa", party="p-a", kind="human", capabilities=["write"], compartments=["a"])
    m.register("own", "opb", party="p-b", kind="human", capabilities=["write"], compartments=["b"])
    # pb works in b for opa, a person it cannot see.
    m.register("own", "pb", party="p-b", kind="model", model="m", operator="opa", name="pb",
               capabilities=["write", "spawn", "schedule"], compartments=["b"])
    m.register("own", "hid-1", party="p-a", kind="tool", model="cron", operator="opa",
               capabilities=["write"], compartments=["a"], session="S")
    m.register("own", "sa", party="p-a", kind="tool", model="cron", operator="opa",
               capabilities=["schedule"], compartments=["a"])
    m.register("own", "wa", party="p-a", kind="human", capabilities=["write"], compartments=["a"])
    m.register("own", "wb", party="p-b", kind="human", capabilities=["write"], compartments=["b"])
    return m


def spawn(m, child, **kw):
    return m.register("pb", child, party="p-b", kind="model", model="m", operator="opa", name="pb",
                      capabilities=["write"], compartments=["b"], **kw)


def code(fn):
    try:
        fn()
        return "OK"
    except g.GateError as e:
        return e.code


def msg(fn):
    try:
        fn()
    except g.GateError as e:
        return str(e)


class Version7(unittest.TestCase):
    def setUp(self):
        self.m = world()

    def test_new_logs_start_at_7(self):
        self.assertEqual(self.m.state()["state"]["schema_version"], 7)

    def test_hidden_operator_is_not_shown(self):
        w = self.m.writers(["b"])
        self.assertNotIn("operator", w["pb"])
        self.assertIn("operator", self.m.writers()["pb"])
        q = self.m.query("wb", 'ops(W, O) :- identity(W, "operator", O).\n?- ops(W, O).')
        self.assertEqual(q["answers"], [])

    def test_hidden_operator_refused_alike(self):
        self.assertEqual(code(lambda: spawn(self.m, "pb-1")), "E_IDENTITY")
        self.assertEqual(msg(lambda: spawn(self.m, "pb-1")), msg(lambda: spawn(self.m, "pb-2")))

    def test_duplicate_ids_are_generic_for_non_owners(self):
        m = self.m
        # a parent whose operator is visible, so the operator rule does not bite first
        m.register("own", "pc", party="p-b", kind="model", model="m", operator="opb", name="pc",
                   capabilities=["write", "spawn"], compartments=["b"])
        m.register("own", "pc-x", party="p-a", kind="human", capabilities=["write"], compartments=["a"])
        make = lambda c: m.register("pc", c, party="p-b", kind="model", model="m", operator="opb", name="pc",
                                    capabilities=["write"], compartments=["b"])
        self.assertEqual(code(lambda: make("pc-x")), "E_REGISTER")   # hidden writer holds the id
        make("pc-y")
        self.assertEqual(code(lambda: make("pc-y")), "E_REGISTER")   # visible duplicate: same answer
        self.assertEqual(msg(lambda: make("pc-x")), msg(lambda: make("pc-y")))
        self.assertEqual(code(lambda: make("zz-1")), "E_SPAWN")      # outside the parent's namespace
        self.assertEqual(code(lambda: m.register("own", "pc-y", party="p-b", kind="human",
                                                 capabilities=["write"], compartments=["b"])), "E_DUPLICATE")

    def test_session_unique_within_visible_domain(self):
        m = self.m
        m.register("own", "pd", party="p-a", kind="model", model="m", operator="opb", name="pd",
                   capabilities=["write", "spawn"], compartments=["b"])
        make = lambda c: m.register("pd", c, party="p-a", kind="model", model="m", operator="opb",
                                    name="pd", capabilities=["write"], compartments=["b"], session="S")
        make("pd-1")          # hid-1 holds "S" in compartment a only: not a collision
        self.assertEqual(code(lambda: make("pd-2")), "E_IDENTITY")   # visible one is
        self.assertNotIn("S", msg(lambda: make("pd-2")))
        # the owner is global
        self.assertEqual(code(lambda: m.register("own", "hid-2", party="p-a", kind="tool", model="cron",
                                                 operator="opa", capabilities=["write"],
                                                 compartments=["b"], session="S")), "E_IDENTITY")

    def test_receipts_carry_no_global_position(self):
        m = self.m
        e = m.append("wb", "claim", g.claim("x", speaker="wb", frame="f", compartment="b"))
        for k in ("seq", "prev"):
            self.assertNotIn(k, e)
        self.assertEqual(e["event_id"], "wb/1")
        e = m.append("own", "claim", g.claim("y", speaker="own", frame="f", compartment="b"))
        self.assertIn("seq", e)
        self.assertNotIn("last_seq", m.writers(["b"])["wb"])

    def test_leases_do_not_reveal_hidden_state(self):
        m = self.m
        la = m.lease("sa", "wa", "job", ts(minutes=30))
        # a hidden target and an unknown one are refused alike
        self.assertEqual(code(lambda: m.lease("pb", "wa", "other", ts(minutes=30))), "E_UNKNOWN_WRITER")
        self.assertEqual(msg(lambda: m.lease("pb", "wa", "other", ts(minutes=30))).replace("wa", "X"),
                         msg(lambda: m.lease("pb", "nobody", "other", ts(minutes=30))).replace("nobody", "X"))
        # a hidden lease on the frame does not contend
        self.assertEqual(code(lambda: m.lease("pb", "wb", "job", ts(minutes=30))), "OK")
        # a hidden lease cannot be released, and looks absent
        self.assertEqual(msg(lambda: m.release("pb", la)).split(la)[0],
                         msg(lambda: m.release("pb", "sa/999")).split("sa/999")[0])

    def test_through_ignores_hidden_lease_events(self):
        m = self.m
        m.remember("wb", g.claim("x", speaker="wb", frame="f", compartment="b"))
        before = m.query("wb", 'ns(W, N) :- identity(W, "name", N).\n?- ns(W, N).')["through"]
        la = m.lease("sa", "wa", "job", ts(minutes=30))
        m.release("sa", la)
        self.assertEqual(m.query("wb", 'ns(W, N) :- identity(W, "name", N).\n?- ns(W, N).')["through"], before)


class OldLogs(unittest.TestCase):
    def test_version_6_is_unchanged(self):
        m = world(6)
        self.assertEqual(m.writers(["b"])["pb"]["operator"], "opa")
        self.assertEqual(code(lambda: m.register("pb", "pb-1", party="p-b", kind="model", model="m",
                                                 operator="opa", name="pb", capabilities=["write"],
                                                 compartments=["b"])), "OK")
        self.assertEqual(code(lambda: m.register("pb", "pb-1", party="p-b", kind="model", model="m",
                                                 operator="opa", name="pb", capabilities=["write"],
                                                 compartments=["b"])), "E_DUPLICATE")
        e = m.append("wb", "claim", g.claim("x", speaker="wb", frame="f", compartment="b"))
        self.assertIn("seq", e)
        la = m.lease("sa", "wa", "job", ts(minutes=30))
        self.assertEqual(code(lambda: m.lease("pb", "wb", "job", ts(minutes=30))), "E_LEASED")
        self.assertIsNotNone(la)


if __name__ == "__main__":
    unittest.main()
