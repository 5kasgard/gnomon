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
