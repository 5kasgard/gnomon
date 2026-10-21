# Across datasets: what transfers, what noise does, and which signals are invariant

Written 2026-10-21 before any LongMemEval-S result was computed. Committed
before the run.

**Datasets.** Each is a separate environment, from different authors and
generators:
- **BEAM 500K:** 35 synthetic conversations, about 500K tokens each. The
  combiner was trained here (`beam_learn.md`).
- **LoCoMo:** 10 long conversations between two people, 1,535 answerable
  questions. There is no user or assistant, so speaker A is recorded as
  the user. That is one fixed rule, not a choice per conversation.
- **LongMemEval-S (cleaned):** 500 questions, each with its own history
  of about 490 turns over about 53 sessions. Evidence is the turns marked
  `has_answer` (the answer sessions when none is marked). It has one
  question type BEAM lacks: what the assistant said. **This is the test
  set.** Nothing is tuned on it.

**Experiments.**
1. **Transfer.** The BEAM-trained weights (`beam_learn_results.json`),
   unchanged, scored on LoCoMo and LongMemEval-S against words, hybrid,
   and own words first.
2. **Variety.** The same model trained on BEAM 500K and LoCoMo together,
   each environment weighted equally in the loss. Scored on LongMemEval-S.
3. **Noise.** During training, each feature value is dropped with
   probability p and the rest are rescaled by 1/(1-p). p is chosen from
   {0, 0.1, 0.2, 0.4} by training on BEAM and scoring on LoCoMo, so the
   validation environment is not the test environment. Then scored on
   LongMemEval-S, both BEAM-only and BEAM + LoCoMo.
4. **Topology** (descriptive, run after 1-3). Weights fitted on each
   environment separately, including LongMemEval-S. Signals whose weights
   keep their sign and size across environments are the candidates for
   real regularities. Signals that flip are artefacts of a generator. This
   follows the logic of invariant causal prediction (Peters, Bühlmann and
   Meinshausen, 2016).

**Metric:** recall@10 with directives loaded, intervals by conversation
(by question for LongMemEval-S, where each question has its own history).
Median tokens are reported for every arm.

**Recorded in advance:**
- LongMemEval's "single-session-assistant" questions put the evidence in
  the assistant's words. A rule that favours the user's own words should
  lose there. Whether the learned model does is a test of whether it
  learnt "primary source" or just "user".
- LoCoMo's two speakers make "own words" close to meaningless there.
