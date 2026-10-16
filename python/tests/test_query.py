"""M3: queries with proofs. Standard library only.

* The Rust engine agrees with a naive reference evaluator on hundreds of
  random programs (recursion, negation, comparisons, mixed types).
* Every proof the core returns checks out under gnomon.proofs, which has its
  own parser, canonical JSON and hashing, and reads the log itself.
* Tampered proofs and tampered logs are caught.
"""

from __future__ import annotations

import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import datalog_reference  # noqa: E402
import gnomon  # noqa: E402
from gnomon import GateError, Memory, claim  # noqa: E402
from gnomon import checkers, proofs  # noqa: E402
from gnomon.ingest import ingest, record_transcript  # noqa: E402


def t(minute: int) -> str:
    return f"2026-10-14T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


def answer_set(result: dict) -> set:
    return {tuple(a["values"]) for a in result["answers"]}


# ------------------------------------------------------------ random programs

CONSTS = [0, 1, 2, "a", "b", "B", "ab", "1"]


def random_program(rng: random.Random) -> str:
    lines = []
    edb = {"e0": 2, "e1": 1, "e2": 2}
    for name, arity in edb.items():
        for _ in range(rng.randint(4, 16)):
            vals = [rng.choice(CONSTS) for _ in range(arity)]
            lines.append(f"{name}({', '.join(json.dumps(v) for v in vals)}).")
    idb = {f"p{i}": rng.choice([1, 2]) for i in range(6)}
    level = {f"p{i}": i // 2 for i in range(6)}

    def term(bound, allow_new=True):
        r = rng.random()
        if bound and r < 0.55:
            return rng.choice(sorted(bound))
        if r < 0.75 and allow_new:
            v = f"V{rng.randint(0, 3)}"
            return v
        if r < 0.85 and allow_new:
            return "_"
        return json.dumps(rng.choice(CONSTS))

    for head, harity in idb.items():
        for _ in range(rng.randint(1, 3)):
            bound: set[str] = set()
            body = []
            usable = list(edb) + [p for p in idb if level[p] <= level[head]]
            for _ in range(rng.randint(1, 3)):
                pred = rng.choice(usable)
                ar = edb.get(pred) or idb[pred]
                args = [term(bound) for _ in range(ar)]
                body.append(f"{pred}({', '.join(args)})")
                bound |= {a for a in args if a.startswith("V")}
            if bound and rng.random() < 0.4:
                lower = [p for p in list(edb) + list(idb) if p in edb or level[p] < level[head]]
                pred = rng.choice(lower)
                ar = edb.get(pred) or idb[pred]
                args = [rng.choice(sorted(bound)) if rng.random() < 0.6 else rng.choice(["_", json.dumps(rng.choice(CONSTS))])
                        for _ in range(ar)]
                body.append(f"not {pred}({', '.join(args)})")
            if bound and rng.random() < 0.4:
                op = rng.choice(["=", "!=", "<", "<=", ">", ">="])
                a = rng.choice(sorted(bound))
                b = rng.choice(sorted(bound)) if rng.random() < 0.5 else json.dumps(rng.choice(CONSTS))
                body.append(f"{a} {op} {b}")
            if bound and rng.random() < 0.15:
                body.append(f"contains({rng.choice(sorted(bound))}, {json.dumps(rng.choice(['a', 'B', '']))})")
            if bound and rng.random() < 0.2:
                new = "W"
                body.append(f"{new} = {rng.choice(sorted(bound))}")
                bound.add(new)
            if not bound:
                continue
            hargs = [rng.choice(sorted(bound)) if rng.random() < 0.85 else json.dumps(rng.choice(CONSTS))
                     for _ in range(harity)]
            lines.append(f"{head}({', '.join(hargs)}) :- {', '.join(body)}.")
    q = rng.choice(list(idb))
    qargs = [f"Q{i}" if rng.random() < 0.92 else json.dumps(rng.choice(CONSTS)) for i in range(idb[q])]
    lines.append(f"?- {q}({', '.join(qargs)}).")
    return "\n".join(lines)


class Canonical(unittest.TestCase):
    def test_the_checkers_canonical_json_matches_the_reference_and_core(self):
        import jcs_reference
        from gnomon import _jcs
        rng = random.Random(5)
        alphabet = ["a", "Z", "é", "\u2028", "\x00", "\x1f", '"', "\\", "\U0001F600", "\uffff", "\u00e9", "ö", "\t"]

        def text():
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))

        def value(depth=0):
            r = rng.random()
            if depth > 3 or r < 0.3:
                return rng.choice([None, True, False, rng.randint(-2**53 + 1, 2**53 - 1), text()])
            if r < 0.6:
                return [value(depth + 1) for _ in range(rng.randint(0, 4))]
            return {text(): value(depth + 1) for _ in range(rng.randint(0, 4))}

        for _ in range(3000):
            v = value()
            want = jcs_reference.canonical(v)
            self.assertEqual(_jcs.canonical(v), want)
        for _ in range(200):
            v = value()
            self.assertEqual(_jcs.canonical(v), gnomon.canonicalize(v))
        # Review M3 round 4, finding 4: lone surrogates are not text.
        for bad in ({"\ud800": 1}, "\udfff", ["a\ud800b"]):
            with self.assertRaises(UnicodeError):
                _jcs.canonical(bad)


