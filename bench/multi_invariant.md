# The invariant model, tested on a dataset none of this has seen

Written 2026-10-21 after `multi.md`'s run, before any ConvoMem result was
computed. Committed before the run.

## What `multi.md` found
- **The BEAM-trained combiner did not transfer.**
  - LoCoMo: 41.7 against 47.5 for the hybrid.
  - LongMemEval-S: 77.1 against 78.6 for the hybrid.
- **Feature dropout during training improved transfer at every level
  tried.** BEAM to LoCoMo went 41.7 (no noise), 44.5 (0.1), 44.6 (0.2),
  44.9 (0.4). On LongMemEval-S:

  | Trained on | No noise | Dropout 0.4 |
  |---|---|---|
  | BEAM | 77.1 | 80.7 |
  | BEAM + LoCoMo | 76.0 | 82.4 |

- **Own words first was best overall on LongMemEval-S: 83.6.** It failed,
  as predicted, on questions about the assistant's words: 75.0, against
  92.9 for the varied, noise-trained model.
- **Weights fitted per environment** (BEAM, LoCoMo, LongMemEval-S), the
  base signals:
  - **Kept sign and rough size:** word (+3.1, +5.7, +3.8), meaning
    (+3.6, +2.0, +4.3), meaning rank (+2.4, +1.6, +3.2), surprise when
    written (+1.00, +1.05, +1.28, the most stable), topic surprise
    (+0.4, +1.5, +0.5), atypical (+1.9, +4.6, +0.9) and number (+1.5,
    +0.6, +0.6).
  - **Own words:** +1.4, 0.0 (meaningless in LoCoMo) and +1.2.
  - **Flipped:** shortness (+2.5, -6.6, -0.9) and position in time (-1.8,
    0.0, +0.7).

## Fixed choices
- **Test set: ConvoMem** (Salesforce, arXiv 2511.10523). It is
  independent of BEAM, LoCoMo and LongMemEval.
  - The pre-mixed cases with 150 to 300 conversations each, from the last
    batch of five evidence kinds: user facts, assistant facts, changing
    facts, implicit connections, preferences.
  - 60 cases per kind, sampled with seed 20261021.
  - Evidence is the messages equal to the case's evidence messages.
  - Questions with no evidence message found are skipped and counted.
- **Invariant model (the primary arm).**
  - Only the signals that kept their sign everywhere: word, meaning,
    meaning rank, surprise, topic surprise, atypical, number, own words.
    Shortness and position are dropped.
  - Base columns only, with no question-cue interactions, since the cue
    weights are where BEAM phrasing could hide.
  - Trained on BEAM 500K, LoCoMo and LongMemEval-S, each environment
    weighted equally, with dropout 0.4 (the level chosen in `multi.md`).
- **Also reported:**
  - words, hybrid and own words first;
  - the full model: all signals and cues, the same three environments,
    dropout 0.4;
  - the invariant model without dropout.
- **Metric:** recall@10 with directives, 95% intervals by test case, and
  median tokens.

Nothing will be changed after the run.

**Amendment, before any result.** The first extraction ran out of memory
because it held every qualifying case before sampling. The 60 cases per
file are now drawn by seeded reservoir sampling while streaming. It is
still a seeded random sample of 60; only the sampling mechanics changed.
That also ran out of memory (60 large cases held at once). The final
version reads each file twice. The first pass counts the qualifying cases
and draws 60 of their positions with `random.Random(20261021).sample`,
which is the sampling first written down. The second pass processes one
case at a time.

**Second amendment, before any result.** Each ConvoMem case carries
about 60 questions (18,000 in all), not one. Per case, 5 questions are
now drawn with a seed derived from 20261021, kind and case number, which
gives about 1,500 questions. Each case is processed in a fresh worker
process.

This run also found a product bug. The recall core's analysis cache
lives for the whole process and is capped at 2,000,000 entries by count,
not by size. It grew by about 90 MB per 14,000-message case and is never
freed below the cap, so a long-running server over a large record could
exhaust memory. It should be capped by bytes.

## Result on ConvoMem (one run; `multi_invariant_results.json`)

1,061 questions over histories of 150-300 conversations. Evidence is
inside the candidates for 90.7%. Recall@10 with directives, 95% intervals
by question:

| | Words | Hybrid | Own words first | Full model, dropout | **Invariant, dropout** | Invariant, no dropout |
|---|---|---|---|---|---|---|
| All | 66.2 [63.5, 69.0] | 69.9 [67.1, 72.6] | 71.3 [68.5, 74.0] | 72.7 [70.2, 75.4] | **72.7** [69.9, 75.4] | 73.3 [70.7, 75.9] |
| Assistant facts | 92.7 | 91.5 | 81.4 | 90.3 | 89.9 | 90.7 |
| Changing facts | 62.9 | 67.8 | 74.1 | 73.1 | 73.1 | 73.7 |
| Implicit connections | 19.9 | 22.4 | 23.6 | 23.6 | 23.6 | 24.8 |
| Preferences | 42.7 | 50.8 | 56.9 | 54.4 | 55.2 | 56.0 |
| User facts | 83.4 | 86.5 | 90.0 | 90.8 | 90.0 | 90.0 |
| Median tokens | 419 | 406 | 398 | 404 | 408 | 407 |

**What it says.**
- **Trained across three environments, the learned weighting
  transfers to a fourth.** It is 2.8-3.4 points above the hybrid and
  6.5-7.1 above words alone.
  - Against own words first it gains 1.4-2.0, which is within the
    intervals.
  - It keeps the primary-source nuance own-words-first lacks:
    assistant facts at 89.9-90.7 against 81.4.
- **Restricting to the invariant signals cost nothing:** 72.7 against
  72.7 for the full model. That is 8 weights instead of 66.
- **Dropout did not help here** (72.7 against 73.3 without it). In
  `multi.md` it helped a model trained on BEAM alone, and BEAM with
  LoCoMo. Read together: noise substitutes for variety. When training
  spans enough varied environments, adding noise has nothing left to
  do. That is what domain randomisation predicts. One run, so a
  hypothesis, not a law.
- **Surprise keeps a positive weight** (+0.45, topic surprise +0.40) in
  a model fitted across three environments and tested on a fourth.
- **The frontier is ConvoMem's "implicit connection" questions:** links
  the user never stated, the far connections. Every arm is at 20-25%.
