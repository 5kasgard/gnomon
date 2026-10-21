# A situation model Gnomon can ship

The tests so far (situation.md, situation_reader.md, situation_cues.md)
settle the shape: keep the user's own sentences about their situation,
exactly as said, with pointers to the record; let the reader select among
them; do not summarise. The selector that kept the most evidence for free
was a logistic regression on static embeddings, trained on Haiku's
labels of one ConvoMem history (81.7% of evidence kept).

Those weights cannot ship. ConvoMem is CC BY-NC 4.0, and a classifier fitted
to labels of its text is plausibly a derivative. Without learnt weights the
choice is poor. On the 4 test histories (202 questions), at the same share:

| Selector | Evidence kept | Mean tokens per history |
|---|---|---|
| Every first-person, non-request sentence | 96.5% | about 33K x 4 = 132K |
| Hand-written word flags, top 25% | 58.4% | 9.4K x 4 |
| Learnt on ConvoMem labels, top 25% | 81.7% | 12.1K x 4 |
| Random 25% | 46.0% | 8.4K x 4 |

(Token columns are the sum across the 4 histories divided as shown.)

The learnt selector needs few labels: trained on 1,000 of the 4,831
labelled sentences it keeps 78.2-78.7% (3 seeds), on 2,000 77.7-79.2%.

## Protocol, fixed before labelling

- **Training data:** LongMemEval-S cleaned (MIT). Every first-person,
  non-request user sentence (the same prefilter), deduplicated; 2,000
  sampled with seed 20261022.
- **Labels:** Haiku, with the same prompt as before (situation.py, PROMPT),
  in 2 chunks of 1,000.
- **Selector:** the same features and training as situation_distil.py. The
  share kept is the share Haiku kept in the LongMemEval sample.
- **Test:** the same 4 ConvoMem test histories and 202 questions; evidence
  kept. Also reported: the same weights at ConvoMem's dev share, and the
  ConvoMem-trained selector for reference.
- **Decision:** ship the LongMemEval-trained weights as Gnomon's default
  selector if they keep at least 75% of the evidence. Otherwise ship no
  default; the user (or a model they choose) labels a sample of their own
  history once.

The transfer is a real test: LongMemEval is a different dataset, built
differently, and its labels never see ConvoMem.
