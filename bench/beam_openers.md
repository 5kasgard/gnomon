# Openers: an index of how each conversation began

BEAM's event-ordering and summary questions ("list the order in which I
brought up aspects of my budget tracker") stay under 7% recall@10. Their
evidence is spread over many conversations: 5.8 messages in 3.7 at 100K,
12.9 in 7.7 at 500K. Only about 30% of it is in the top 200 by words
(beam_consolidate.md).

## Where the evidence sits (descriptive, looked at before fixing the method)

A BEAM conversation (session) is one of its "turns". For each question
there are two measures: the share of evidence messages that are the first
user message of their conversation, and the share of the conversations
holding evidence whose first user message is itself evidence.

| | Evidence messages that open a conversation | Evidence conversations opened by evidence |
|---|---|---|
| Event ordering, 100K (40 questions) | 81% | 100% |
| Event ordering, 500K (70) | 85% | 100% |
| Summaries, 100K (36) | 26% | 49% |
| Summaries, 500K (70) | 31% | 50% |

People raise a topic at the start of a conversation. An index of openers
(one line per conversation: when, a pointer, and the first 60 tokens of
the person's first message) holds most ordering evidence. Its size:
about 5.6K tokens at 100K and 20K at 500K, so it must be selected from at
larger sizes. This is a property of how BEAM was generated. Real
conversations often open with the task too, but that is not shown here.

## Protocol, fixed before running

- **Sizes:** 100K, 500K, 1M and 10M. Every question uses the features
  cached for beam_learn.py. Budget: 2,000 tokens for every arm.
- **A, the reference:** hybrid (words and meaning fused), top items in
  order until the budget is spent.
- **B, openers:** the index lines ranked by the fusion (RRF, k=60) of
  meaning (cosine with potion-retrieval-32M) and shared rare words (idf
  sum). The top lines that fit the budget are kept and shown in record
  order. A line counts its whole message as present.
- **C, both:** 1,000 tokens of B, then 1,000 of A.
- **Measured:** evidence recall (the share of a question's evidence
  messages in the view), with cluster bootstrap 95% intervals by
  conversation. Primary: event ordering and summaries, pooled over sizes,
  B against A. Also reported: every category, so any harm shows.
- **Not tuned:** the budget, the 60 tokens and the fusion are fixed here
  and run once.
