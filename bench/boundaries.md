# Boundaries: where information enters a record

## The pattern across results so far

Several results that looked separate point the same way:

- **Surprise** (how unlike everything in the previous 2,000 turns a turn
  is) is one of the eight signals whose weight kept its sign on every
  dataset (multi_invariant.md).
- **Openers.** How a conversation began held 21.5% of BEAM's
  event-ordering evidence at 2,000 tokens, against 3.5% for whole
  messages (beam_openers.md). Every conversation holding that evidence
  opens with it.
- **Exact text beats rewriting.** Selected sentences beat whole messages
  (beam_compress.md). A condensed profile lost to the raw situation
  sentences (situation_cues.md).

In psychology, event segmentation theory (Zacks and colleagues) says
people cut experience into events where prediction fails. Memory is
organised around those boundaries, and what is at a boundary is
remembered best. Neural data show the same: hippocampal activity peaks
at boundaries (Baldassano et al. 2017, Ben-Yakov and Henson 2018).

Mapped onto Gnomon:

- A conversation's start is a boundary the metadata happens to mark.
- Surprise is the prediction error that would mark boundaries the
  metadata does not.

If so, the openers' gain is not a quirk of BEAM's sessions. It is one
case of indexing a record at its boundaries, and boundaries found by
surprise alone should carry it to records with other structures.

There is a duller explanation that has to be ruled out first. An opener
line is at most 60 tokens, while the comparison arm held whole messages,
so openers may win only because short lines fit more per token. The
openers test had no control for that. This one does.

## Protocol, fixed before running

- **Records:** BEAM (100K, 500K, 1M, 10M), LoCoMo, LongMemEval-S and
  ConvoMem: every question in the features cached for multi.md and
  beam_learn.py, aligned and checked against the loaders.
- **Budget:** 2,000 tokens. Every index line is a person's message cut to
  60 tokens, with a pointer. Lines are ranked by the same fusion as
  openers (RRF of meaning, from potion-retrieval-32M cosine, and shared
  rare words, an idf sum over that index's lines). The top lines that fit
  are kept. A line counts its whole message as present, in every index
  arm alike.
- **Arms:**
  - **A, hybrid:** whole messages, words and meaning fused (the
    reference).
  - **F, all lines:** every message by the person, cut to 60 tokens
    (the density control).
  - **B, openers:** the first message by the person in each
    conversation, as the data marks conversations.
  - **D, surprise boundaries:** messages by the person whose local
    shift is in the top 10% of that record's messages by the person.
    Local shift is 1 − cosine to the mean of the previous 5 messages of
    any speaker; the first message counts as 1. D uses no metadata.
- **Measured:** evidence recall, per dataset and per BEAM size, with
  cluster bootstrap 95% intervals by conversation (by question for
  LongMemEval-S and ConvoMem, one question set per record).
- **Questions, decided before seeing results:**
  1. **Is it structure or density?** B against F on BEAM event ordering.
     If F matches B, openers win only by density.
  2. **Does a boundary index help beyond density elsewhere?** D against F,
     and B against F, on every dataset.
  3. **Does surprise find the boundaries the metadata marks?** The share
     of openers among D's lines, against the 10% expected by chance.
- **Not tuned:** 60 tokens, 2,000 tokens, the window of 5 and the top 10%
  are fixed here, and everything is run once.

## Result (run once): the hypothesis fails; density explains the openers

Evidence recall at 2,000 tokens (`boundaries_results.json`):

| | A, whole messages (hybrid) | F, every person's line | B, openers | D, surprise boundaries |
|---|---|---|---|---|
| BEAM 100K | 40.0 | 66.1 | 64.4 | 10.5 |
| BEAM 500K | 40.0 | 63.5 | 62.4 | 12.6 |
| BEAM 1M | 28.5 | 52.1 | 51.7 | 8.4 |
| BEAM 10M | 25.4 | 40.8 | 41.0 | 10.0 |
| LoCoMo | **78.0** | 40.2 | 10.3 | 5.5 |
| LongMemEval-S | 74.8 | **77.0** | 45.9 | 15.6 |
| ConvoMem | **81.8** | 56.8 | 0.0 | 1.8 |

1. **Structure or density? Density.** On BEAM event ordering, openers
   score 21.4 and every person's line scores 20.7 (B − F +0.7 [−0.1,
   1.6]). Openers won in beam_openers.md because 60-token lines fit
   more per token than whole messages. Their position in the
   conversation added nothing. That claim in beam_openers.md is
   corrected here.
2. **A boundary index beyond density? No.** Surprise boundaries lose to
   every person's line everywhere, by 31 to 62 points. Openers lose
   badly outside BEAM: ConvoMem's conversations open with filler, so its
   openers hold no evidence.
3. **Does surprise find the marked boundaries?** On LongMemEval it does:
   85% of D's lines are openers, against a 19.5% base rate. On BEAM
   slightly (86% against 77%), on LoCoMo and ConvoMem not at all. Surprise
   does mark where conversations change, where they do change sharply.
   But a boundary is not where the evidence is.

**What the numbers show instead.** The unit of what the reader is given
decides as much as the ranking does:

- **Where messages are long** (BEAM, where assistant replies run to
  hundreds of tokens), short lines beat whole messages by 15 to 26
  points.
- **Where messages are short** (ConvoMem, LoCoMo), whole messages win,
  and the person-only index misses the evidence other speakers hold.
  LoCoMo has two people; ConvoMem has assistant facts.

A budget is a knapsack: each item has a value (relevance) and a cost
(its tokens). Hybrid retrieval ranks by value alone.

## Next, fixed before running: separate ranking from cost

- **H, hybrid headlines:** hybrid's order (words and meaning over whole
  messages, of every speaker), each message shown as its first 60
  tokens with a pointer.
- **G, every line:** every message of every speaker cut to 60 tokens,
  ranked by the line fusion as F.
- **A and F** again, for reference.
- **Two measures,** because a cut line may not hold the fact:
  - **lenient:** the evidence message is pointed to;
  - **strict:** its whole text is in the view, meaning the message was
    at most 60 tokens, or the arm shows whole messages.

  Strict is a lower bound on what a reader holds without `expand`.
- **Prediction:** H beats A on lenient recall everywhere, because the
  same ranking fits more items. H against A on strict recall shows what
  cutting costs.
