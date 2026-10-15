"""Cross-language and end-to-end tests. Standard library only.

Run from the repository root:  python3 -m unittest discover -s python/tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import gnomon  # noqa: E402
from gnomon import GateError, Memory, claim  # noqa: E402
import jcs_reference  # noqa: E402

VECTORS = HERE.parent.parent / "tests" / "vectors" / "canonical.json"
RNG = random.Random(20261014)  # fixed seed: reproducible


def t(minute: int) -> str:
    return f"2026-10-14T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


def random_string(max_len: int = 8) -> str:
    pools = [
        "abcxyz019",
        "éö€דּ",
        "\U0001F600\U0001F680\U00010348",  # astral: surrogate pairs in UTF-16
        "\n\t\"\\/\u0001\u001f",
        "日本語",
    ]
    return "".join(RNG.choice(RNG.choice(pools)) for _ in range(RNG.randint(0, max_len)))


def random_value(depth: int = 0):
    kinds = ["null", "bool", "int", "str"] + (["list", "dict"] if depth < 4 else [])
    k = RNG.choice(kinds)
    if k == "null":
        return None
    if k == "bool":
        return RNG.random() < 0.5
    if k == "int":
        return RNG.choice([0, -1, 1, 2**53 - 1, -(2**53 - 1), RNG.randint(-10**12, 10**12)])
    if k == "str":
        return random_string()
    if k == "list":
        return [random_value(depth + 1) for _ in range(RNG.randint(0, 4))]
    return {random_string(5): random_value(depth + 1) for _ in range(RNG.randint(0, 5))}


class HashingOracle(unittest.TestCase):
    """The Rust SHA-256 must agree with Python's hashlib on everything."""

    def test_against_hashlib(self):
        for n in list(range(0, 200)) + [RNG.randint(200, 20000) for _ in range(100)]:
            data = bytes(RNG.getrandbits(8) for _ in range(n))
            self.assertEqual(gnomon.sha256_hex(data), hashlib.sha256(data).hexdigest(), n)


