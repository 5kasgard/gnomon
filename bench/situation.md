# A write-time situation model for implicit connections

Fixed 2026-10-22, before any test-batch result. Committed before the run.

## Development (ConvoMem implicit-connection `batched_046`, not the test batch)
- **Prefilter (plain code):** the user's first-person sentences that are not
  requests. This keeps 92.8% of the evidence at about 74K tokens per
  history, against 154K tokens for every user sentence.
- **v1, one Haiku reader per history** (`PROMPT_V1`), 3 histories, 189
  questions. It kept 7-10% of the candidates (7-10K tokens).
  - Evidence among the kept facts: 43.9%.
  - Ranking within them by meaning fails: the evidence reaches the top 3
    for 7% of questions. So the situation model is loaded whole, not
    searched.
  - Loaded whole next to the hybrid top 10: 56.6% of questions have
    evidence present, against 22.2% for the hybrid top 10 alone.
- **v2 (`PROMPT`), four readers per history,** each reading one quarter in
  a single pass, with a prompt that leans towards inclusion. On one
  history (60 questions) it kept 82% of the evidence, against 40% for v1,
  in 26.6K tokens against 7.3K. It costs about 490K Haiku tokens per
  history against about 245K.
- **A scoring fault was found and fixed during development:** loading "1
  case" draws a different case than the first of "3 cases".

## Test protocol
- 4 histories from `batched_047`: `load_convomem(per_file=4,
  seed=20261021)`, every question per history.
- v2 exactly as written, 4 readers per history, with Claude Haiku (the
  sub-agent model).
- Reported per question:
  - evidence present in the hybrid top 10;
  - in the situation model loaded whole;
  - in both;
  - the situation model's size in tokens.
- Also reported: Haiku tokens spent building it.
- Results are evidence presence, not answer accuracy. A reader run is the
  next step if this holds.

## Test result (one run; `situation_results.json`)

ConvoMem implicit connections, `batched_047`: 4 histories of about 300
conversations, every question, 202 in all. 95% intervals by history:

| Evidence present for the question | Recall |
|---|---|
| Hybrid top 10 (what recall alone gives) | 21.8 [18.7, 26.6] |
| Situation model, loaded whole | **75.7** [70.4, 79.3] |
| **Both** | **81.7** [75.3, 86.7] |

**Situation model sizes:** 1,484-1,965 kept of about 7,500 candidate
sentences, 32.7-40.6K tokens. That is 4-5% of each history's text, small
enough to hold in a prompt-cached prefix. At a tenth of the input price
on a cache hit, that is about 3.5K token-equivalents per question.

**Cost to build, once per history:** about 615K Haiku tokens through
sub-agents. Most of that is sub-agent overhead: each reader's context is
re-read as it works. A direct API call with a cached instruction would
read each history's about 131K candidate tokens once, plus a short list
of numbers out.

**What it is not:**
- It is evidence presence, not answer accuracy. A reader has to apply the
  situation to the question.
- ConvoMem's paper reports answer accuracy of 63-82% for full context and
  70.8% for its best extraction method on these cases. That is not the
  same measure.
- 4 histories, one run.

**What it means for the design.** Implicit connections cannot be ranked
into the top 10 by any cheap signal measured. A small model reading each
history once, selecting the user's own sentences about their situation
without rewriting them, and loading that selection whole, puts the
evidence in front of the reader for about four questions in five.
