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

## Result (one run; `situation_reader_results.json`)

123 questions, 2 histories. About 730K Haiku tokens for the readers and
178K for the judge.

| | Top 10 alone (A) | Top 10 + situation model (B) |
|---|---|---|
| Answer uses the evidence (2+ distinctive words, primary) | 30.1% | **38.2%** |
| Same with 1+ word (sensitivity) | 57.7% | 60.2% |
| Paired by question | | B better on 20, worse on 10 |
| Blinded judge against the rubric | better on 25 | better on 33 (tie 65) |

The judge picked position 1 27 times and position 2 31 times, so order
bias is not driving the result. The direction is the same on both
measures, and modest. The judge margin (33 against 25) is not
significant on a sign test.

**Where it breaks:**
- **When the evidence was in the situation model** (103 questions), B used
  it 39.8% of the time, against 33.0% for A. Presence is not application.
  The reader has to find the one line that applies among about 1,900
  (48K tokens), for each of about 60 questions.
- **A smaller slice chosen by similarity to the question does not help:**
  the evidence is in the 200 most similar situation sentences for 41% of
  questions. Applicability is not similarity.
- **A smaller model chosen by the classifier loses the evidence:** it is
  kept for 81.7% of questions at 25% of sentences, 53.0% at 10%, 35.6% at
  5%, 15.8% at 2% and 9.9% at 1%.

**What this says.** Evidence presence (21.8% → about 80%) is necessary but
not sufficient. The next lever is precision. The aim is a situation model
of tens of standing circumstances, like the about 60 each history plants,
not thousands of sentences. Options, roughly cheapest first:
- a stricter second selection pass (a model, once per history);
- merging and superseding near-duplicate statements;
- a stronger reader.

Each needs a measurement before it is believed.
