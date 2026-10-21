# Reader test: does the situation model change answers?

Fixed 2026-10-22, before any answer was produced. Committed before the
readers ran. Harness: `situation_reader.py`.

**Questions:** ConvoMem implicit-connection test histories 0 and 1
(`batched_047`, the same as `situation.md`). Every question with
evidence: 123.

**Conditions.** The same reader, Claude Haiku as a sub-agent, one per
history and condition:
- **A:** Gnomon's top 10 messages for the question (hybrid).
- **B:** the same top 10, plus that history's distilled situation model
  (`situation_distil.py`, the development share; about 48K tokens),
  given once per history.

**Reader prompt (both conditions):** answer each question as a helpful
assistant to this user would, in two to four sentences, taking into
account anything in the memory that bears on it. Don't mention the memory
itself. Write one JSON line per question.

**Primary measure (judge-free):** the answer uses the evidence when it
contains at least two of the evidence's distinctive words. Those are
content words of the evidence messages that are:
- not in the question;
- not stop words;
- in fewer than 1% of the history's candidate sentences.

One or more is reported as a sensitivity check. Paired by question.

**Secondary measure (blinded judge):** a separate Haiku sub-agent sees the
question, ConvoMem's rubric answer and the two answers as "1" and "2". The
order is random with seed 20261022, and the key is kept outside the
judge's folder. It answers "1", "2" or "tie": which follows the rubric
better. How often it picks each position is reported as a check on order
bias.

**Limits recorded in advance:**
- One reader model, one run, 2 histories.
- The judge is from the same model family as the reader.
- Condition B also gives the reader more text overall.
