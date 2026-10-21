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
| Every first-person, non-request sentence | 96.5% | 132.6K |
| Hand-written word flags, top 25% | 58.4% | 37.6K |
| Learnt on ConvoMem labels, top 25% | 81.7% | 48.4K |
| Random 25% | 46.0% | 33.5K |

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

## Result: the weights do not transfer

Haiku kept 347 of the 2,000 LongMemEval sentences (17.3%). About 282K
Haiku tokens in all, in 2 agents.

| Selector, on the 4 ConvoMem test histories (202 questions) | Evidence kept | Mean tokens per history |
|---|---|---|
| LongMemEval-trained, its own share, 17.3% (primary) | **49.0%** | 29.6K |
| LongMemEval-trained, ConvoMem's share, 25.3% | 60.9% | 41.8K |
| ConvoMem-trained, 25.3% (reference) | 81.7% | 48.4K |

49.0% is below the 75% bar, so by the rule fixed above no weights ship.
What matters about a person's situation depends on the history, as which
ranking mechanism works best did (compartments.md). The answer is the
same: learn it per memory.

## What shipped

`gnomon.situation` and the MCP tool `situation`:

- Until a memory has learnt its selector, the situation model is every
  candidate sentence (96.5% of evidence; on these histories about 130K
  tokens, so `budget` keeps the most recent).
- Learning, once per memory: `situation` with mode "sample" returns about
  1,000 numbered candidate sentences and the labelling prompt. The
  client's model (Claude, in a session) picks the lines; mode "learn"
  fits the weights and saves them beside the log. Gnomon itself calls no
  model. From the subsampling above, 1,000 labels keep about 78% of the
  evidence at a quarter of the size. Those labels came from another
  history of the same dataset; labels of the person's own history should
  fit at least as well, but that is untested.
- The model is the person's exact sentences with pointers, oldest first,
  with the select-then-answer instruction (B2).

Checked end to end: `gnomon.situation`, given weights learnt with its own
`train` from the ConvoMem dev labels, keeps the evidence for 81.7% of the
202 test questions, and its candidates for 96.5%: the same as the bench,
to the question.
