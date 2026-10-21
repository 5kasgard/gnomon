# Implicit connections: why cheap signals cannot find them

Measured 2026-10-22 on a development batch of ConvoMem implicit-connection
cases (`batched_046`, 30 histories of 100-300 conversations, 83
questions). This is not the test batch `batched_047` scored in
`multi_invariant.md`, where every method reaches 20-25%.

## What the category is
ConvoMem's paper (arXiv 2511.10523) defines it as applying unstated context
to later requests. The user breaks an ankle; weeks later they ask how to
relax at the weekend, and a good answer must account for the cast. On the
development batch the evidence is always the user describing their own
situation:
- a commitment: "a recurring dental appointment every Tuesday at 3 PM";
- a plan: "offline for two weeks";
- a habit: "long runs every Saturday morning";
- a constraint: "the budget will be tighter";
- a goal.

The later question asks for a plan or a suggestion that the situation
should shape. The link is applicability, not similarity.

## Measured
- **A plain rule for self-disclosure** (first person, not a request, with
  a state or commitment word) covers the evidence in 73% of questions. It
  also marks about 1,818 sentences per history (median).
- **Among those disclosures**, the evidence ranks at median 394 by meaning
  similarity to the question. It is in the top 10 for only 14% of
  questions.
- **Separation (AUC) among disclosures, where 0.5 is chance:**

  | Signal | AUC |
  |---|---|
  | Meaning similarity | 0.742 |
  | Sentence length | 0.699 |
  | Surprise | 0.607 |
  | Life-event words | 0.538 |
  | Time-scope words | 0.527 |
  | Position | 0.479 |

No cheap signal comes close to isolating one sentence among about 1,800.

## What would
The paper's best result is "block-based extraction": a model reads the
history once and writes down the user's facts. It reached 70.8% at 300
conversations with one evidence item, above reading the whole context
(63.5%) and far above the memory systems (25-45%). For Gnomon this is a
situation model written once, at write time, by a small model. Each item
would be a claim citing its source turn and kept current by supersession,
then loaded with every recall the way standing directives are.

**Cost:** about 300K input tokens of Haiku per ConvoMem-sized history,
once. Benchmarking it on 60 test histories is about 18M tokens. That is
deferred by the owner's choice along with the summary pass, since both are
the same write-time pass.
