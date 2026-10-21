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