class Differential(unittest.TestCase):
    def test_engine_matches_naive_reference(self):
        rng = random.Random(20261016)
        checked = nonempty = 0
        for i in range(600):
            src = random_program(rng)
            try:
                got = gnomon.datalog(src)
            except GateError as e:
                # Only an undefined relation (a p_i with no surviving rule) is
                # expected; the reference must then fail on it too.
                self.assertIn("unknown relation", e.message, src)
                continue
            want = datalog_reference.answers(src)
            self.assertEqual(answer_set(got), want, f"program {i}:\n{src}")
            self.assertEqual(proofs.check(got, src), [], f"program {i}:\n{src}")
            checked += 1
            nonempty += bool(want)
        self.assertGreater(checked, 400)
        self.assertGreater(nonempty, 150)

    def test_transitive_closure_on_a_larger_graph(self):
        rng = random.Random(7)
        edges = {(rng.randint(0, 59), rng.randint(0, 59)) for _ in range(150)}
        src = " ".join(f"e({a}, {b})." for a, b in sorted(edges))
        src += " p(X, Y) :- e(X, Y). p(X, Z) :- p(X, Y), e(Y, Z). ?- p(X, Y)."
        got = gnomon.datalog(src)
        self.assertEqual(answer_set(got), datalog_reference.answers(src))
        self.assertEqual(proofs.check(got, src), [])


