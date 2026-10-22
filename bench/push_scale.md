# Push at scale: what reaches a reader that does not pull

The live tests (live.md) found that what is pushed into the reader's
context gets applied and what is merely offered mostly does not (Gemma
called memory tools 1 time in 12; Claude pulled, but still applied pushed
facts more). The shipped push is the situation model at session start,
1,500 tokens of the person's own most recent sentences.

The live tests ran on short histories. A real memory grows, and a
diagnosis on ConvoMem (16 histories, 46K to 146K tokens of candidate
sentences each; exploratory, not a result) found the session-start push
holds the evidence for 0 to 3.5% of questions, no better than a random
1,500 tokens (1.0 to 7.4%). Nothing chosen without the question can hold
a large history in 1,500 tokens.

The question-dependent push already exists: `--per-message` adds
`context` for each prompt (UserPromptSubmit), and it is off by default
because it costs tokens on every prompt. This test measures both, and
how small the per-message push can be.

## Protocol, fixed before running

- **Records:** BEAM 100K, 1M and 10M; LoCoMo; LongMemEval-S; ConvoMem
  (the cached questions of views.md).
- **Session start:** the situation as shipped (every first-person,
  non-request user sentence; the most recent that fit 1,500 tokens).
  Measured: the share of questions with any evidence message among the
  pushed sentences.
- **Per message:** the shipped view (length-aware windows over the hybrid
  order) at **500, 1,000 and 2,000** tokens. Measured: answer words in
  view (primary) and evidence recall.
- **Decision:**
  - If per message at 2,000 holds the evidence for at least 5 times as
    many questions as the session-start push, on every record, then auto
    mode turns `--per-message` on once the memory's situation no longer
    fits the session-start budget. Small memories are unchanged.
  - Its default budget is the smallest of the three whose answer words in
    view are within 5 points of 2,000's on every record.
- **No model is called.** Run once.
