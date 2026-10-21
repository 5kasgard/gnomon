"""Stress tests for gnomon.forms: thousands of random profiles and dials
against the invariants every design must keep, and the learner in
simulated worlds (stationary, noisy, and changing) measured by regret."""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gnomon.forms import ALPHABET, SOURCES, TIERS, Dials, Learner, Profile, candidates, compose  # noqa: E402

DELIBERATIVE = {"Q", "B"}


def random_profile(rng: random.Random) -> Profile:
    return Profile(
        parts=rng.choice([1, 1, 2, 5, 24, 40, 41, 300, 5000, 10**6]),
        parallel=rng.random(),
        coupling=rng.choice(["low", "high"]),
        check=rng.choice(["exact", "review", "none"]),
        locatable=rng.random() < 0.3,
        expert=rng.random() < 0.2,
        open_ended=rng.random() < 0.3,
        adversarial=rng.random() < 0.15,
        graded=rng.random() < 0.2,
        tier=rng.choice(TIERS),
        families=rng.choice([1, 1, 2, 3, 8]),
        context_parts=rng.choice([2, 3, 10, 40, 1000]),
        repeats=rng.choice([1, 1, 50, 999, 1000, 10**4, 10**7]),
        narrow=rng.random() < 0.8,
    )


def random_dials(rng: random.Random) -> Dials:
    return Dials(*(rng.choice([0.0, 1.0, rng.random()]) for _ in range(5)))


class InvariantFuzz(unittest.TestCase):
    def test_every_design_keeps_the_invariants(self):
        rng = random.Random(7)
        words = set()
        for n in range(4000):
            p, d = random_profile(rng), random_dials(rng)
            x = compose(p, d)
            ctx = f"case {n}: {p} {d} -> {x.word}"
            words.add(x.shape)
            self.assertTrue(x.stages, ctx)
            letters = [s.letter for s in x.stages]
            for s in x.stages:
                self.assertIn(s.letter, ALPHABET, ctx)
                self.assertGreaterEqual(s.size, 1, ctx)
                self.assertTrue(s.why, ctx)
                for k in s.sources:
                    self.assertIn(k, SOURCES, ctx)
            self.assertTrue(math.isfinite(x.calls) and x.calls >= 0, ctx)
            self.assertGreaterEqual(x.rounds, 1, ctx)
            self.assertGreaterEqual(x.workers, 1, ctx)
            self.assertLessEqual(x.workers, max(p.parts, 1), ctx)
            # Evidence-backed rules that must never be broken:
            if p.check == "exact":
                self.assertNotIn("B", letters, ctx)  # no debate where a check can choose
            for s in x.stages:
                if s.letter in DELIBERATIVE:
                    self.assertLessEqual(s.size, 4, ctx)  # never more than four voices
                if s.letter == "F":
                    self.assertLessEqual(s.size, 2, ctx)  # never more than two tiers
            if p.expert:
                self.assertIn("R", letters, ctx)
                self.assertNotIn("Q", letters, ctx)
            if "I" not in letters:
                self.assertTrue(any("unseen" in w for w in x.warnings), ctx)
            if p.parts > 1 and p.coupling == "high" and p.tier != "haiku":
                self.assertNotIn("D", letters, ctx)
            as_dict = x.as_dict()
            self.assertEqual(as_dict["word"], x.word)
        # Shapes (words without sizes), which the learner keys on, stay a
        # few hundred across 4,000 random profiles spanning the whole space.
        self.assertLess(len(words), 250)

    def test_candidates_are_distinct_and_start_with_the_composed_form(self):
        rng = random.Random(11)
        for _ in range(300):
            p, d = random_profile(rng), random_dials(rng)
            c = candidates(p, d)
            self.assertEqual(c[0].word, compose(p, d).word)
            self.assertEqual(len({x.word for x in c}), len(c))