class CanonicalOracle(unittest.TestCase):
    """The Rust canonicaliser must agree with an independent implementation."""

    def test_random_values(self):
        for _ in range(2000):
            v = random_value()
            self.assertEqual(gnomon.canonicalize(v), jcs_reference.canonical(v))

    def test_shared_vectors(self):
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertGreater(len(vectors), 10)
        for vec in vectors:
            self.assertEqual(gnomon.canonicalize_text(vec["input"]), vec["canonical"], vec["input"])
            self.assertEqual(jcs_reference.canonical(json.loads(vec["input"])), vec["canonical"])
            self.assertEqual(gnomon.sha256_hex(vec["canonical"].encode("utf-8")), vec["sha256"])

    def test_rejections(self):
        for bad in ["1.5", "1e3", "NaN", "-0", "9007199254740992", '{"a":1,"a":2}', '"\\ud800"']:
            with self.assertRaises(GateError, msg=bad):
                gnomon.canonicalize_text(bad)
        with self.assertRaises(ValueError):
            gnomon.canonicalize({"x": float("nan")})  # stopped before the core

    def test_claim_id_is_hash_of_canonical_body(self):
        body = claim("x", speaker="alex", frame="f", compartment="lab")
        expected = "c:" + hashlib.sha256(jcs_reference.canonical(body).encode("utf-8")).hexdigest()
        self.assertEqual(gnomon.claim_id(body), expected)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "memory.jsonl"
        self.m = Memory(self.path)
        m = self.m
        m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                   compartments=["lab", "private"], record_time=t(0))
        m.register("alex", "claude-c1", party="anthropic", kind="model",
                   capabilities=["write", "verify"], compartments=["lab"], record_time=t(1))
        m.register("alex", "gpt-g1", party="openai", kind="model",
                   capabilities=["write", "verify"], compartments=["lab"], record_time=t(2))

    def tearDown(self):
        self.dir.cleanup()

    def test_memory_round_trip_and_rebuild(self):
        m = self.m
        ep = m.episode("claude-c1", "Alex: let's build it in Rust and Python.",
                       compartment="lab", participants=["alex", "claude-c1"], record_time=t(3))
        decided = m.remember("claude-c1", claim(
            "Core is written in Rust with Python on top", speaker="alex", frame="substrate stack",
            compartment="lab", stance="decided", truth="true", confidence=9500,
            origin="manual", source_episode=ep["event_id"]), record_time=t(4))
        suggested = m.remember("claude-c1", claim(
            "Use Datalog for queries", speaker="claude-c1", frame="substrate stack",
            compartment="lab", stance="suggested"), record_time=t(5))
        self.assertNotEqual(decided, suggested)

        s1 = m.state()
        s2 = Memory(self.path).state()
        self.assertEqual(s1["digest"], s2["digest"])
        claims = s1["state"]["claims"]
        self.assertEqual(claims[decided]["body"]["stance"], "decided")
        self.assertEqual(claims[suggested]["body"]["stance"], "suggested")
        self.assertEqual(claims[decided]["body"]["speaker"], "alex")

    def test_as_of(self):
        m = self.m
        m.remember("claude-c1", claim("early", speaker="a", frame="f", compartment="lab"), record_time=t(10))
        m.remember("gpt-g1", claim("late", speaker="a", frame="f", compartment="lab"), record_time=t(20))
        self.assertEqual(len(m.state(as_of=t(15))["state"]["claims"]), 1)
        self.assertEqual(len(m.state()["state"]["claims"]), 2)

    def test_gate_errors_surface_with_codes(self):
        m = self.m
        cid = m.remember("claude-c1", claim("mine", speaker="c", frame="f", compartment="lab"), record_time=t(10))
        with self.assertRaises(GateError) as e:
            m.verdict("claude-c1", cid, "supports", "cross_model_review", record_time=t(11))
        self.assertEqual(e.exception.code, "E_SELF_VERIFY")
        with self.assertRaises(GateError) as e:
            m.remember("gpt-g1", claim("secret", speaker="c", frame="f", compartment="private"), record_time=t(12))
        self.assertEqual(e.exception.code, "E_COMPARTMENT")
        m.verdict("gpt-g1", cid, "supports", "cross_model_review", record_time=t(13))
        self.assertEqual(len(m.state()["state"]["claims"][cid]["supports"]), 1)

    def test_dispute_flag_and_appeal(self):
        m = self.m
        m.register("alex", "kimi-k1", party="moonshot", kind="model",
                   capabilities=["write", "verify"], compartments=["lab"], record_time=t(3))
        m.register("alex", "judge", party="judge-co", kind="model",
                   capabilities=["verify"], compartments=["lab"], record_time=t(3))
        m.register("alex", "mistral-m1", party="mistral", kind="model",
                   capabilities=["write", "verify"], compartments=["lab"], record_time=t(3))
        T = m.remember("claude-c1", claim("T", speaker="c", frame="f", compartment="lab",
                                          truth="true"), record_time=t(10))
        B = m.remember("gpt-g1", claim("B", speaker="g", frame="f", compartment="lab",
                                       truth="true"), record_time=t(11))
        m.supersede("gpt-g1", T, B, "contested", [], "I disagree", record_time=t(12))
        c = m.state()["state"]["claims"][T]
        self.assertEqual((c["effective_truth"], c["disputed"]), ("true", True))
        sup = m.state()["state"]["supersessions"]
        contest = next(k for k, v in sup.items() if v["kind"] == "contested")
        m.resolve("gpt-g1", contest, "withdrawn", "on reflection", record_time=t(13))
        # Refute T, then appeal it on new outside evidence.
        v = m.verdict("kimi-k1", T, "refutes", "test", record_time=t(14))
        m.supersede("gpt-g1", T, B, "refuted", [v["event_id"]], "test failed", record_time=t(15))
        self.assertEqual(m.state()["state"]["claims"][T]["effective_truth"], "false")
        s = m.verdict("mistral-m1", T, "supports", "test", record_time=t(16))
        a = m.appeal("claude-c1", T, [s["event_id"]], "fixed test passes", record_time=t(17))
        with self.assertRaises(GateError) as e:
            m.resolve("mistral-m1", a["event_id"], "reinstated", "x", record_time=t(18))
        self.assertEqual(e.exception.code, "E_RESOLVE_PARTY")
        m.resolve("judge", a["event_id"], "reinstated", "evidence holds", record_time=t(19))
        c = m.state()["state"]["claims"][T]
        self.assertEqual((c["effective_truth"], c["disputed"], c["defeated"]), ("true", False, False))
        self.assertEqual(c["balance"], {"supports": 1, "refutes": 1})

    def test_compartment_filter_on_read(self):
        m = self.m
        m.remember("alex", claim("private thought", speaker="alex", frame="f", compartment="private"), record_time=t(10))
        m.remember("alex", claim("shared thought", speaker="alex", frame="f", compartment="lab"), record_time=t(11))
        visible = json.dumps(m.state(compartments=["lab"])["state"])
        self.assertNotIn("private thought", visible)
        self.assertIn("shared thought", visible)

    def test_filtered_view_reveals_nothing_hidden(self):
        """Review round 2, finding 4: digest/count/head leaked hidden content."""
        digests = []
        for secret in ["merger Friday", "merger Monday"]:
            with tempfile.TemporaryDirectory() as d:
                m = Memory(Path(d) / "m.jsonl")
                m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                           compartments=["lab", "private"], record_time=t(0))
                m.remember("alex", claim("shared", speaker="alex", frame="f", compartment="lab"),
                           record_time=t(1))
                m.episode("alex", secret, compartment="private", record_time=t(2))
                view = m.state(compartments=["lab"])
                self.assertNotIn("count", view)
                self.assertNotIn("head", view)
                self.assertNotIn("last_seq", json.dumps(view))
                digests.append(view["digest"])
                self.assertIn("count", m.state())  # the full view keeps them
        self.assertEqual(digests[0], digests[1])

    def test_verify_budget_and_checkpoint(self):
        m = self.m
        count, head = m.checkpoint()
        with self.assertRaises(GateError) as e:
            m.verify(budget=1)
        self.assertEqual(e.exception.code, "E_EXHAUSTED")
        m.check_checkpoint(count, head)
        # Bytes, not text: text mode would turn \n into \r\n on Windows.
        lines = self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b"".join(lines[:-1]))
        with self.assertRaises(GateError) as e:
            m.check_checkpoint(count, head)
        self.assertEqual(e.exception.code, "E_CHECKPOINT")


