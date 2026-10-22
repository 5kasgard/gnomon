# Projections: where the difficulty sits

Abstraction in a complex system often moves a difficulty to another
layer rather than removing it. This project has done that twice:

- "which mechanism" became "calibrate per memory";
- "calibrate per memory" became "which signal tells it".

Before designing that signal, this analysis lays every measured
projection side by side and accounts for the remaining gap. It is
descriptive: nothing is chosen from it. It needs no new data and no
model.

## Families and environments

- **Ranking:** words, meaning, hybrid, own words first, invariant.
  Recall@10 with directives, per question, from the cached features.
  Environments: BEAM 100K, 500K, 1M and 10M, LoCoMo, LongMemEval-S and
  ConvoMem.
- **Views:** whole, keyword window, length-aware window, sentences.
  Answer words in a 2,000-token view, per question (views.md).
  Environments: the same 7, plus QASPER.

## The accounting, per family and environment

Four levels of choice, each a ceiling for the one above it:

1. **One fixed arm** for every environment, the best on average.
2. **The best arm per environment.** This is the ceiling for per-memory
   calibration.
3. **The best arm per question.** This is the ceiling for any per-question
   choice, given these arms.
4. **Perfect:** 100.

The gaps between them are:

- **environment-level:** 2 − 1;
- **question-level:** 3 − 2;
- **unreachable:** 4 − 3, which no choice among these arms can close.

## Two further projections

- **Difficulty against mechanism.** Each question's arm scores split
  into a common level (how hard the question is for every arm) and the
  contrasts between arms. Reported: the share of variance in each part,
  and how many dimensions the contrasts span, from an SVD of the
  question × arm contrast matrix.
- **Agreement as a free signal.** For ranking, how much the arms' top 10s
  overlap is known at query time, with no feedback. Reported: whether low
  agreement marks the questions where the per-question choice gains over
  the environment's best arm (AUC), and where the question is hard.

## Result

From `projections_results.json`. Ranking is recall@10. Views are answer
words in 2,000 tokens. "Difficulty" is the share of variance that is
common to all arms.

| Ranking | Fixed (invariant) | Per env | Per question | Env gap | Question gap | Unreachable | Difficulty | Agreement AUC: gain / hard |
|---|---|---|---|---|---|---|---|---|
| BEAM 100K | 65.6 | 65.8 | 71.1 | 0.2 | 5.3 | 28.9 | 88% | 0.62 / 0.69 |
| BEAM 500K | 62.7 | 62.7 | 66.3 | 0.0 | 3.6 | 33.7 | 86% | 0.57 / 0.69 |
| BEAM 1M | 50.1 | 50.3 | 54.6 | 0.1 | 4.3 | 45.4 | 87% | 0.59 / 0.72 |
| BEAM 10M | 36.0 | 36.1 | 39.5 | 0.1 | 3.4 | 60.5 | 84% | 0.61 / 0.70 |
| LoCoMo | 51.8 | 70.4 | 73.2 | **18.6** | 2.8 | 26.8 | 60% | 0.60 / 0.59 |
| LongMemEval-S | 83.6 | 83.6 | 88.2 | 0.0 | 4.6 | 11.8 | 73% | 0.68 / 0.69 |
| ConvoMem | 72.7 | 72.7 | 78.0 | 0.0 | 5.3 | 22.0 | 77% | 0.58 / 0.74 |

| Views | Fixed (K′) | Per env | Per question | Env gap | Question gap | Unreachable | Difficulty |
|---|---|---|---|---|---|---|---|
| BEAM 100K–10M | 51.7–59.2 | same | 56.5–64.7 | 0.0–0.3 | 3.5–5.5 | 35.3–43.5 | 79–87% |
| LoCoMo | 60.3 | 60.5 | 62.0 | 0.2 | 1.5 | 38.0 | 89% |
| LongMemEval-S | 65.2 | 65.8 | 72.9 | 0.6 | 7.1 | 27.1 | 85% |
| ConvoMem | 66.4 | 67.4 | 68.9 | 1.0 | 1.5 | 31.1 | 92% |
| QASPER | 74.9 | 80.7 | 87.5 | **5.8** | 6.8 | 12.5 | 65% |

**Where the difficulty sits:**

- **Mechanism choice is a small layer.** With the shipped defaults, the
  environment-level gap is about 0 except in two places: LoCoMo ranking
  (18.6) and QASPER views (5.8). The question-level gap is 1.5 to 7
  points. Per-memory calibration, and any signal for it, works only on
  this layer.
- **Most of the variance is difficulty, not mechanism.** 60 to 92% of it
  is a question being hard or easy for every arm alike. The arms fail
  together.
- **The unreachable part is the largest,** 12 to 60 points.
- **Agreement among the arms is a weak free signal:** AUC 0.57 to 0.68 for
  where a per-question choice would gain, and 0.59 to 0.74 for hard
  questions.

**Splitting the unreachable part for ranking** (share of evidence):

| | Outside the 400 candidates | In them, but below every arm's top 10 | Best rank of the missed (median) |
|---|---|---|---|
| BEAM 100K | 2.0 | 31.0 | 50 |
| BEAM 500K | 14.3 | 23.8 | 60 |
| BEAM 1M | 22.1 | 27.2 | 60 |
| BEAM 10M | **41.7** | 20.8 | 63 |
| LoCoMo | 5.5 | 21.1 | 52 |
| LongMemEval-S | 0.6 | 10.7 | 31 |
| ConvoMem | 9.1 | 13.1 | 30 |

So the difficulty has two layers below mechanism choice:

1. **Depth.** Evidence is found but ranked just past what fits, around
   rank 30 to 60. This is a question of how many items fit per token, and
   who makes the final cut. The density results (knapsack, views) act on
   this layer: 30-odd short items in place of 5 whole ones, with the
   reader choosing among them.
2. **Reach.** Evidence the question's words and meaning do not find at
   all. It grows with the size of the record, from 2% at 100K to 42% at
   10M. No choice of ranking, view or calibration reaches it. It needs a
   different link from question to evidence: time, entities, or the
   person's situation (as the situation model did for implicit
   connections).

The signal for view calibration works on the smallest layer. By this
accounting, depth and reach come first.
