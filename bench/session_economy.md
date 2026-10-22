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

## Result (run once): dedupe ships; the gate does not

From `session_economy_results.json`:

**Real use (WildChat, 100 histories, 4,827 prompts):**
- **Dedupe** saves **48.9%** of pushed tokens (median history 34.7%).
  Within a conversation, prompts keep finding the same earlier messages.
- **The gate** would skip 46.0% of prompts (median history 51.2%).

**What the gate would lose on memory questions:**

| Record | Questions | Skipped | Answer words in view lost (of) |
|---|---|---|---|
| BEAM 100K | 275 | **31.3%** | **17.7** (59.2) |
| BEAM 1M | 485 | **8.5%** | **3.8** (55.0) |
| LoCoMo | 1,480 | 4.7% | **3.1** (60.3) |
| LongMemEval-S | 461 | 4.1% | **3.0** (65.2) |
| ConvoMem | 1,058 | 0.0% | 0.0 (66.4) |

- **Dedupe meets its rule and ships.** `--per-message` now leaves out any
  span (pointer and character range) already pushed in the same session.
  The record of what was pushed is reset when the context is compacted,
  since the model may no longer hold it. It is lossless while the context
  holds; on real histories it about halves the per-prompt cost.
- **The gate fails its rule.** It skips too many real memory questions:
  a question about the record can share little meaning with any one
  message, while its words still find the evidence. BEAM 100K, a
  single-topic project history, is where the per-memory threshold sits
  highest, and loses most.
- **So the prompt cannot be judged by meaning alone.** That is the same
  lesson as dynamic_budget.md: the words find what the meaning misses.

## Caveat

The WildChat push used a dense ranking only, as an approximation of the
product's hybrid order. The saving depends on how often a conversation's
prompts return to the same earlier messages, which the hybrid order is
unlikely to change much, but it was not measured with the product
itself.
