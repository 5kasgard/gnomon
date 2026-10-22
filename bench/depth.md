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
