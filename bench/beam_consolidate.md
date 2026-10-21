# BEAM: standing directives — what was fixed before the held-out runs

Written 2026-10-21, after tuning on BEAM 500K (the development size) and
before running 100K, 1M or 10M with `--consolidate`. Committed before those
runs so the order is in git.

## The mechanism (`python/gnomon/consolidate.py`)
- From every user message, keep the sentences that start "Always", "Never",
  "Please always", "Don't ever" / "Do not ever", or contain "I prefer",
  "I prioritise/prioritize(d)", "I favour/favor", "I would rather / I'd
  rather", "I've been told/instructed to always".
- Load them with every question, in record order. Budget 1,500 tokens
  (4 characters each); past it, keep those sharing most words with the
  question, then the most recent.
- Their tokens count in the question's budget, so the last-N-tokens
  window it is compared with gets the same number of tokens.

Nothing in the pattern list or the budget will change after the held-out
runs. If they show a fault, it is reported, and any fix is a new version
measured on a size not yet used.

## Development result (500K, 35 conversations, recall@10)
| | without | with |
|---|---|---|
| all questions | 38.7 [35.9, 41.5] | 51.5 [48.5, 54.3] |
| instruction_following | 17.4 | 90.5 |
| preference_following | 20.2 | 63.6 |
| median tokens | 5,348 | 6,243 |

## Tried and dropped (500K, first 12 conversations)
Coverage mode for order and summary questions — the best hit from each
episode first, then by rank, in record order. Event ordering 2.4 -> 2.4,
summaries 6.3 -> 3.1 (user turns only: 6.8 -> 6.5, 10.0 -> 5.9). The
evidence is not in the candidates to begin with: only about 30% of it is
in the top 200 by words. Reordering cannot fix that; dated summaries
written once (an offline model pass) or a semantic index might, and are the
next things to measure.