class Memories(unittest.TestCase):
    CHAT = ("Alex: Let's use Rust for the core. Maybe Python on top?\n"
            "Claude: I'd suggest Python for the bindings.\n"
            "Claude: 14 October 2026 is a Wednesday.\n")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "memory.jsonl"
        self.m = m = Memory(self.log)
        m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                   compartments=["lab", "private"], record_time=t(0))
        m.register("alex", "extractor", party="gnomon-rules", kind="tool",
                   capabilities=["write"], compartments=["lab"], record_time=t(1))
        m.register("alex", "calc", party="gnomon-calc", kind="tool",
                   capabilities=["verify"], compartments=["lab"], record_time=t(1))
        ep = record_transcript(m, "alex", self.CHAT, participants=["Alex", "Claude"],
                               compartment="lab", record_time=t(2))
        ingest(m, "extractor", ep, frame="chat", compartment="lab", record_time=t(3))
        self.wrong = m.remember("extractor", claim("2 + 2 = 5", speaker="Claude", frame="f",
                                                   compartment="lab", truth="true"), record_time=t(4))
        checkers.run(m, "calc", record_time=t(5))
        m.remember("alex", claim("Supplier is Acme", speaker="alex", frame="f", compartment="private",
                                 stance="decided", truth="true", origin="manual"), record_time=t(6))

    def tearDown(self):
        self.dir.cleanup()

    def ask(self, program, reader="extractor", as_of=None):
        r = self.m.query(reader, program, as_of)
        self.assertEqual(proofs.check(r, program, gnomon.VIEWS, self.log), [], program)
        return r

    def test_decisions_with_their_words_and_speaker(self):
        r = self.ask("?- decision(C), statement(C, S), speaker(C, P).")
        self.assertEqual([a["values"][1:] for a in r["answers"]], [["Let's use Rust for the core.", "Alex"]])
        text = proofs.explain(r)
        self.assertIn("decision(", text)
        self.assertIn("by view: decision(C) :- stance(C, \"decided\"), believed(C).", text)
        self.assertIn("absent: disputed(", text)
        self.assertIn("extractor/", text)  # the event that recorded it

    def test_computed_verdicts_are_queryable_and_proved(self):
        r = self.ask('?- verdict(V, C, "calc", R, "computed"), statement(C, S).')
        got = {(a["values"][2], a["values"][3]) for a in r["answers"]}
        self.assertIn(("refutes", "2 + 2 = 5"), got)
        self.assertIn(("supports", "14 October 2026 is a Wednesday."), got)
        r = self.ask(f'?- challenged("{self.wrong}").')
        self.assertEqual(len(r["answers"]), 1)

    def test_compartments_and_readers(self):
        r = self.ask("?- claim(C), statement(C, S), contains(S, \"acme\").", reader="extractor")
        self.assertEqual(r["answers"], [])
        r = self.ask("?- claim(C), statement(C, S), contains(S, \"acme\").", reader="alex")
        self.assertEqual(len(r["answers"]), 1)
        with self.assertRaises(GateError) as e:
            self.m.query("nobody", "?- claim(C).")
        self.assertEqual(e.exception.code, "E_REF")

    def test_as_of(self):
        r = self.ask("?- verdict(V, C, W, R, M).", as_of=t(4))
        self.assertEqual(r["answers"], [])
        self.assertEqual(r["as_of"], t(4))
        r = self.ask("?- verdict(V, C, W, R, M).", as_of=t(5))
        self.assertGreater(len(r["answers"]), 0)

    def test_errors_carry_line_numbers(self):
        with self.assertRaises(GateError) as e:
            self.m.query("alex", "mine(C) :- claim(C).\n?- mien(C).")
        self.assertEqual(e.exception.code, "E_QUERY")
        self.assertIn("line 2", e.exception.message)
        self.assertIn("unknown relation 'mien/1'", e.exception.message)

    def test_tampered_proofs_are_caught(self):
        prog = "?- decision(C), statement(C, S)."
        r = self.ask(prog)
        nodes = r["nodes"]
        leaf = next(k for k, n in nodes.items() if n.get("sources") and n["fact"][0] == "statement")

        bad = copy.deepcopy(r)
        bad["nodes"][leaf]["fact"][2] = "Let's use Go for the core."
        self.assertTrue(any("does not state" in p or "does not follow" in p
                            for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))

        bad = copy.deepcopy(r)
        bad["nodes"][leaf]["sources"][0]["hash"] = "0" * 64
        self.assertTrue(any("not in the log" in p for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))

        bad = copy.deepcopy(r)
        decision = next(k for k, n in nodes.items() if n["fact"][0] == "decision")
        bad["rules"][bad["nodes"][decision]["rule"]]["text"] = "decision(C) :- stance(C, \"decided\")."
        self.assertTrue(any("not in the program" in p for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))

        bad = copy.deepcopy(r)
        believed = next(k for k, n in nodes.items() if n["fact"][0] == "believed")
        self.assertEqual(len(bad["nodes"][believed]["absent"]), 1)
        bad["nodes"][believed]["absent"] = []
        self.assertTrue(any("does not follow" in p for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))

        bad = copy.deepcopy(r)
        bad["nodes"][decision]["premises"].append(r["answers"][0]["proof"])
        self.assertTrue(any("circular" in p or "does not follow" in p
                            for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))

        bad = copy.deepcopy(r)
        bad["answers"][0]["values"][1] = "something else"
        self.assertTrue(any("does not prove it" in p for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))

    def test_a_tampered_log_fails_the_check(self):
        prog = "?- decision(C)."
        r = self.ask(prog)
        lines = self.log.read_bytes().split(b"\n")
        lines[3] = lines[3].replace(b"Let's use Rust", b"Let's use Ruby")
        tampered = Path(self.dir.name) / "tampered.jsonl"
        tampered.write_bytes(b"\n".join(lines))
        self.assertTrue(proofs.check(r, prog, gnomon.VIEWS, tampered))

    def test_a_relabelled_view_is_caught_even_through_absence(self):
        # Review M3 round 4, finding 1: the later event breaks a negation
        # without touching anything the proof cites.
        prog = 'bar_exists :- statement(_, "bar").\n?- statement(C, "foo"), not bar_exists.'
        self.m.remember("extractor", claim("foo", speaker="x", frame="f", compartment="lab", truth="true"),
                        record_time=t(7))
        early = self.m.query("extractor", prog)
        self.assertEqual(len(early["answers"]), 1)
        self.m.remember("extractor", claim("bar", speaker="x", frame="f", compartment="lab", truth="true"),
                        record_time=t(8))
        self.assertEqual(self.m.query("extractor", prog)["answers"], [])
        self.assertEqual(proofs.check(early, prog, gnomon.VIEWS, self.log), [])
        bad = copy.deepcopy(early)
        bad["as_of"] = t(8)
        self.assertTrue(any("left out of its view" in p for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)))
        # Review M3 round 5: the same forgery, dropping what would expose it.
        for drop in (["view"], ["through"], ["through", "as_of"], ["view", "through", "as_of"]):
            bad = copy.deepcopy(early)
            bad["as_of"] = t(8)
            for k in drop:
                bad[k] = None
            self.assertTrue(any("does not state" in p for p in proofs.check(bad, prog, gnomon.VIEWS, self.log)), drop)

    def test_hidden_events_never_raise_false_alarms(self):
        # Review M3 round 4, finding 2: a writer id registered in a hidden
        # compartment matched a speaker named in the proof.
        self.m.remember("extractor", claim("hello", speaker="bob", frame="f", compartment="lab", truth="true"),
                        record_time=t(7))
        self.m.register("alex", "bob", party="bob", kind="model", capabilities=["write"],
                        compartments=["private"], record_time=t(8))
        prog = '?- speaker(C, "bob").'
        r = self.m.query("extractor", prog, as_of=t(9))
        self.assertEqual(len(r["answers"]), 1)
        self.assertEqual(proofs.check(r, prog, gnomon.VIEWS, self.log), [])
        r = self.m.query("extractor", prog)
        self.assertEqual(proofs.check(r, prog, gnomon.VIEWS, self.log), [])

    def test_the_view_must_be_the_readers(self):
        prog = "?- decision(C)."
        r = self.ask(prog)
        self.assertEqual(r["view"], {"reader": "extractor", "compartments": ["lab"]})
        bad = copy.deepcopy(r)
        bad["view"]["compartments"] = ["lab", "private"]
        self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log))
        bad = copy.deepcopy(r)
        bad["view"]["compartments"] = ["private"]
        self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log))

    def test_every_view_is_checkable(self):
        for line in gnomon.VIEWS.splitlines():
            if ":-" not in line:
                continue
            head = line.split(":-")[0].strip()
            name = head.split("(")[0]
            arity = head.count(",") + 1
            args = ", ".join(f"X{i}" for i in range(arity))
            self.ask(f"?- {name}({args}).", reader="alex")


