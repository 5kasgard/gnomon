# Dynamic budget: let the record decide how much to push

push_scale.md found that a reader that does not pull is reached at scale
only by what is pushed for the message at hand, and that a fixed 2,000
tokens per prompt holds the evidence for 70 to 96% of questions. A fixed
budget is the crude version: some prompts have one strong match, some
have many, and some have none.

The literature points the same way. Adaptive top-k work (Tail-Aware
Adaptive-k, arXiv 2606.11907) finds that ranked similarity curves fall
steeply over the relevant items and then flatten into noise, and cuts at
the knee. Calibrated budget allocation (arXiv 2606.29959) finds that
graded budgets beat a fixed one on the cost frontier, and that more
context can hurt. depth.md found the same here: past 2,000 tokens, more
view dilutes the reader.

## Protocol, fixed before running

- **Records:** BEAM 100K, 1M and 10M; LoCoMo; LongMemEval-S; ConvoMem (the
  cached questions of push_scale.md). Conversations are split in two by a
  hash of their name: **dev** (tuning) and **test** (reported).
- **View:** the shipped one (length-aware windows over the hybrid order);
  every rule below changes only how many of its lines are kept.
- **Fixed budgets (the frontier):** 250, 500, 1,000, 1,500, 2,000 and
  3,000 tokens. The cost-benefit frontier is the line through their mean
  tokens and mean answer words in view, per record.
- **Dynamic rules,** each capped at 2,000 tokens. Each line has a
  similarity: the cosine between the question and its message, as
  cached.
  - **Knee (no tuning):** sort the similarities of the top 50 lines in
    the hybrid order. The knee is the point furthest below the chord from
    the first to the last value (Kneedle). Keep the lines, in hybrid
    order, whose similarity is at least the knee's.
  - **Relative (one parameter):** keep the lines whose similarity is at
    least α times the best. α is chosen on dev from 0.50 to 0.95 in steps
    of 0.05, maximising the pooled gain defined below.
- **Measured on test:** for each rule and record, mean tokens and mean
  answer words in view. **Gain** is the rule's answer words minus the
  fixed frontier's at the same mean tokens. Bootstrap 95% intervals by
  conversation (BEAM, LoCoMo) or question (otherwise).
- **Decision:** a rule whose pooled test gain has an interval above 0,
  and which loses no more than 1 point on any record, becomes how
  `--per-message` sizes its push (capped at its budget). If both
  qualify, the one with the larger pooled gain. Otherwise the fixed
  budget stays.
- **No model is called.** Run once.

## Result (run once): both rules lose to a fixed budget

Gain is answer words in view against the fixed frontier at the same mean
tokens, on the test half (`dynamic_budget_results.json`). Dev chose
α = 0.50, the edge of the grid; every α lost on dev (−2.2 to −16.0).

| Record (test n) | Knee: tokens / words | Knee gain | Relative 0.50: tokens / words | Relative gain | Fixed 2,000: tokens / words |
|---|---|---|---|---|---|
| BEAM 100K (165) | 477 / 38.3 | −4.0 [−7.0, −0.9] | 1,622 / 56.3 | −1.1 [−3.6, +1.6] | 1,968 / 59.5 |
| BEAM 1M (262) | 391 / 32.5 | −2.6 [−4.3, −0.9] | 1,758 / 53.2 | −0.6 [−1.5, +0.2] | 1,970 / 55.5 |
| BEAM 10M (40) | 365 / 29.4 | −5.6 [−13.2, −1.3] | 1,862 / 55.7 | +0.3 [−0.1, +0.5] | 1,967 / 55.9 |
| LoCoMo (900) | 198 / 15.4 | −13.3 [−15.0, −11.3] | 1,591 / 40.9 | −15.1 [−17.2, −12.5] | 1,977 / 57.9 |
| LongMemEval-S (227) | 595 / 46.1 | −3.7 [−6.7, −0.7] | 1,053 / 54.5 | −2.6 [−5.5, +0.0] | 1,968 / 63.1 |
| ConvoMem (596) | 247 / 38.6 | −9.4 [−11.5, −7.2] | 1,524 / 61.2 | −2.5 [−3.6, −1.4] | 1,977 / 65.9 |
| **Pooled** | | **−6.4 [−7.8, −5.3]** | | **−3.6 [−4.4, −2.8]** | |

Neither rule qualifies, so `--per-message` keeps a fixed budget.

Why it fails here when it works in the papers:

- **The similarity curve does not mark where the evidence ends.** The
  view is ordered by a fusion of words and meaning, and much of the
  evidence is found by its words, not its meaning. Cutting on the
  meaning score drops lines the words had ranked well. LoCoMo, a dialogue
  where evidence is short replies with little in common with the
  question's meaning, loses most.
- **The fixed frontier is nearly straight up to 2,000 tokens.** Each
  extra token buys about as much as the last (push_scale.md: 500, 1,000
  and 2,000 tokens give 41.6, 50.7 and 59.2 answer words on BEAM 100K),
  so there is no cheap knee to find.

So a per-prompt budget that adapts to the prompt has to come from
somewhere other than the similarity of what was found.