class Concurrency(unittest.TestCase):
    """Review finding 9: concurrent appends must serialise, not corrupt."""

    def test_threads_appending_together(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            m = Memory(Path(d) / "memory.jsonl")
            m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                       compartments=["lab"], record_time=t(0))
            writers = ["w1", "w2", "w3", "w4"]
            for i, w in enumerate(writers):
                m.register("alex", w, party=f"p{i}", kind="model", capabilities=["write"],
                           compartments=["lab"], record_time=t(1))
            errors = []

            def work(w):
                try:
                    for i in range(30):
                        m.episode(w, f"{w} note {i}", compartment="lab", record_time=t(2))
                except Exception as e:  # pragma: no cover - surfaced below
                    errors.append(e)

            threads = [threading.Thread(target=work, args=(w,)) for w in writers]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.assertEqual(errors, [])
            self.assertEqual(m.verify()["count"], 1 + len(writers) + 120)

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "needs symlinks")
    def test_processes_via_symlinks_serialise(self):
        """Review round 2, finding 1: separate processes, different paths."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "memory.jsonl"
            m = Memory(real)
            m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                       compartments=["lab"], record_time=t(0))
            writers = [f"w{i}" for i in range(6)]
            for i, w in enumerate(writers):
                m.register("alex", w, party=f"p{i}", kind="model", capabilities=["write"],
                           compartments=["lab"], record_time=t(1))
            script = (
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "from gnomon import Memory;"
                "m = Memory(sys.argv[2]); w = sys.argv[3]\n"
                "for i in range(40): m.episode(w, w + str(i) + 'x' * 20000, compartment='lab',"
                " record_time='2026-10-14T10:02:00Z')"
            )
            procs = []
            for i, w in enumerate(writers):
                link = Path(d) / f"link{i}.jsonl"
                os.symlink(real, link)
                procs.append(subprocess.Popen(
                    [sys.executable, "-c", script, str(HERE.parent), str(link), w]))
            self.assertEqual([p.wait() for p in procs], [0] * len(writers))
            self.assertEqual(m.verify()["count"], 1 + len(writers) + 40 * len(writers))

    def test_missing_log_is_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(GateError) as e:
                Memory(Path(d) / "nope.jsonl").verify()
            self.assertEqual(e.exception.code, "E_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
