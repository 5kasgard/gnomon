"""Issue #48: from protocol version 7, compartments isolate operators, session
ids, names, leases, lease/release/revoke visibility, clocks and as_of."""

import tempfile
import unittest

import gnomon as g

T0 = "2026-01-01T00:00:%02dZ"


def t(n):
    return T0 % n


def world(version=None):
    m = g.Memory(f"{tempfile.mkdtemp()}/m.jsonl")
    body = {"party": "p-own", "kind": "human", "capabilities": ["owner", "govern"], "compartments": ["a", "b"]}
    if version is None:
        m.register("own", "own", record_time=t(1), **body)
    else:
        m.append("own", "register", {"writer": "own", **body, "schema_version": version}, t(1))
    return m


def reg(m, w, comps, caps=("write",), n=2, **kw):
    kw.setdefault("party", "p-" + w)
    kw.setdefault("kind", "model")
    if kw["kind"] == "model":
        kw.setdefault("model", "mod")
        kw.setdefault("operator", "own")
    return m.register("own", w, capabilities=list(caps), compartments=comps, record_time=t(n), **kw)


def code(fn):
    try:
        fn()
        return "OK"
    except g.GateError as e:
        return e.code


class Version(unittest.TestCase):
    def test_new_logs_start_at_7(self):
        self.assertEqual(g.SCHEMA_VERSION, 7)
        self.assertEqual(world().state()["state"]["schema_version"], 7)


class Operator(unittest.TestCase):
    def setUp(self):
        self.m = m = world()
        reg(m, "ha", ["a"], kind="human", model=None)
        reg(m, "bot", ["a", "b"], operator="ha", surface="s")
        reg(m, "wb", ["b"])
        reg(m, "wa", ["a"])

    def test_view_and_facts_hide_an_operator_the_reader_cannot_see(self):
        self.assertNotIn("operator", self.m.writers(["b"])["bot"])
        self.assertEqual(self.m.writers(["a"])["bot"]["operator"], "ha")
        self.assertEqual(self.m.writers()["bot"]["operator"], "ha")
        q = '?- identity("bot", "operator", O).'
        self.assertEqual(self.m.query("wb", q)["answers"], [])
        self.assertEqual(len(self.m.query("wa", q)["answers"]), 1)

    def test_old_log_still_shows_it(self):
        m = world(6)
        reg(m, "ha", ["a"], kind="human", model=None)
        reg(m, "bot", ["a", "b"], operator="ha", surface="s")
        self.assertEqual(m.writers(["b"])["bot"]["operator"], "ha")

    def test_non_owner_cannot_name_an_operator_in_a_disjoint_compartment(self):
        for version, expect in ((None, "E_IDENTITY"), (6, "OK")):
            m = world(version)
            reg(m, "ha", ["a"], kind="human", model=None)
            reg(m, "wb", ["b"], caps=("write", "spawn"), operator="ha", surface="s")
            self.assertEqual(code(lambda: m.register(
                "wb", "wb-1", party="p-wb", kind="model", model="mod", operator="ha", surface="s",
                capabilities=["write"], compartments=["b"], record_time=t(3))), expect)

    def test_a_shared_operator_is_fine(self):
        m = world()
        reg(m, "wb", ["b"], caps=("write", "spawn"), surface="s")
        self.assertEqual(code(lambda: m.register("wb", "wb-1", party="p-wb", kind="model", model="mod",
                                                 operator="own", surface="s", capabilities=["write"], compartments=["b"],
                                                 record_time=t(3))), "OK")


class Scoped(unittest.TestCase):
    def test_sessions_and_names_are_scoped_from_v7(self):
        for version, disjoint in ((None, "OK"), (6, "E_IDENTITY")):
            m = world(version)
            reg(m, "s1", ["a"], party="p1", surface="x", session="k", name="Ann")
            self.assertEqual(code(lambda: reg(m, "s2", ["b"], n=3, party="p1", surface="x", session="k")), disjoint)
            self.assertEqual(code(lambda: reg(m, "n2", ["b"], n=4, name="Ann")), disjoint)
            self.assertEqual(code(lambda: reg(m, "s3", ["a"], n=5, party="p1", surface="x", session="k")), "E_IDENTITY")
            self.assertEqual(code(lambda: reg(m, "n3", ["a", "b"], n=6, name="Ann")), "E_IDENTITY")

    def test_writer_ids_stay_global(self):
        m = world()
        reg(m, "w1", ["a"])
        self.assertEqual(code(lambda: reg(m, "w1", ["b"], n=3)), "E_DUPLICATE")


