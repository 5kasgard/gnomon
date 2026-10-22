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

## Result (run once)

Evidence recall at 2,000 tokens; results in `beam_openers_results.json`.

| | A, hybrid | B, openers | C, both |
|---|---|---|---|
| **Ordering and summaries, all sizes (388 questions, primary)** | 4.4 [3.4, 5.7] | **16.0 [14.0, 18.2]** | 11.2 [9.4, 13.1] |
| B − A, paired | | +11.6 [9.7, 13.6] | |
| Event ordering, 100K / 500K / 1M / 10M | 6.6 / 3.1 / 2.5 / 2.1 | 43.8 / 20.3 / 14.1 / 6.7 | 32.6 / 11.3 / 9.4 / 5.2 |
| Summaries, 100K / 500K / 1M / 10M | 4.6 / 8.4 / 3.5 / 2.3 | 11.4 / 12.0 / 7.7 / 9.4 | 7.4 / 10.3 / 6.3 / 6.1 |
| Information extraction | 43.9 | 64.3 | 62.9 |
| Multi-session reasoning | 29.8 | 60.3 | 57.2 |
| Knowledge update | 62.5 | 84.6 | 84.3 |
| Temporal reasoning | 62.6 | 83.8 | 81.1 |
| Instructions | 11.7 | 42.7 | 32.1 |
| Preferences | 27.3 | 59.9 | 49.0 |

Event ordering rises about sixfold (3.5 → 21.5 pooled). Summaries roughly
double. The gain falls with size, as the index outgrows the budget:
6.7% at 10M.

**Read this carefully.** B gives pointers, not text. An opener line is at
most 60 tokens, and it counts its whole message as present. A holds
whole messages, so at 2,000 tokens it holds only a few, mostly long
assistant replies. Two things follow:

- For ordering, the opener is the useful part: what the person raised,
  when.
- For facts, the answer may lie past the first 60 tokens and need
  `expand`. B's lead on fact categories measures how well it points to
  the right conversation, not answer text in hand.

What it shows is that navigating by how conversations began reaches far
more of the right conversations per token than retrieving whole
messages.

## In the product

`Tiers.openers` and `context` with view `"openers"`. Each recorded
conversation (episode) contributes its first turn by the person. The
product scores shared words with the record's sentence frequencies, not
the openers' own. Rebuilt with one episode per BEAM conversation, it holds
slightly more than the bench arm:

| | Bench B | Product |
|---|---|---|
| Event ordering, 100K / 500K | 43.8 / 20.3 | 46.1 / 22.0 |
| Summaries, 100K / 500K | 11.4 / 12.0 | 12.5 / 13.2 |

## Correction (boundaries.md)

A control this test lacked changes the reading. The control is every
message by the person, cut to the same 60 tokens and ranked the same way.
On BEAM event ordering it scores 20.7 against the openers' 21.4
(difference +0.7 [−0.1, 1.6]). The gain came from showing short lines
instead of whole messages, not from where in a conversation they sit.
Outside BEAM, openers fail badly (LoCoMo 10.3, ConvoMem 0.0).