class Disputes(unittest.TestCase):
    """Review M3, findings 6 and 7: facts the core derives from several
    events (truth after rulings, defeat, resolutions) must check out when
    genuine, and forged ones must not."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "m.jsonl"
        self.m = m = Memory(self.log)
        m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                   compartments=["ca"], record_time=t(0))
        for i in range(1, 7):
            m.register("alex", f"w{i}", party=f"p{i}", kind="model", capabilities=["write", "verify"],
                       compartments=["ca"], record_time=t(1))

    def tearDown(self):
        self.dir.cleanup()

    def c(self, writer, text, minute):
        return self.m.remember(writer, claim(text, speaker="x", frame="f", compartment="ca", truth="true"),
                               record_time=t(minute))

    def all_relations_check_out(self):
        schema = json.loads(json.dumps(gnomon._native.call("gn_views")["schema"]))
        for name, arity in schema:
            prog = f"?- {name}({', '.join(f'X{i}' for i in range(arity))})."
            r = self.m.query("w6", prog)
            self.assertEqual(proofs.check(r, prog, gnomon.VIEWS, self.log), [], prog)

    def contest(self):
        x, y = self.c("w1", "X holds", 2), self.c("w2", "Y holds", 3)
        ct = self.m.supersede("w2", x, y, "contested", [], "I disagree", t(4))
        return x, y, ct["event_id"]

    def truth(self, cid):
        return self.m.query("w6", f'?- truth("{cid}", T).')["answers"][0]["values"][0]

    def test_a_refutation_that_ends_a_contest(self):
        x, y, _ = self.contest()
        v = self.m.verdict("w3", x, "refutes", "computed", record_time=t(5))
        z = self.c("w3", "Z holds", 6)
        self.m.supersede("w3", x, z, "refuted", [v["event_id"]], "computed", t(7))
        self.assertEqual((self.truth(x), self.truth(y)), ("false", "true"))
        self.all_relations_check_out()

    def test_every_ruling(self):
        for outcome, by in [("old", "w5"), ("new", "w5"), ("both", "w5"), ("withdrawn", "w2")]:
            with self.subTest(outcome=outcome):
                self.tearDown()
                self.setUp()
                x, y, ct = self.contest()
                self.m.resolve(by, ct, outcome, "ruling", t(5))
                self.all_relations_check_out()

    def test_appeals_reinstated_and_upheld(self):
        for outcome in ["reinstated", "upheld"]:
            with self.subTest(outcome=outcome):
                self.tearDown()
                self.setUp()
                x, y = self.c("w1", "X holds", 2), self.c("w2", "Y holds", 3)
                v = self.m.verdict("w3", x, "refutes", "computed", record_time=t(4))
                self.m.supersede("w2", x, y, "refuted", [v["event_id"]], "computed", t(5))
                v2 = self.m.verdict("w4", x, "supports", "computed", record_time=t(6))
                ap = self.m.appeal("w1", x, [v2["event_id"]], "new evidence", t(7))
                self.all_relations_check_out()  # while open
                self.m.resolve("w5", ap["event_id"], outcome, "ok", t(8))
                self.assertEqual(self.truth(x), "true" if outcome == "reinstated" else "false")
                self.all_relations_check_out()

    def forge(self, result, fact):
        bad = copy.deepcopy(result)
        for n in bad["nodes"].values():
            if n["fact"][0] == fact[0]:
                n["fact"] = list(fact)
            if n["fact"][0] == "?":
                n["fact"] = ["?"] + list(fact[1:])
        bad["answers"][0]["values"] = list(fact[1:])
        return bad

    def test_forged_resolutions_are_caught(self):
        x, y, ct = self.contest()
        v = self.m.verdict("w3", x, "refutes", "computed", record_time=t(5))
        z = self.c("w3", "Z holds", 6)
        rf = self.m.supersede("w3", x, z, "refuted", [v["event_id"]], "computed", t(7))["event_id"]
        prog = "?- resolution(D, O, R)."
        r = self.m.query("w6", prog)
        self.assertEqual(r["answers"][0]["values"], [ct, "new", rf])
        for fake in [("resolution", "no-such-dispute", "both", rf), ("resolution", ct, "old", rf),
                     ("resolution", ct, "both", rf)]:
            self.assertTrue(proofs.check(self.forge(r, fake), prog, gnomon.VIEWS, self.log), fake)

    def test_forged_truth_citing_an_unrelated_ruling_is_caught(self):
        x, y = self.c("w1", "X holds", 2), self.c("w2", "Y holds", 3)
        v = self.m.verdict("w3", x, "refutes", "computed", record_time=t(4))
        self.m.supersede("w2", x, y, "refuted", [v["event_id"]], "computed", t(5))
        v2 = self.m.verdict("w4", x, "supports", "computed", record_time=t(6))
        ap = self.m.appeal("w1", x, [v2["event_id"]], "new evidence", t(7))
        ruling = self.m.resolve("w5", ap["event_id"], "reinstated", "ok", t(8))
        prog = f'?- truth("{y}", T).'
        r = self.m.query("w6", prog)
        bad = self.forge(r, ("truth", y, "false"))
        for n in bad["nodes"].values():
            if n["fact"][0] == "truth":
                n["sources"] = [{"event": ruling["event_id"], "kind": "resolve", "hash": ruling["hash"]}]
        self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log))

    def ref(self, eid):
        for line in self.log.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev["event_id"] == eid:
                return {"event": eid, "kind": ev["kind"], "hash": ev["hash"]}
        raise KeyError(eid)

    def test_a_closer_must_really_have_ended_the_contest(self):
        # Review M3 round 2, finding 2: a contest ruled "old", then a later
        # refutation of X, cited as if it had ended the contest.
        x, y, ct = self.contest()
        self.m.resolve("w5", ct, "old", "ruling", t(5))
        v = self.m.verdict("w3", x, "refutes", "computed", record_time=t(6))
        z = self.c("w3", "Z holds", 7)
        rf = self.m.supersede("w3", x, z, "refuted", [v["event_id"]], "computed", t(8))["event_id"]
        y_first = self.m.state()["state"]["claims"][y]["first_event"]
        self.assertEqual(self.truth(y), "false")

        prog = f'?- truth("{y}", T).'
        r = self.m.query("w6", prog)
        bad = copy.deepcopy(r)
        for n in bad["nodes"].values():
            if "sources" in n:
                n["fact"], n["sources"] = ["truth", y, "true"], [self.ref(ct), self.ref(rf), self.ref(y_first)]
            else:
                n["fact"] = ["?", "true"]
        bad["answers"][0]["values"] = ["true"]
        self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log))

        prog = "?- resolution(D, O, R)."
        r = self.m.query("w6", prog)
        self.assertEqual(proofs.check(r, prog, gnomon.VIEWS, self.log), [])
        bad = copy.deepcopy(r)
        for n in bad["nodes"].values():
            if "sources" in n:
                n["fact"], n["sources"] = ["resolution", ct, "new", rf], [self.ref(ct), self.ref(rf)]
            else:
                n["fact"] = ["?", ct, "new", rf]
        bad["answers"][0]["values"] = [ct, "new", rf]
        self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log))

    def test_only_the_latest_ruling_counts(self):
        # Review M3 round 2: X wins one contest, then loses a later one; a
        # proof citing the earlier ruling (or just X's claim) is stale.
        x, y, z = self.c("w1", "X holds", 2), self.c("w2", "Y holds", 3), self.c("w3", "Z holds", 4)
        c1 = self.m.supersede("w2", x, y, "contested", [], "no", t(5))["event_id"]
        r1 = self.m.resolve("w5", c1, "old", "ruling", t(6))["event_id"]
        c2 = self.m.supersede("w3", x, z, "contested", [], "no", t(7))["event_id"]
        self.m.resolve("w6", c2, "new", "ruling", t(8))
        x_first = self.m.state()["state"]["claims"][x]["first_event"]
        self.assertEqual(self.truth(x), "false")
        prog = f'?- truth("{x}", T).'
        r = self.m.query("w4", prog)
        self.assertEqual(proofs.check(r, prog, gnomon.VIEWS, self.log), [])
        for sources in ([c1, r1, x_first], [x_first]):
            bad = copy.deepcopy(r)
            for n in bad["nodes"].values():
                if "sources" in n:
                    n["fact"], n["sources"] = ["truth", x, "true"], [self.ref(e) for e in sources]
                else:
                    n["fact"] = ["?", "true"]
            bad["answers"][0]["values"] = ["true"]
            self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log), sources)

    def test_a_past_view_cannot_pose_as_a_later_one(self):
        # Review M3 round 3, finding 1: a proof made as of t(4) (X believed)
        # relabelled as a later moment, after X was refuted.
        x, y = self.c("w1", "X holds", 2), self.c("w2", "Y holds", 3)
        prog = f'?- believed("{x}").'
        early = self.m.query("w6", prog)
        v = self.m.verdict("w3", x, "refutes", "computed", record_time=t(5))
        self.m.supersede("w2", x, y, "refuted", [v["event_id"]], "computed", t(6))
        self.assertEqual(early["as_of"], early["through"]["time"])
        self.assertEqual(proofs.check(early, prog, gnomon.VIEWS, self.log), [])  # true of its moment
        self.assertEqual(self.m.query("w6", prog)["answers"], [])
        for later in (None, t(9)):
            bad = copy.deepcopy(early)
            bad["as_of"] = later
            self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log), later)

    def test_events_after_the_view_are_not_part_of_it(self):
        x, y, ct = self.contest()
        prog = "?- disputed(C)."
        r = self.m.query("w6", prog)
        self.assertEqual(len(r["answers"]), 2)
        self.m.resolve("w5", ct, "old", "ruling", t(5))
        # The proof was right for its view, and stays right as the log grows.
        self.assertEqual(proofs.check(r, prog, gnomon.VIEWS, self.log), [])
        bad = copy.deepcopy(r)
        bad["through"] = None
        self.assertTrue(proofs.check(bad, prog, gnomon.VIEWS, self.log))

    def test_repeated_not_is_an_error_in_the_checker_too(self):
        with self.assertRaises(proofs.ParseError):
            proofs.parse("p(1). ?- " + "not " * 20000 + "p(1).")


if __name__ == "__main__":
    unittest.main()