class Returns(unittest.TestCase):
    def test_write_return_hides_the_global_position(self):
        self.assertNotIn("seq", world().episode("own", "hi", compartment="a", record_time=t(2)))
        self.assertIn("seq", world(6).episode("own", "hi", compartment="a", record_time=t(2)))


class Leases(unittest.TestCase):
    def setUp(self):
        self.m = m = world()
        reg(m, "sa", ["a"], caps=("schedule",))
        reg(m, "sb", ["b"], caps=("schedule",), n=3)
        reg(m, "ra", ["a"], n=4)
        reg(m, "rb", ["b"], n=5)

    def test_target_in_another_compartment_looks_unregistered(self):
        m = self.m
        self.assertEqual(code(lambda: m.lease("sa", "rb", "f", "2099-01-01T00:00:00Z", record_time=t(6))),
                         "E_UNKNOWN_WRITER")
        self.assertEqual(code(lambda: m.lease("sa", "nobody", "f", "2099-01-01T00:00:00Z", record_time=t(6))),
                         "E_UNKNOWN_WRITER")

    def test_frame_names_are_scoped(self):
        m = self.m
        la = m.lease("sa", "ra", "f", "2099-01-01T00:00:00Z", record_time=t(6))
        self.assertEqual(code(lambda: m.lease("sb", "rb", "f", "2099-01-01T00:00:00Z", record_time=t(7))), "OK")
        reg(m, "ra2", ["a"], n=8)
        self.assertEqual(code(lambda: m.lease("sa", "ra2", "f", "2099-01-01T00:00:00Z", record_time=t(9))),
                         "E_LEASED")
        self.assertTrue(la)

    def test_through_excludes_leases_of_other_compartments(self):
        m = self.m
        m.episode("rb", "mine", compartment="b", record_time=t(6))
        lease = m.lease("sa", "ra", "f", "2099-01-01T00:00:00Z", record_time=t(7))
        m.release("sa", lease, record_time=t(8))
        end = lambda r: m.recall(r, "mine")["through"]["event"]
        self.assertEqual(end("rb"), "rb/1")
        self.assertEqual(end("ra"), "sa/2")

    def test_old_log_keeps_the_fall_through(self):
        m = world(6)
        reg(m, "sa", ["a"], caps=("schedule",))
        reg(m, "ra", ["a"], n=3)
        reg(m, "rb", ["b"], n=4)
        m.episode("rb", "mine", compartment="b", record_time=t(5))
        m.lease("sa", "ra", "f", "2099-01-01T00:00:00Z", record_time=t(6))
        self.assertEqual(m.recall("rb", "mine")["through"]["event"], "sa/1")


class Clock(unittest.TestCase):
    def test_each_compartment_has_its_own_clock(self):
        m = world()
        reg(m, "wa", ["a"])
        reg(m, "wb", ["b"], n=3)
        m.episode("wa", "late in A", compartment="a", record_time=t(30))
        self.assertEqual(code(lambda: m.episode("wb", "early in B", compartment="b", record_time=t(10))), "OK")
        self.assertEqual(code(lambda: m.episode("wa", "back in A", compartment="a", record_time=t(20))), "E_CLOCK")

    def test_old_log_keeps_the_global_clock(self):
        m = world(6)
        reg(m, "wa", ["a"])
        reg(m, "wb", ["b"], n=3)
        m.episode("wa", "late in A", compartment="a", record_time=t(30))
        self.assertEqual(code(lambda: m.episode("wb", "early in B", compartment="b", record_time=t(10))), "E_CLOCK")

    def test_as_of_does_not_stop_at_the_first_later_event(self):
        m = world()
        reg(m, "wa", ["a"])
        reg(m, "wb", ["b"], n=3)
        m.episode("wa", "late in A", compartment="a", record_time=t(30))
        m.episode("wb", "early in B", compartment="b", record_time=t(10))
        eps = m.state(as_of=t(20))["state"]["episodes"]
        self.assertEqual(sorted(e["body"]["content"] for e in eps.values()), ["early in B"])
        self.assertEqual(len(m.state(as_of=t(40))["state"]["episodes"]), 2)


if __name__ == "__main__":
    unittest.main()
