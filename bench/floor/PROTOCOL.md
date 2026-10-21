# The floor benchmark: protocol

Fixed before the second round of results (2026-10-21), so the numbers cannot
be steered by choices made after seeing them.

## Data
- `sms`: UCI SMS Spam Collection, 5,574 messages, spam or not.
- `yelp`: 10,000 Yelp reviews, the star rating (1-5).
- `sentiment`: the same reviews, positive (4-5 stars) or negative (1-2);
  3-star reviews dropped. The LLM's star labels map the same way.

Each task: 300 random items (seed 20261021) form the probe the LLM labels;
the rest is split in half, the first half as the label pool, the second as
the evaluation set no worker trains on.

## Workers
Fixed settings, never tuned on evaluation items:
- rules: written from general knowledge (the SMS rules may reflect the
  author model's prior exposure to this dataset; flagged in results);
- voters (naive Bayes), stumps (300 boosted depth-1 trees), linear
  (logistic regression on word 1-2 grams and character 3-5 grams, C=8);
- mycel: Mycel-D (github.com/5kasgard/mycel) on a 64-dimensional SVD of
  word TF-IDF, K = classes, C = 64 nuclei per class, B = 8, M = 16, grow,
  30 epochs; one configuration, chosen before running;
- llm: Claude Haiku labelling every probe item by reading it.

## The floor
The cheap worker answers; its least confident items go to the LLM.
The escalation share is chosen on one half of the probe and scored on the
other (two-fold), with the rule: the smallest share among
{0, 5, 10, 20, 30, 50, 70, 100}% whose accuracy on the choosing half is at
least the LLM's accuracy on that half minus 1 point. The reported accuracy
and cost are the held-out halves' averages. The in-sample curve is
reported too, labelled as such.

## Reported
Accuracy with Wilson 95% intervals; cost per million items from
`gnomon.firm.TIERS` prices (25 items per request, 250 instruction tokens,
4 characters per token) and measured CPU time at $0.04 per vCPU-hour.
Nothing is dropped: every worker run appears in `results.json`.

## Amendments (after an independent review, 2026-10-21)

Recorded rather than silently applied:
1. The Mycel configuration first fixed here (MycelD, 64 nuclei per class,
   B=8) barely learnt its own training data (SMS: 88.9% on training items
   against 88.0% for the majority class). It is still reported, as
   `mycel-1st`. Added: the configuration Mycel's own round-5 protocol had
   frozen (`frozen/r5_frozen.json` in the Mycel repository: MycelCo with a
   shared pool of 512 nuclei, B=24, M=16, T=512, s=3.0, growth), as
   `mycel`. Neither was tuned on these tasks.
2. Sentiment: the LLM labelled stars, and said 3 on 18 of the 254 probe
   items whose true label is positive or negative. Those count as wrong in
   the headline. Results with the LLM's 3 read as positive, and as
   negative, are reported beside it.
3. LLM output is costed at 12 tokens per item, not 6.
4. This protocol was written after a first round of results had been seen
   (round one: SMS and star ratings, in-sample floor curves).
