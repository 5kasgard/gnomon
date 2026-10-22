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

## Result (run once)

The share of out-of-pool evidence reached by 100 added messages
(`reach_results.json`), with each link against the control:

| | n | Deeper (control) | Time | Terms | Meaning of seeds | All three |
|---|---|---|---|---|---|---|
| BEAM 100K | 24 | 95.4 | 97.9 | 29.8 | 94.0 | 96.9 |
| BEAM 500K | 185 | 23.9 | 26.8 | 9.2 | 15.0 | 21.2 |
| BEAM 1M | 256 | 13.8 | **24.2** (+10.4 [4.7, 16.2]) | 10.6 | 10.8 | 18.5 (+4.7 [0.3, 9.1]) |
| BEAM 10M | 104 | 4.9 | **13.4** (+8.5 [−0.8, 19.4]) | 0.9 | 4.0 | 9.5 |
| LoCoMo | 151 | **58.4** | 48.2 | 33.9 | 45.6 | 53.9 |
| LongMemEval-S | 15 | 71.0 | 75.6 | 84.3 | **98.3** (+27.3 [10.6, 46.2]) | 90.3 |
| ConvoMem | 108 | 12.0 | 26.4 (+14.4 [5.1, 24.1]) | 23.6 (+11.6 [2.8, 20.8]) | 16.7 | **36.6** (+24.5 [14.4, 34.7]) |

What it shows:

- **Going deeper works while the record is small and fails as it grows.**
  On BEAM it reaches 95% at 100K, 24% at 500K, 14% at 1M and 5% at 10M.
  Past the first 400, similarity to the question is mostly noise in a
  large record.
- **Time is the link that holds at scale.** What was said near found
  evidence reaches 2.7 times as much at 10M and 1.8 times at 1M. This is
  locality of reference, the principle that memory hierarchies run on
  (Denning's working set): what is used together was written together.
  A record's order is a free index of what belongs together.
- **No link is best everywhere, again.**
  - LoCoMo's evidence is reached best by going deeper: two people talking,
    with no topic threads to follow.
  - LongMemEval's by the meaning of found evidence (only 15 questions).
  - ConvoMem's by all three fused (+24.5), its evidence conversations
    being mixed among filler.
- **Rare shared terms alone are a weak link,** except on ConvoMem.

This is the third time the same shape has appeared, after ranking and
views. So the mechanism for reach is also a set of links with a
per-memory choice. The fused arm is the safest default: never far
behind except on LoCoMo (−4.5 [−18.1, 7.5]), and best where links matter
most. Time alone is the strongest single link at the scale where reach
matters.
