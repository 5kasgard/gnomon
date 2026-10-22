# Push at scale: what reaches a reader that does not pull

The live tests (live.md) found that what is pushed into the reader's
context gets applied and what is merely offered mostly does not (Gemma
called memory tools 1 time in 12; Claude pulled, but still applied pushed
facts more). The shipped push is the situation model at session start,
1,500 tokens of the person's own most recent sentences.

The live tests ran on short histories. A real memory grows, and a
diagnosis on ConvoMem (16 histories, 46K to 146K tokens of candidate
sentences each; exploratory, not a result) found the session-start push
holds the evidence for 0 to 3.5% of questions, no better than a random
1,500 tokens (1.0 to 7.4%). Nothing chosen without the question can hold
a large history in 1,500 tokens.

The question-dependent push already exists: `--per-message` adds
`context` for each prompt (UserPromptSubmit), and it is off by default
because it costs tokens on every prompt. This test measures both, and
how small the per-message push can be.

## Protocol, fixed before running

- **Records:** BEAM 100K, 1M and 10M; LoCoMo; LongMemEval-S; ConvoMem
  (the cached questions of views.md).
- **Session start:** the situation as shipped (every first-person,
  non-request user sentence; the most recent that fit 1,500 tokens).
  Measured: the share of questions with any evidence message among the
  pushed sentences.
- **Per message:** the shipped view (length-aware windows over the hybrid
  order) at **500, 1,000 and 2,000** tokens. Measured: answer words in
  view (primary) and evidence recall.
- **Decision:**
  - If per message at 2,000 holds the evidence for at least 5 times as
    many questions as the session-start push, on every record, then auto
    mode turns `--per-message` on once the memory's situation no longer
    fits the session-start budget. Small memories are unchanged.
  - Its default budget is the smallest of the three whose answer words in
    view are within 5 points of 2,000's on every record.
- **No model is called.** Run once.

## Result (run once): the rule was not met

From `push_scale_results.json`: the share of questions with evidence in
what is pushed, and answer words in view (judge-free).

| Record | n | Session start, 1,500 | Per message, 500 | 1,000 | 2,000 | Answer words at 500 / 1,000 / 2,000 |
|---|---|---|---|---|---|---|
| BEAM 100K | 275 | 15.6 | 76.0 | 83.3 | 88.7 | 41.6 / 50.7 / 59.2 |
| BEAM 1M | 485 | 7.0 | 65.8 | 75.5 | 83.5 | 37.4 / 46.2 / 55.0 |
| BEAM 10M | 136 | **0.0** | 54.4 | 61.8 | 69.9 | 34.8 / 43.5 / 51.7 |
| LoCoMo | 1,480 | 16.6 | 62.0 | 75.4 | 83.5 | 44.5 / 53.8 / 60.3 |
| LongMemEval-S | 461 | 34.3 | 87.2 | 93.3 | 96.1 | 49.0 / 58.3 / 65.2 |
| ConvoMem | 1,058 | 1.4 | 72.2 | 78.5 | 82.6 | 55.0 / 60.8 / 66.4 |

- **The first condition fails on one record.** Per message at 2,000 holds
  the evidence for 5 to 60 times as many questions as the session-start
  push on five records, but only 2.8 times on LongMemEval-S (96.1 against
  34.3). LongMemEval's start push does unusually well: its histories are
  about 50 short sessions, and 1,500 tokens of recent sentences cover
  several of them.
- **So, by the rule, `--per-message` stays opt-in.** The rule used a
  ratio, so a record where the session-start push already does fairly
  well fails it, even with a 62-point absolute gap. That is a weakness of
  the rule as written, recorded here, not a reason to set it aside after
  seeing the data.
- **Smaller per-message budgets cost too much.** At 1,000 tokens, answer
  words fall 6 to 8 points; at 500, 11 to 18. If per-message is turned
  on, 2,000 is the budget to use.

What it shows, whatever the rule:

- **The session-start push does not scale.** It holds 0 to 16.6% of the
  evidence on every record except LongMemEval, and 0% at 10M. The live
  tests that found push works ran on short histories, where the
  situation fits.
- **What reaches a reader that does not pull, at scale, is what is pushed
  for the message at hand.** Whether that is worth about 2,000 tokens a
  prompt is a cost the owner sets; the measurement is here for that choice.
