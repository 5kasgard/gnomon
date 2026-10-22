# Depth: does a deeper view help the reader, or dilute it?

projections.md found that much of the missed evidence is found but ranked
just past what fits: median rank 30 to 63. A deeper view holds it. But
presence is not use: the situation model put evidence in view three times
as often, and the reader used it only a little more (situation_reader.md).
So the question is the reader's, not the view's.

## Protocol, fixed before running

- **Questions:** 40 per dataset, drawn with seed 20261022 from those with
  answer words. BEAM 1M, where depth is largest; LongMemEval-S; LoCoMo.
- **Views:** the shipped one (length-aware windows over the hybrid order),
  at three budgets: **2K** (shipped), **4K** and **8K** tokens.
- **Reader:** Haiku, one agent per dataset and budget. It answers each
  question from its view only, in one short sentence.
- **Primary measure:** answer words in the answer. That is the share of the
  gold answer's content words in the reader's answer, judge-free, as in
  views.md. Paired per question against 2K, with bootstrap intervals.
- **Also:** token F1, answer words in view (presence), and the ratio of use
  to presence.
- **Prediction:** none fixed. Both dilution (deeper is worse) and depth
  (deeper is better) are plausible, and the run decides.

## Result (run once)

Haiku read 40 questions per dataset at each budget, about 4M Haiku
tokens in all. The 8K views were read in chunks of 10 questions, because
one reader cannot hold 40 of them. Results are in `depth_results.json`.

| | Budget | Answer words in answer | F1 | Answer words in view | Use ÷ presence |
|---|---|---|---|---|---|
| BEAM 1M | 2K | 27.2 | 33.3 | 48.8 | 0.56 |
| | 4K | 31.7 (+4.5 [−1.1, 11.8]) | 28.7 | 58.1 | 0.55 |
| | 8K | 27.4 (+0.1 [−7.1, 7.3]) | 34.1 | 68.3 | **0.40** |
| LongMemEval-S | 2K | 61.4 | 40.4 | 69.1 | 0.89 |
| | 4K | 68.1 (+6.8 [−0.6, 15.4]) | 37.1 | 76.6 | 0.89 |
| | 8K | 66.7 (+5.3 [−0.5, 13.0]) | 30.1 | 80.9 | 0.82 |
| LoCoMo | 2K | 49.5 | 39.1 | 57.9 | 0.86 |
| | 4K | 51.9 (+2.4 [−3.3, 9.4]) | 39.0 | 61.4 | 0.85 |
| | 8K | 52.6 (+3.1 [−1.2, 7.5]) | 29.7 | 69.1 | 0.76 |

Pooled over the 120 questions, against 2K:

| | Answer words in answer | F1 | Mean answer length |
|---|---|---|---|
| 4K | **+4.5 [+0.7, +8.9]** | −2.6 [−5.6, −0.0] | 18.0 words (2K: 15.8) |
| 8K | +2.8 [−0.6, +6.6] | **−6.3 [−9.4, −3.4]** | 19.7 words |

What it shows:

- **Presence rises with depth; use rises far less.** From 2K to 8K the
  answer words in view rise 11 to 20 points, and in the answer 0 to 5.
  The share of what is in view that the reader uses falls at 8K on
  every dataset (0.56 → 0.40 on BEAM). This is dilution, the same as
  the situation model showed: more in front of the reader is not more
  used.
- **4K is a modest gain in recall at a cost in precision.** Answers
  carry more of the gold answer's words, but they grow longer and less
  exact (F1 −2.6). 8K adds nothing to recall over 4K, and costs more
  precision (F1 −6.3).
- **So depth is not solved by a bigger view.** The evidence at ranks 30
  to 60 is reached, and then lost in the reading. The lever is the
  reader's selection. The select-then-answer instruction helped a little
  in situation_cues.md. The default stays at 2,000 tokens, and `budget`
  remains the knob for a caller who wants recall over precision.

## Next, fixed before running: select, then answer

The same 120 questions and the same 4K views, with each excerpt numbered.
The reader (Haiku, the same prompt otherwise) first lists the numbers of
up to 5 excerpts that bear on the question, then answers from them. This
is the B2 instruction that helped a little in situation_cues.md.

Compared, paired and pooled over the 120, with the answers already
recorded for plain 4K and plain 2K:

- answer words in the answer;
- F1;
- answer length.

Prediction: selection recovers the precision that 4K lost (F1 at least
2K's) and keeps 4K's recall.
