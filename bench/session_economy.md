# Session economy: push less without losing anything

dynamic_budget.md found that cutting each prompt's push by the
similarity of what was found loses evidence. Two other ways to spend
less come from the session, not the prompt:

- **Dedupe:** a line pushed earlier in the same session is still in the
  reader's context (until the context is compacted). Pushing it again
  adds tokens and nothing else. This is lossless by construction; the
  question is only how much it saves.
- **Gate:** many prompts have nothing in the record (a new topic, "thanks",
  a coding step). wild.md measured that 52% of real messages echo an
  earlier conversation, with the echo threshold set per memory: the 95th
  percentile of similarity between messages of different conversations.
  Pushing only when the prompt clears that threshold would skip the rest.

## Protocol, fixed before running

- **Real use:** WildChat-1M shard 0 (ODC-BY), the histories of wild.md
  (English, 10 to 200 conversations per person), the 100 longest. Memory
  for a conversation: every message of that person's earlier
  conversations. Prompts: every user message of conversations 6 to 25 of
  each history. Per prompt, the push is the shipped window view at 2,000
  tokens over a dense ranking (the static embedder) of the memory's
  messages: an approximation of the product's hybrid order. Aggregates
  only are kept.
  - **Dedupe saving:** the share of pushed tokens whose message was
    already pushed earlier in the same conversation.
  - **Gate skip rate:** the share of prompts whose best similarity to the
    memory is below the memory's threshold (95th percentile of similarity
    between user messages of different earlier conversations, 2,000
    random pairs).
- **Benchmarks (what the gate would lose):** BEAM 100K and 1M, LoCoMo,
  LongMemEval-S, ConvoMem, the questions of push_scale.md. The same
  threshold rule per record. Measured: the share of questions skipped,
  and the answer words in view lost (the 2,000-token view's answer words
  on skipped questions, over all questions).
- **Decision:**
  - Dedupe ships if it saves at least 10% of pushed tokens on WildChat,
    with the record of what was pushed reset when the context is
    compacted.
  - The gate ships if it skips at least 20% of WildChat prompts, and on
    every benchmark record skips at most 5% of questions and loses at
    most 2 points of answer words in view.
- **No model is called.** Run once.
