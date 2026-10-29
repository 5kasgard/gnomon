"""Issue #49: from protocol version 5, the owner can revoke a writer's access to
compartments; reads and writes there stop, for its instances too, and what it
wrote before stays on record."""

import tempfile
import unittest

import gnomon as g
from gnomon.session import instance_for


def world(path, version=None):
    m = g.Memory(path)
    body = {"party": "p-own", "kind": "human", "capabilities": ["owner", "govern"], "compartments": ["a", "b"]}
    if version is None:
        m.register("own", "own", **body)
    else:
        m.append("own", "register", {"writer": "own", **body, "schema_version": version})
    m.register("own", "x", party="p-x", kind="model", model="m", operator="own", surface="claude-code",
               name="X", capabilities=["write", "spawn"], compartments=["a", "b"])
    m.register("own", "y", party="p-y", kind="model", model="m2", operator="own",
               capabilities=["write"], compartments=["a"])
    return m


def say(m, w, text, comp):
    return m.remember(w, g.claim(text, speaker=w, frame="f", compartment=comp))


def code(fn):
    try:
        fn()
        return "OK"
    except g.GateError as e:
        return e.code


class Revoke(unittest.TestCase):
    def setUp(self):
        self.m = world(f"{tempfile.mkdtemp()}/m.jsonl")
        self.secret = say(self.m, "own", "The answer to item 7 is 42.", "a")

    def test_writes_stop(self):
        m = self.m
        say(m, "x", "before", "a")
        m.revoke("own", "x", ["a"], "granted by mistake")
        self.assertEqual(code(lambda: say(m, "x", "after", "a")), "E_COMPARTMENT")
        self.assertEqual(code(lambda: say(m, "x", "still fine", "b")), "OK")

    def test_reads_stop(self):
        m = self.m
        self.assertTrue(any("42" in h["text"] for h in m.recall("x", "answer item 7")["hits"]))
        m.revoke("own", "x", ["a"], "granted by mistake")
        self.assertFalse(any("42" in h.get("text", "") for h in m.recall("x", "answer item 7")["hits"]))
        self.assertNotIn(self.secret, m.state(compartments=m._compartments("x"))["state"]["claims"])

    def test_instances_lose_it_too(self):
        m = self.m
        w = instance_for(m, "x", "sess-1")
        self.assertEqual(code(lambda: say(m, w, "from a session", "a")), "OK")
        m.revoke("own", "x", ["a"], "x misbehaved")
        self.assertEqual(code(lambda: say(m, w, "again", "a")), "E_COMPARTMENT")
        self.assertEqual(m.writers()[w]["compartments"], ["b"])

    def test_history_stays_and_verifies(self):
        m = self.m
        c = say(m, "x", "written before", "a")
        m.revoke("own", "x", ["a", "b"], "retired")
        self.assertIn(c, m.state()["state"]["claims"])
        self.assertEqual(m.writers()["x"]["compartments"], [])
        self.assertTrue(g.Memory(m.path).verify()["ok"])

    def test_only_the_owner_and_never_an_owner(self):
        m = self.m
        self.assertEqual(code(lambda: m.revoke("x", "y", ["a"], "a coup")), "E_CAPABILITY")
        self.assertEqual(code(lambda: m.revoke("own", "own", ["a"], "oops")), "E_REVOKE")

    def test_mistakes_are_refused(self):
        m = self.m
        self.assertEqual(code(lambda: m.revoke("own", "y", ["b"], "not in b")), "E_REVOKE")
        self.assertEqual(code(lambda: m.revoke("own", "nobody", ["a"], "?")), "E_UNKNOWN_WRITER")
        self.assertEqual(code(lambda: m.revoke("own", "y", [], "nothing")), "E_BODY")


class OlderLogs(unittest.TestCase):
    def test_needs_version_5(self):
        m = world(f"{tempfile.mkdtemp()}/v4.jsonl", version=4)
        self.assertEqual(code(lambda: m.revoke("own", "x", ["a"], "too early")), "E_SCHEMA_VERSION")
        m.append("own", "schema_change", {"version": 5, "description": "revocation (#49)"})
        self.assertEqual(code(lambda: m.revoke("own", "x", ["a"], "now")), "OK")


if __name__ == "__main__":
    unittest.main()
