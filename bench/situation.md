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
