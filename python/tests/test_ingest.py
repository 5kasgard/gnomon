"""M2: deterministic extraction and native checkers. Standard library only."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from gnomon import GateError, Memory, claim  # noqa: E402
from gnomon import checkers  # noqa: E402
from gnomon.ingest import (  # noqa: E402
    Candidate, Residue, Sentence, Turn, classify, extract, ingest, parse_document,
    parse_transcript, record_document, record_messages, record_transcript, split_sentences,
)

PEOPLE = ["Alex", "Claude"]
CHAT = (
    "Alex: Let's use Rust for the core. Maybe Python on top?\n"
    "Claude: I'd suggest Python for the bindings. Café support matters, e.g. accents.\n"
    "Alex: Agreed.\n"
    "Alex: I think 2 + 2 = 4. The meeting was at 10:30 today.\n"
    "Claude: I'll send the plan tomorrow. If we use Datalog, queries get simpler.\n"
    "Claude: 14 October 2026 is a Wednesday.\n"
)


def t(minute: int) -> str:
    return f"2026-10-14T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


def s(text: str, speaker: str = "Alex") -> Sentence:
    return Sentence(speaker, text, 0)


def speakers_of(content: str, people=PEOPLE):
    cands, residue = extract(content, participants=people)
    return [(c.sentence.speaker, c.sentence.text) for c in cands], residue


class Parsing(unittest.TestCase):
    def test_turns_and_offsets_are_exact(self):
        turns, unattributed = parse_transcript(CHAT, PEOPLE)
        self.assertEqual(unattributed, [])
        self.assertEqual([x.speaker for x in turns], ["Alex", "Claude", "Alex", "Alex", "Claude", "Claude"])
        for turn in turns:
            self.assertEqual(CHAT[turn.start:turn.start + len(turn.text)], turn.text)
        self.assertIn("10:30", turns[3].text)  # a time, not a speaker

    def test_only_participants_are_speakers(self):
        # Review M2, finding 2: "Step 1:" or "Note:" lines started new speakers.
        turns, _ = parse_transcript("Alex: Plan below.\nStep 1: we will use Postgres.\nNote: fine.", PEOPLE)
        self.assertEqual([x.speaker for x in turns], ["Alex"])

    def test_every_line_break_and_common_layouts(self):
        # Review M2, finding 3: \r, U+2028, NEL, markdown bold, name on its
        # own line and timestamps all misattributed the next speaker.
        for text in [
            "Alex: Let's use Rust for core.\rClaude: We will use Python for it.\r",
            f"Alex: Let's use Rust for core.{chr(0x2028)}Claude: We will use Python for it.",
            "Alex: Let's use Rust for core.\x85Claude: We will use Python for it.",
            "**Alex:** Let's use Rust for core.\n**Claude:** We will use Python for it.",
            "Alex:\nLet's use Rust for core.\nClaude:\nWe will use Python for it.",
            "[10:30] Alex: Let's use Rust for core.\n[10:31] Claude: We will use Python for it.",
        ]:
            got, _ = speakers_of(text)
            self.assertEqual(got, [("Alex", "Let's use Rust for core."), ("Claude", "We will use Python for it.")], repr(text))

    def test_unknown_speakers_are_never_absorbed(self):
        # Review M2 round 2, finding 3: Bob's line became Alex's words.
        for text in ["Alex: We will use Rust for the core.\nBob: We will delete the production database tonight.",
                     "Alex: We will use Rust for the core.\nClaude (assistant): We will delete the production database tonight.",
                     "Alex: We will use Rust for the core.\nSam (PM): We will delete the production database tonight."]:
            got, residue = speakers_of(text)
            self.assertEqual(got, [("Alex", "We will use Rust for the core.")], text)
            self.assertTrue(any("production database" in r.sentence.text and r.reason == "no known speaker"
                                for r in residue), text)

    def test_names_that_look_alike_are_refused(self):
        # Review M2 round 2, findings 6 and 7.
        for people in [["Alex", "ALEX"], [f"Zo{chr(0x00eb)}", f"Zoe{chr(0x0308)}"], [f"Zoe{chr(0x0308)}"], ["Alex\nClaude"]]:
            with self.assertRaises(ValueError, msg=repr(people)):
                parse_transcript("Alex: hi there", people)
        with self.assertRaises(ValueError):
            parse_transcript("Alex: hi there", [f"Stra{chr(0x00df)}e", "STRASSE"])

    def test_sentence_splitting_stays_linear(self):
        # Review M2 round 2, finding 5: 64 s and 74 s.
        start = time.perf_counter()
        extract("Alex: " + "e.g. " * 19990, participants=["Alex"])
        extract("Alex: " + "A. " * 33000, participants=["Alex"])
        self.assertLess(time.perf_counter() - start, 5)

    def test_unattributed_text_is_reported_not_dropped(self):
        # Review M2, finding 11.
        cands, residue = extract("Some preamble here.\nAlex: Let's use Rust for the core.", participants=PEOPLE)
        self.assertEqual([c.sentence.text for c in cands], ["Let's use Rust for the core."])
        self.assertEqual([(r.sentence.text, r.reason) for r in residue], [("Some preamble here.", "no known speaker")])

    def test_continuation_lines_join_the_turn(self):
        turns, _ = parse_transcript("Alex: first line\nsecond line\nClaude: reply", PEOPLE)
        self.assertEqual(turns[0].text, "first line\nsecond line")

    def test_sentences_keep_offsets_and_respect_abbreviations_and_numbers(self):
        for turn in parse_transcript(CHAT, PEOPLE)[0]:
            for sen in split_sentences(turn):
                self.assertEqual(CHAT[sen.start:sen.start + len(sen.text)], sen.text)
        one = split_sentences(Turn("A", "Version 3.5 shipped. Dr. Smith agreed. J. Doe too.", 0))
        self.assertEqual([x.text for x in one], ["Version 3.5 shipped.", "Dr. Smith agreed.", "J. Doe too."])

    def test_documents_split_into_paragraphs(self):
        doc = "First para line one.\nline two.\n\n  Second para.\n"
        turns = parse_document(doc, "alex")
        self.assertEqual([x.text for x in turns], ["First para line one.\nline two.", "Second para."])
        for turn in turns:
            self.assertEqual(doc[turn.start:turn.start + len(turn.text)], turn.text)

    def test_deterministic(self):
        self.assertEqual(extract(CHAT, participants=PEOPLE), extract(CHAT, participants=PEOPLE))


class Rules(unittest.TestCase):
    def check(self, text, stance=None, kind=None, rule=None, residue=None):
        r = classify(s(text), PEOPLE)
        if residue:
            self.assertIsInstance(r, Residue, text)
            self.assertIn(residue, r.reason, text)
            return
        self.assertIsInstance(r, Candidate, f"{text}: {getattr(r, 'reason', '')}")
        if stance:
            self.assertEqual(r.stance, stance, text)
        if kind:
            self.assertEqual(r.statement_kind, kind, text)
        if rule:
            self.assertEqual(r.rule, rule, text)

    def test_stances(self):
        self.check("Let's use Rust for the core.", "decided", rule="decided.lets")
        self.check("We'll go with Postgres for storage.", "decided", rule="decided.go_with")
        self.check("We decided to ship on Friday.", "decided", rule="decided.decided")
        self.check("Maybe Python goes on top.", "suggested", rule="suggested.hedge")
        self.check("We could cache the results here.", "suggested", rule="suggested.modal")
        self.check("I'd suggest Python for the bindings.", "suggested", rule="suggested.suggest")
        self.check("If we use Datalog, queries get simpler.", "hypothetical", "imagine")
        self.check("Unless the tests fail, we use Rust.", "hypothetical", "imagine")
        self.check("Should we use Rust for this?", "asked", "ask")
        self.check("I'll send the plan tomorrow.", "asserted", "promise")
        self.check("The tests pass on main.", "asserted", "describe", rule="asserted.plain")
        self.check("The tests do not pass on main.", "asserted", "describe")

    def test_confidence_cues(self):
        self.assertEqual(classify(s("I think the cache is fine.")).confidence, 5500)
        self.assertEqual(classify(s("The cache is definitely fine.")).confidence, 8500)
        self.assertEqual(classify(s("The cache is fine now.")).confidence, 7000)

    def test_word_boundaries(self):
        self.check("The outlets were closed all week.", "asserted")
        self.check("Mightily they rowed across.", "asserted")

    def test_residue_not_guesses(self):
        self.check("Agreed.", residue="what was said before")
        self.check("Yes!", residue="what was said before")
        self.check("Ship it.", residue="too short")
        self.check("That works well.", residue="refers to something said earlier")
        self.check("Maybe let's use Rust for the core.", residue="mixed signals")
        self.check("I think it definitely works here.", residue="mixed signals")
        self.check(f"normal{chr(0x200b)}text with more words", residue="hidden")
        r = classify(Sentence("", "Some words with no speaker.", 0))
        self.assertEqual(r.reason, "no known speaker")

    def test_context_dependent_sentences_from_a_real_conversation(self):
        for text in ["Let's go then.", "Maybe a combo of all three.",
                     "It's how you got access in the first place.",
                     "I agree, and it's a better design than the one I described.",
                     "That approach works for both of them in the long run."]:
            self.check(text, residue="refers to something said earlier")
        self.check("Then we compile the core first.", "asserted")

    def test_decisions_need_more_than_a_surface_phrase(self):
        # Review M2, finding 10: each of these was recorded as decided, 9000.
        for text, why in [
            ("We haven't decided whether we use Rust.", "negated"),
            ("I'm not sure we decided to use Rust.", "mixed signals"),
            ("I think we decided to use Rust here.", "mixed signals"),
            ("Bob claims we decided to drop Rust.", "someone else's words"),
            ("We use Rust only if the benchmark passes.", "conditional"),
            ("Compile times go up when we use Rust.", "conditional"),
            ("Do we use Rust for the core.", "question"),
            ("Please let us know when the build is ready.", "conditional"),
            ("Please let us know about the build status.", "not a decision"),
            ("Let's say the budget is ten thousand dollars.", "not a decision"),
            ("Let's see whether Rust is fast enough.", "not a decision"),
            ("Let's decide tomorrow about the core.", "not a decision"),
        ]:
            self.check(text, residue=why)

    def test_common_sentences_are_not_over_blocked(self):
        # Review M2 round 2, over-blocking: these were all residue.
        self.check("Python is slow when used for tight loops.", "asserted")
        self.check("We will use Rust, not Go.", "decided")
        self.check("Let's use Rust and not Go.", "decided")
        self.check("Let's use Rust rather than Go for this.", "decided")
        # ...while the unsafe readings stay residue.
        self.check("We use Postgres when we need transactions.", residue="conditional")
        self.check("I'll check the logs when I get home.", residue="conditional")
        self.check("We haven't decided whether we use Rust.", residue="negated")
        self.check("We will not use Rust for this.", "asserted")  # a negative, asserted: accurate
        # Review M2 round 3, finding 3: deferrals are not contrasts.
        for text in ["Let's use Rust, but not yet.", "We use Rust, but not anymore.",
                     "We'll go with Rust, not really.", "Let's use Rust, not ever.",
                     "Let's go with Rust, but not decided.", "We decided on Rust, but not finally."]:
            self.check(text, residue="negated")

    def test_a_speaker_label_inside_a_sentence_is_residue(self):
        self.check("You wrote earlier Alex: we will use Go.", residue="speaker label")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = m = Memory(Path(self.dir.name) / "memory.jsonl")
        m.register("alex", "alex", party="alex", kind="human", capabilities=["owner"],
                   compartments=["lab", "private"], record_time=t(0))
        m.register("alex", "extractor", party="gnomon-rules", kind="tool", operator="alex",
                   capabilities=["write"], compartments=["lab"], record_time=t(1))
        m.register("alex", "calc", party="gnomon-calc", kind="tool", operator="alex",
                   capabilities=["verify"], compartments=["lab"], record_time=t(1))
        self.ep = record_transcript(m, "alex", CHAT, participants=PEOPLE, compartment="lab", record_time=t(2))

    def tearDown(self):
        self.dir.cleanup()

    def claims(self):
        return {c["body"]["statement"]: c["body"] for c in self.m.state()["state"]["claims"].values()}

    def test_ingest_writes_quoted_claims_and_returns_residue(self):
        r = ingest(self.m, "extractor", self.ep, frame="chat 2026-10-14", compartment="lab", record_time=t(3))
        self.assertEqual(r.refused, [])
        by_text = self.claims()
        self.assertEqual(by_text["Let's use Rust for the core."]["stance"], "decided")
        self.assertEqual(by_text["Let's use Rust for the core."]["speaker"], "Alex")
        self.assertEqual(by_text["Maybe Python on top?"]["stance"], "asked")
        self.assertEqual(by_text["I'll send the plan tomorrow."]["speaker"], "Claude")
        for body in by_text.values():
            self.assertEqual(body["origin"], "direct")
            self.assertEqual(CHAT[body["quote_start"]:body["quote_start"] + len(body["quote"])], body["quote"])
        self.assertIn("Agreed.", [x.sentence.text for x in r.residue])

    def test_structured_messages_need_no_parsing(self):
        # Review M2, finding 2: text quoted inside a message stays that
        # sender's words when the source says who sent what.
        ep = record_messages(self.m, "alex", [("Claude", "You wrote this earlier:\nAlex: we will use Go for everything."),
                                              ("Alex", "We will use Rust for the core.")],
                             compartment="lab", record_time=t(3))
        r = ingest(self.m, "extractor", ep, frame="f", compartment="lab", record_time=t(4))
        written = [(c.sentence.speaker, c.sentence.text) for _, c in r.written]
        self.assertIn(("Alex", "We will use Rust for the core."), written)
        self.assertNotIn("Alex", [sp for sp, txt in written if "Go" in txt])

    def test_documents_have_one_author(self):
        ep = record_document(self.m, "alex", "Rust is fast enough for us. Python is friendly to newcomers.",
                             author="Alex", compartment="lab", record_time=t(3))
        r = ingest(self.m, "extractor", ep, frame="f", compartment="lab", record_time=t(4))
        self.assertEqual({c.sentence.speaker for _, c in r.written}, {"Alex"})
        self.assertEqual(r.refused, [])

    def test_gaps_between_turns_are_reported(self):
        # Review M2 round 2, finding 4: a gap that merely began like a label
        # was dropped, words and all.
        content = "Alex: We will use Rust for the core.\nBob: We will delete the production database tonight.\nClaude: Fine by me then."
        ep = self.m.append("alex", "episode", {
            "compartment": "lab", "content": content, "medium": "chat", "participants": PEOPLE,
            "turns": [{"speaker": "Alex", "start": 6, "end": 36},
                      {"speaker": "Claude", "start": content.index("Fine"), "end": len(content)}],
        }, t(3))["event_id"]
        r = ingest(self.m, "extractor", ep, frame="f", compartment="lab", record_time=t(4))
        self.assertTrue(any("production database" in x.sentence.text for x in r.residue))

    def test_episode_without_turns_or_author_is_all_residue(self):
        ep = self.m.episode("alex", "Alex: hi there friend\nClaude: hello again friend", compartment="lab",
                            participants=PEOPLE, record_time=t(3))["event_id"]
        r = ingest(self.m, "extractor", ep, frame="f", compartment="lab", record_time=t(4))
        self.assertEqual(r.written, [])
        self.assertIn("cannot tell who said what", r.residue[0].reason)

    def test_reingest_writes_nothing_new(self):
        ingest(self.m, "extractor", self.ep, frame="f", compartment="lab", record_time=t(3))
        again = ingest(self.m, "extractor", self.ep, frame="f", compartment="lab", record_time=t(4))
        self.assertEqual(again.written, [])
        self.assertTrue(again.refused)
        self.assertTrue(all(code == "E_DUPLICATE" for _, code in again.refused))

    def test_a_lying_judge_cannot_change_the_words_or_the_speaker(self):
        # Review M2, finding 4.
        def judge(res: Residue, content: str):
            fake = replace(res.sentence, text="Alex approved the budget.", speaker="Alex")
            return Candidate(fake, "describe", "decided", "true", 10000, "judge")

        r = ingest(self.m, "extractor", self.ep, frame="f", compartment="lab", judge=judge, record_time=t(3))
        statements = self.claims()
        self.assertNotIn("Alex approved the budget.", statements)
        agreed = statements["Agreed."]
        self.assertEqual((agreed["speaker"], agreed["origin"], agreed["confidence"]), ("Alex", "inferred", 7000))
        self.assertEqual(r.refused, [])

    def test_ingest_only_sees_what_its_writer_may_see(self):
        secret = record_messages(self.m, "alex", [("Alex", "We will use the secret supplier.")],
                                 compartment="private", record_time=t(3))
        with self.assertRaises(GateError) as e:
            ingest(self.m, "extractor", secret, frame="f", compartment="lab", record_time=t(4))
        self.assertEqual(e.exception.code, "E_REF")

    def test_checkers_settle_what_the_machine_can_compute(self):
        ingest(self.m, "extractor", self.ep, frame="f", compartment="lab", record_time=t(3))
        rem = lambda text, truth="true": self.m.remember(
            "extractor", claim(text, speaker="a", frame="f", compartment="lab", truth=truth), record_time=t(4))
        rem("0.1 + 0.2 = 0.3")
        rem("2 + 2 = 5")
        rem("1 + 1 = 2", truth="false")  # asserts a truth is false
        rem("2 + 2 = 5", truth="false")  # asserts a falsehood is false
        rem("We measured 2 + 2 = 5 twice.")
        rem("3 + 3 = 7", truth="unknown")
        report = checkers.run(self.m, "calc", record_time=t(5))
        self.assertEqual(report.failures, [])
        claims = self.m.state()["state"]["claims"]
        got = {(claims[cid]["body"]["statement"], claims[cid]["body"]["truth"]): res for cid, res, _ in report.verdicts}
        self.assertEqual(got[("0.1 + 0.2 = 0.3", "true")], "supports")  # exact, not floating point
        self.assertEqual(got[("2 + 2 = 5", "true")], "refutes")
        self.assertEqual(got[("1 + 1 = 2", "false")], "refutes")  # review M2, finding 5
        self.assertEqual(got[("2 + 2 = 5", "false")], "supports")
        self.assertEqual(got[("We measured 2 + 2 = 5 twice.", "true")], "abstain")
        self.assertEqual(got[("14 October 2026 is a Wednesday.", "true")], "supports")
        self.assertNotIn(("3 + 3 = 7", "unknown"), got)  # nothing asserted, nothing to check
        self.assertEqual(checkers.run(self.m, "calc", record_time=t(6)).verdicts, [])

    def test_one_bad_claim_never_stops_the_checker(self):
        # Review M2, finding 6: a crash on one claim aborted every later one,
        # and claims in compartments the checker can't see aborted the run.
        self.m.remember("alex", claim("2 + 2 = 9", speaker="a", frame="f", compartment="private",
                                      truth="true"), record_time=t(3))
        self.m.remember("extractor", claim(f"14 Augu{chr(0x017f)}t 2026 is a Friday", speaker="a", frame="f",
                                           compartment="lab", truth="true"), record_time=t(3))
        self.m.remember("extractor", claim("2 + 2 = 5", speaker="a", frame="f", compartment="lab",
                                           truth="true"), record_time=t(3))
        report = checkers.run(self.m, "calc", record_time=t(4))
        self.assertEqual(report.failures, [])
        self.assertEqual([r for _, r, _ in report.verdicts], ["refutes"])


class Checkers(unittest.TestCase):
    def j(self, text):
        r = checkers.judge(text)
        return r and r[0]

    def test_arithmetic(self):
        self.assertEqual(self.j("2 + 2 = 4"), "supports")
        self.assertEqual(self.j("2 + 2 = 5"), "refutes")
        self.assertEqual(self.j("(3 + 4) × 2 = 14."), "supports")
        self.assertEqual(self.j("2^10 = 1024"), "supports")
        self.assertEqual(self.j("1/3 + 1/3 = 0.6"), "refutes")
        self.assertIsNone(checkers.judge("1/3 + 1/3 = 0.67"))  # correctly rounded: not wrong
        self.assertEqual(self.j("7 / 2 ≠ 3"), "supports")
        self.assertEqual(self.j("10 - 3 = 7"), "supports")
        for nothing in ["x = 5", "v2 = 3", "1 / 0 = 0", "Rust is fast."]:
            self.assertIsNone(checkers.judge(nothing), nothing)

    def test_hyphens_between_digits_are_not_subtraction(self):
        # Review M2, finding 7.
        for text in ["2026-10-14 = 2002", "2026-10-14 = 2026-10-14", "10-3 = 7", "1 + 1 = 2026-10-14"]:
            self.assertIsNone(checkers.judge(text), text)

    def test_ambiguous_notation_is_left_alone(self):
        # Review M2 round 2, finding 8: each was refuted.
        for text in ["20 + 4 = 4!", "1.000 + 1 = 1.001", "1.500 + 500 = 2.000", "1/3 = 0.333",
                     "2/3 = 0.67", "-2^2 = 4", "2^3^2 = 64"]:
            self.assertIsNone(checkers.judge(text), text)
        self.assertEqual(self.j("0.125 + 0.125 = 0.25"), "supports")  # 3 decimals, but true
        for text in ["1/8 = 0.13", "5/8 = 0.63", "2/3 = 0.666"]:  # half up, truncated
            self.assertIsNone(checkers.judge(text), text)

    def test_skips_never_hide_real_errors(self):
        # Review M2 round 3, findings 4 and 6: wrong under every reading.
        for text in ["0.125 × 8 = 2", "3.142 × 2 = 7", "1.250 + 1.250 = 3.5", "2 + 2 = 5!", "1/3 = 0.4"]:
            self.assertEqual(self.j(text), "refutes", text)

    def test_negation_and_conditions_are_never_refuted(self):
        # Review M2, finding 8: each of these true statements was refuted.
        for text in ["It is not true that 2 + 2 = 5.", "2 + 2 = 5 is wrong", "Pigs fly if 2 + 2 = 5."]:
            self.assertEqual(self.j(text), "abstain", text)

    def test_calendar(self):
        self.assertEqual(self.j("14 October 2026 is a Wednesday"), "supports")
        self.assertEqual(self.j("October 14, 2026 was a Tuesday"), "refutes")
        self.assertEqual(self.j("2024-02-29 is a Thursday"), "supports")
        self.assertEqual(self.j("2025-02-29 is a Saturday"), "refutes")  # no such date
        self.assertIsNone(checkers.judge("114 October 2026 is fine"))

    def test_calendar_edge_cases(self):
        # Review M2, findings 6 and 9.
        self.assertIsNone(checkers.judge(f"14 Augu{chr(0x017f)}t 2026 is a Friday"))  # no crash, no lookalike match
        self.assertIsNone(checkers.judge(f"2026-10-14 is a Wedne{chr(0x017f)}day"))
        self.assertIsNone(checkers.judge("October 4, 1582 was a Thursday"))  # Julian era: not checked
        self.assertIsNone(checkers.judge(f"{chr(0x0662)}{chr(0x0660)}{chr(0x0662)}{chr(0x0666)}-{chr(0x0661)}{chr(0x0660)}-{chr(0x0661)}{chr(0x0664)} is a Wednesday"))

    def test_nothing_can_exhaust_the_machine(self):
        self.assertIsNone(checkers.judge("9^9^9 = 1"))
        self.assertIsNone(checkers.judge("99999999^64 * 99999999^64 * 99999999^64 * 99999999^64 = 1"))
        start = time.perf_counter()
        for text in ["1 " + "+ 1 " * 20000 + "= 3", "(" * 5000 + "1" + ")" * 5000 + " = 1", "1" + " " * 50000 + "x",
                     "1+1=" * 25000, "14 " + " " * 100000 + "October", "1 October " * 20000]:
            checkers.judge(text)
        self.assertLess(time.perf_counter() - start, 5)


if __name__ == "__main__":
    unittest.main()
