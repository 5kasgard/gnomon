# BEAM: a meaning layer next to word ranking

This file was committed before any held-out run (100K, 1M, 10M). The
choices below were fixed on 500K and will not change after those runs.

## Fixed choices
- **Model:** minishlab/potion-retrieval-32M. It is a static embedding table
  (model2vec), 512 dimensions, encoding about 45,000 messages a second on
  two CPUs. No transformer runs at query time.
- **Unit:** whole messages, first 4,000 characters.
- **Fusion:** reciprocal rank fusion, 1/(60 + rank), with the standard
  constant. The top 200 from each side.
- **Binary variant:** the dense side is searched by the signs of the
  embedding (1 bit per dimension), and its top 100 are rescored with the
  full vectors.
- **Directives:** loaded in every arm, as in `beam.py --consolidate`.
- **Not tried, so as not to tune on dev:** fusion weights, other models, and
  embedding sentences instead of messages.

## Development result (500K, 629 questions, recall@10)

| | Words | Dense | Hybrid | Hybrid, binary |
|---|---|---|---|---|
| All | 52.1 [49.3, 54.9] | 53.7 [50.3, 57.1] | **57.5** [54.5, 60.7] | 57.4 [54.5, 60.4] |
| Contradictions | 72.0 | 73.5 | **82.1** | 82.1 |
| Knowledge updates | 63.9 | 71.1 | **76.3** | 75.6 |
| Multi-session | 46.9 | **53.2** | 52.8 | 52.1 |
| Temporal | 68.8 | 72.1 | 74.5 | **77.1** |
| Information extraction | 50.0 | 42.9 | **56.4** | 55.4 |
| Ordering | 3.9 | 5.1 | 5.2 | 5.0 |
| Summaries | 9.0 | 7.9 | **11.5** | 9.7 |
| Median tokens of top 10 | 5,328 | 1,987 | 4,069 | 3,805 |

Hybrid is ahead on recall and uses fewer tokens: the dense side prefers
shorter messages. Ordering and summaries stay low under every arm.

## Held-out results (one run, after the above was committed)

`beam_dense_results.json`. Recall@10 with directives in every arm; 95%
intervals by conversation:

| Size | Words | Dense | Hybrid | Hybrid, binary | Median tokens (words / hybrid) |
|---|---|---|---|---|---|
| 100K | 59.0 | 59.0 | **63.4** [60.3, 66.5] | 62.7 | 5,363 / 4,519 |
| 1M | 40.2 [38.3, 42.4] | 43.7 | **46.4** [44.5, 48.4] | 45.8 | 6,045 / 3,893 |
| 10M | 24.3 [20.7, 27.8] | 26.9 | **33.0** [29.5, 36.5] | 30.2 | 5,308 / 3,589 |

At every size, hybrid recalls more of the evidence than words alone
while sending 15-36% fewer tokens. By category at 1M / 10M, it moved:
- multi-session reasoning: 20.8 -> 32.7 / 11.5 -> 19.2;
- knowledge updates: 56.9 -> 65.4 / 41.6 -> 64.8;
- information extraction: 32.6 -> 42.5 / 36.0 -> 61.0;
- temporal: 53.6 -> 63.5 / 14.6 -> 24.9.

Ordering and summaries stayed under 6% at 1M and 10M.

**Binary search loses ground at 10M** (30.2 against 33.0). A conversation
there has hundreds of thousands of messages, and 100 candidates by bit
distance is too few to rescore. The rescoring depth should probably
grow with the size of the record. That is a candidate for the next
version, not a change made after seeing these numbers.
