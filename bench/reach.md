# Reach: what links a question to evidence it cannot find directly

projections.md split the unreachable evidence into two layers:

- **depth:** found, but ranked past what fits;
- **reach:** not among the 400 candidates that words and meaning give.

Reach grows with the record: 2% of evidence at BEAM 100K, 22% at 1M and
42% at 10M. Evidence that shares neither words nor meaning with the
question can only be reached through something else. Each kind of link
has its own mathematics:

- **Time:** what was said near found evidence. Neighbours in the record.
- **Shared rare terms:** what mentions the same names, numbers or
  entities as found evidence. A bipartite graph of messages and terms; one
  step of spreading activation.
- **Meaning of found evidence:** what resembles the found messages rather
  than the question. Pseudo-relevance feedback (Rocchio).

The plain alternative is to go deeper in the same ranking. It is the
control.

## Protocol, fixed before running

- **Records:** BEAM 100K, 500K, 1M and 10M, LoCoMo, LongMemEval-S and
  ConvoMem: every question with evidence outside its candidate pool, from
  the cached features.
- **Seeds:** the hybrid top 10, the same for every arm.
- **Arms,** each adding exactly M = 100 messages from outside the pool:
  - **deeper (control):** the next 100 by meaning similarity to the
    question.
  - **time:** messages within ±w of a seed in record order, w growing
    1, 2, ... until 100 are added; ties nearest first.
  - **terms:** messages sharing the seeds' rarest terms. A term is
    weighted by its idf when it appears in a seed and not in the
    question. A message scores the sum over the terms it shares; the
    top 100 are added.
  - **meaning of seeds:** the 100 most similar to the mean of the seeds'
    embeddings.
  - **all three:** reciprocal-rank fusion of time, terms and meaning of
    seeds, top 100.
- **Measured:** the share of out-of-pool evidence that the added messages
  reach, per environment. Paired per question against the control, with
  cluster bootstrap 95% intervals (by conversation for BEAM and LoCoMo,
  by question otherwise).
- **Not tuned:** M = 100, the top-10 seeds and the arm definitions are
  fixed here, and everything is run once.