def world(rng, n_types=4, n_arms=4, noise=0.1):
    """A simulated firm: task types with profiles, forms (arms) whose true
    payoff depends on the type, rewards with noise."""
    profiles = [random_profile(rng) for _ in range(n_types)]
    payoff = [[rng.uniform(0.2, 0.9) for _ in range(n_arms)] for _ in range(n_types)]
    return profiles, payoff, noise


def run(learner, profiles, payoff, noise, rounds, rng, switch_at=None):
    from gnomon.forms import Design, Stage
    letters = "SPQR"
    arms = [Design([Stage(letters[i], 1, "arm", ())], 1.0, 1, 1) for i in range(len(payoff[0]))]
    regret = []
    for t in range(rounds):
        if switch_at is not None and t == switch_at:
            payoff = [list(reversed(row)) for row in payoff]  # the world changes: best becomes worst
        k = rng.randrange(len(profiles))
        pick = learner.choose(profiles[k], arms)
        j = arms.index(pick)
        r = payoff[k][j] + rng.gauss(0, noise)
        learner.update(profiles[k], pick, r)
        regret.append(max(payoff[k]) - payoff[k][j])
    return regret


class LearnerSimulation(unittest.TestCase):
    def test_regret_falls_in_a_stationary_noisy_world(self):
        totals = []
        for seed in range(5):
            rng = random.Random(seed)
            profiles, payoff, noise = world(rng, noise=0.15)
            reg = run(Learner(alpha=0.6), profiles, payoff, noise, 600, rng)
            early, late = sum(reg[:100]) / 100, sum(reg[-100:]) / 100
            self.assertLess(late, early + 1e-9, f"seed {seed}")
            totals.append(late)
        # On average it ends close to the best form for each type of task.
        self.assertLess(sum(totals) / len(totals), 0.05)

    def test_forgetting_trades_steady_regret_for_faster_recovery(self):
        after, steady = {1.0: [], 0.995: []}, {1.0: [], 0.995: []}
        for seed in range(6):
            for decay in (1.0, 0.995):
                rng = random.Random(100 + seed)
                profiles, payoff, noise = world(rng, noise=0.1)
                reg = run(Learner(alpha=0.6, decay=decay, detect=0), profiles, payoff, noise, 900, rng,
                          switch_at=600)
                steady[decay].append(sum(reg[400:600]) / 200)
                after[decay].append(sum(reg[600:700]) / 100)
        mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
        self.assertLess(mean(after[0.995]), mean(after[1.0]))  # notices the change sooner
        self.assertLess(mean(steady[1.0]), mean(steady[0.995]))  # but pays for it when nothing changes

    def test_change_detection_recovers_without_steady_cost(self):
        # Seeds 3000+ were not used to choose the detector's setting.
        for noise in (0.05, 0.3):
            after = {0: [], 8: []}
            steady = {0: [], 8: []}
            alarms = 0
            for seed in range(3000, 3006):
                for detect in (0, 8):
                    rng = random.Random(seed)
                    profiles, payoff, _ = world(rng, noise=noise)
                    reg = run(Learner(alpha=0.6, detect=detect), profiles, payoff, noise, 800, rng, switch_at=500)
                    after[detect].append(sum(reg[500:600]) / 100)
                    rng = random.Random(seed + 50)
                    profiles, payoff, _ = world(rng, noise=noise)
                    lr = Learner(alpha=0.6, detect=detect)
                    reg = run(lr, profiles, payoff, noise, 800, rng)  # a world that never changes
                    steady[detect].append(sum(reg[400:800]) / 400)
                    if detect:
                        alarms += lr.alarms
            mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
            self.assertLess(mean(after[8]), mean(after[0]), noise)
            self.assertLessEqual(mean(steady[8]), mean(steady[0]) + 0.002, noise)
            self.assertLessEqual(alarms, 3, noise)  # false alarms in six unchanging worlds

if __name__ == "__main__":
    unittest.main()
