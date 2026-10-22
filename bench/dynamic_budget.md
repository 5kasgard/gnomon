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
