"""M7: the record as a forum. Standard library only.

Questions, answers, critiques, replies and citations are claims like any
other, linked by reference; views say what is open, settled or unanswered,
with proofs the independent checker accepts.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import gnomon  # noqa: E402
from gnomon import VIEWS, GateError, Memory, proofs  # noqa: E402
from test_mcp import Proc  # noqa: E402


def t(minute: int) -> str:
    return f"2026-10-16T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "memory.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
                   compartments=["lab"], record_time=t(0))
        m.register("john", "claude", party="anthropic", kind="model", model="claude-opus-5-5", operator="john",
                   name="Claude", capabilities=["write", "verify", "spawn"], compartments=["lab"], record_time=t(1))
        m.register("john", "gpt", party="openai", kind="model", model="gpt-6", operator="john", name="GPT",
                   capabilities=["write", "verify"], compartments=["lab"], record_time=t(1))
        self.q = m.ask("john", "Which database should the prototype use?", frame="db", compartment="lab",
                       record_time=t(2))
        self.a = m.respond("claude", self.q, "answers", "SQLite: one file, no server.", frame="db",
                           compartment="lab", truth="true", confidence=7000, record_time=t(3))
        self.k = m.respond("gpt", self.a, "critiques", "SQLite serialises writers.", frame="db",
                           compartment="lab", truth="true", confidence=8000, record_time=t(4))

    def tearDown(self):
        self.dir.cleanup()

    def ask(self, program: str) -> set:
        r = self.m.query("john", program)
        self.assertEqual(proofs.check(r, program, VIEWS, self.log), [], program)
        return {tuple(a["values"]) for a in r["answers"]}



class Forum(Fixture):
    def test_questions_stay_open_until_an_answer_is_verified(self):
        self.assertEqual(self.ask("?- open_question(Q)."), {(self.q,)})
        self.assertEqual(self.ask("?- answer(A, Q)."), {(self.a, self.q)})
        self.m.verdict("gpt", self.a, "supports", "external_source", record_time=t(5))
        self.assertEqual(self.ask("?- open_question(Q)."), set())
        self.assertEqual(self.ask("?- settled(Q)."), {(self.q,)})

    def test_critiques_wait_for_a_reply_from_the_author(self):
        self.assertEqual(self.ask("?- unanswered_critique(K, C)."), {(self.k, self.a)})
        # A reply from someone else does not count.
        self.m.respond("gpt", self.k, "replies", "To be fair, reads are concurrent.", frame="db",
                       compartment="lab", record_time=t(5))
        self.assertEqual(self.ask("?- unanswered_critique(K, C)."), {(self.k, self.a)})
        self.m.respond("claude", self.k, "replies", "WAL mode: one writer, many readers.", frame="db",
                       compartment="lab", truth="true", confidence=8000, record_time=t(6))
        self.assertEqual(self.ask("?- unanswered_critique(K, C)."), set())

    def test_threads_and_discussion(self):
        r = self.m.respond("claude", self.k, "replies", "Fair point.", frame="db", compartment="lab",
                           record_time=t(5))
        d = self.m.discussion("john", self.q)
        self.assertEqual(d["root"], self.q)
        self.assertFalse(d["truncated"])
        self.assertEqual([p["claim"] for p in d["posts"]], [self.q, self.a, self.k, r])
        self.assertEqual(d["posts"][1]["refers_to"], [{"claim": self.q, "relation": "answers"}])
        self.assertEqual(d["posts"][3]["refers_to"], [{"claim": self.k, "relation": "replies"}])
        # A critique changes no truth.
        self.assertEqual(d["posts"][1]["truth"], "true")

    def test_awaiting_verification(self):
        c = self.m.respond("gpt", self.a, "cites", "Benchmarks needed before choosing.", frame="db",
                           compartment="lab", record_time=t(5))
        self.assertIn((c,), self.ask("?- awaiting_verification(C)."))
        self.assertNotIn((self.q,), self.ask("?- awaiting_verification(C)."))

    def test_forged_references_are_caught(self):
        q = "?- critique(K, C)."
        r = self.m.query("john", q)
        for node in r["nodes"].values():
            if node["fact"][0] == "reference":
                node["fact"][3] = "answers"
        self.assertTrue(proofs.check(r, q, VIEWS, self.log))

    def test_over_mcp(self):
        s = Proc("--log", self.log, "--writer", "claude", "--per-session")
        try:
            r = s.call("open_questions", {})
            self.assertEqual([x["claim"] for x in r["structuredContent"]["questions"]], [self.q])
            self.assertEqual(r["structuredContent"]["questions"][0]["answers"], 1)
            self.assertTrue(r["structuredContent"]["proofs_checked"])
            r = s.call("remember", {"statement": "Use Postgres once there are two writers.", "frame": "db",
                                    "compartment": "lab", "truth": "unknown", "stance": "suggested",
                                    "about": [{"claim": self.k, "relation": "replies"}]})
            self.assertFalse(r["isError"], r)
            r = s.call("discussion", {"claim": self.q})
            self.assertIn("Use Postgres once there are two writers.", r["content"][0]["text"])
            r = s.call("remember", {"statement": "x", "frame": "db", "compartment": "lab", "truth": "unknown",
                                    "about": [{"claim": self.a, "relation": "answers"}]})
            self.assertTrue(r["isError"])
            self.assertIn("E_REFERENCE", r["content"][0]["text"])
        finally:
            s.close()

    def test_no_peeking_at_hidden_claims(self):
        with self.assertRaises(GateError):
            self.m.discussion("gpt", "c:" + "0" * 64)


if __name__ == "__main__":
    unittest.main()


class ContentAddressedProofs(Fixture):
    """Rules are named by the hash of their text, proof nodes by the hash of
    what they say and rest on: an answer's proof_hash fixes its whole
    derivation, down to the log events."""

    def test_hashes_are_stable_and_checked(self):
        import copy
        import hashlib
        q = "?- open_question(Q), statement(Q, S)."
        r1, r2 = self.m.query("john", q), self.m.query("john", q)
        self.assertEqual(r1["answers"][0]["proof_hash"], r2["answers"][0]["proof_hash"])
        for rule in r1["rules"].values():
            self.assertEqual(rule["hash"], hashlib.sha256(rule["text"].encode()).hexdigest())
        self.assertEqual(proofs.check(r1, q, VIEWS, self.log), [])
        bad = copy.deepcopy(r1)
        leaf = next(k for k, n in bad["nodes"].items() if "sources" in n)
        bad["nodes"][leaf]["hash"] = "0" * 64
        self.assertTrue(any("hash" in p for p in proofs.check(bad, q, VIEWS, self.log)))
        bad = copy.deepcopy(r1)
        for n in bad["nodes"].values():
            n.pop("hash")
        self.assertTrue(any("content-addressed" in p for p in proofs.check(bad, q, VIEWS, self.log)))
        # A new answer to the question changes the derivation, so the hash.
        self.m.verdict("gpt", self.a, "supports", "external_source", record_time=t(9))
        r3 = self.m.query("john", "?- question(Q), statement(Q, S).")
        r4 = self.m.query("john", "?- question(Q), statement(Q, S).")
        self.assertEqual(r3["answers"][0]["proof_hash"], r4["answers"][0]["proof_hash"])


class ReviewM7(Fixture):
    """Each confirmed finding of the M7 review, as a test."""

    def test_a_long_thread_does_not_break_other_views(self):
        # Finding 1: views the query does not use are not evaluated.
        prev = self.k
        for i in range(1200):
            prev = self.m.respond("gpt" if i % 2 else "claude", prev, "replies", f"Point {i}.", frame="db",
                                  compartment="lab", record_time=t(10))
        self.assertEqual(self.ask("?- open_question(Q)."), {(self.q,)})
        self.assertEqual(self.ask("?- answer(A, Q)."), {(self.a, self.q)})

    def test_discussion_is_bounded_on_shared_threads(self):
        # Finding 2: a DAG is walked once, never expanded into a tree.
        ids = [self.a, self.k]
        for i in range(60):
            ids.append(self.m.respond("claude", ids[-1], "replies", f"r{i}", frame="db", compartment="lab",
                                      record_time=t(10)) if i % 2 else
                       self.m.remember("gpt", gnomon.claim(f"c{i}", speaker="gpt", frame="db", compartment="lab",
                                                             about=[{"claim": ids[-1], "relation": "cites"},
                                                                    {"claim": ids[-2], "relation": "cites"}]),
                                       record_time=t(10)))
        d = self.m.discussion("john", self.q)
        self.assertEqual(len(d["posts"]), 63)
        self.assertEqual(len({p["claim"] for p in d["posts"]}), 63)
        small = self.m.discussion("john", self.q, limit=10)
        self.assertTrue(small["truncated"])
        self.assertEqual(len(small["posts"]), 10)

    def test_each_author_answers_for_their_own_claim(self):
        # Finding 3.
        b = self.m.respond("gpt", self.q, "answers", "Postgres.", frame="db", compartment="lab",
                           record_time=t(5))
        both = self.m.remember("john", gnomon.claim("Both ignore backups.", speaker="john", frame="db",
                                                    compartment="lab", about=[{"claim": self.a, "relation": "critiques"},
                                                                              {"claim": b, "relation": "critiques"}]),
                               record_time=t(6))
        self.m.respond("claude", both, "replies", "Litestream covers it.", frame="db", compartment="lab",
                       record_time=t(7))
        self.assertIn((both, b), self.ask("?- unanswered_critique(K, C)."))
        self.assertNotIn((both, self.a), self.ask("?- unanswered_critique(K, C)."))

    def test_a_refuted_question_is_not_open(self):
        # Finding 4.
        bad = self.m.ask("gpt", "Why is Rust slower than Python?", frame="db", compartment="lab", record_time=t(5))
        self.assertIn((bad,), self.ask("?- open_question(Q)."))
        self.m.verdict("claude", bad, "refutes", "external_source", record_time=t(6))
        fixed = self.m.ask("claude", "Is Rust slower than Python?", frame="db", compartment="lab", record_time=t(7))
        self.m.supersede("claude", bad, fixed, "refuted", [f"claude/{self._seq('claude') - 1}"],
                         "false premise", record_time=t(8))
        self.assertNotIn((bad,), self.ask("?- open_question(Q)."))

    def _seq(self, writer):
        return self.m.state()["state"]["writers"][writer]["last_seq"]

    def test_one_answer_answers_one_question(self):
        # Finding 10.
        q2 = self.m.ask("john", "And for tests?", frame="db", compartment="lab", record_time=t(5))
        with self.assertRaises(GateError):
            self.m.remember("claude", gnomon.claim("SQLite.", speaker="claude", frame="db", compartment="lab",
                                                  about=[{"claim": self.q, "relation": "answers"},
                                                         {"claim": q2, "relation": "answers"}]))

    def test_partly_hashed_proofs_fail(self):
        # Finding 7.
        import copy
        q = "?- answer(A, Q)."
        r = self.m.query("john", q)
        bad = copy.deepcopy(r)
        for a in bad["answers"]:
            a.pop("proof_hash")
            a["hash"] = "junk"
        self.assertTrue(proofs.check(bad, q, VIEWS, self.log))
        bare = copy.deepcopy(r)
        for a in bare["answers"]:
            a.pop("proof_hash")
        for x in list(bare["nodes"].values()) + list(bare["rules"].values()):
            x.pop("hash")
        self.assertEqual(proofs.check(bare, q, VIEWS, self.log), [])
        self.assertTrue(proofs.check(bare, q, VIEWS, self.log, require_hashes=True))

    def test_discussion_order_and_edges(self):
        # Findings 8 and 9.
        rs = [self.m.respond("claude", self.k, "replies", f"r{i}", frame="db", compartment="lab",
                             record_time=t(5)) for i in range(12)]
        posts = [p["claim"] for p in self.m.discussion("john", self.k)["posts"]]
        self.assertEqual(posts, [self.k] + rs)
        two = self.m.remember("gpt", gnomon.claim("See both.", speaker="gpt", frame="db", compartment="lab",
                                                 about=[{"claim": self.a, "relation": "replies"},
                                                        {"claim": self.a, "relation": "cites"}]),
                              record_time=t(6))
        posts = self.m.discussion("john", self.a)["posts"]
        self.assertEqual([p["claim"] for p in posts].count(two), 1)
        with self.assertRaises(GateError):
            self.m.discussion("nobody", self.q)
        self.assertIn("abstains", posts[0])

    def test_open_questions_counts_exactly(self):
        # Finding 5.
        for i in range(40):
            self.m.respond("gpt" if i % 2 else "claude", self.q, "answers", f"Option {i}.", frame="db",
                           compartment="lab", record_time=t(5))
        s = Proc("--log", self.log, "--writer", "claude", "--per-session")
        try:
            r = s.call("open_questions", {})["structuredContent"]
            self.assertEqual(r["questions"][0]["answers"], 41)
            self.assertFalse(r["truncated"])
        finally:
            s.close()

