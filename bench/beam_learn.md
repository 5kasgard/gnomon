# BEAM: many weak signals, weighed by small steps

Fixed 2026-10-21 on BEAM 500K and committed before the one run on 100K,
1M and 10M.

## Where it came from (all on 500K, 20 conversations)
- **Surprise relative to the question's topic** (the top 300 by meaning,
  in time order) separates evidence from other candidates. AUC:
  - ordering 0.73, where similarity to the question gives 0.60;
  - facts 0.84;
  - summaries 0.51.
- **Controls.** Among the person's own messages only, topic surprise
  still separates evidence for facts (0.85) and summaries (0.66), but not
  for ordering (0.55). Most of the ordering signal was "the user said it".
- **Typicality of the topic.** Fact and ordering evidence is atypical of
  its topic (AUC 0.28-0.35). Summary evidence is slightly typical (0.56).
  This matches the two routes to memory in the psychology literature:
  schema violation for events, schema fit for gist. Within the person's
  own messages it is weak (0.50-0.60).

A signal that helps one kind of question and not another needs weighing
per kind. Neither a fixed fusion nor a hand-picked rule can do that.

## Fixed choices (`beam_learn.py`)
- **Candidates:** the union of the top 200 by words and the top 200 by
  meaning. It holds 84.5% of the evidence at 500K.
- **Eleven signals:**
  - word-rank score;
  - meaning similarity, and its rank score;
  - the person's own words;
  - shortness;
  - holds a number;
  - surprise when written (against the previous 2,000 messages);
  - surprise within the topic;
  - atypicality;
  - position in time;
  - a bias term.
- **Cues:** each signal is crossed with five question cues (order,
  summary, when, count, change) plus "always". That gives 66 weights.
- **Training:** listwise softmax over each question's candidates. Adam,
  learning rate 0.03, L2 1e-3, 400 full-batch steps, seed 0. Trained on
  all 35 conversations at 500K.
- **Scoring:** recall@10 with standing directives, against hybrid and own
  words first on the same candidates.

## Validation inside 500K (25 conversations trained, 10 held aside)

| | Hybrid | Own words first | Learned |
|---|---|---|---|
| All | 59.4 [56.5, 62.8] | 63.6 [60.6, 67.0] | **66.6** [62.0, 70.7] |
| Median tokens of top 10 | 4,501 | 1,263 | **493** |

With L2 at 1e-2 the learned arm scored 66.1, so the result is not
sensitive to that choice.

## A caution recorded in advance
BEAM's questions come from templates. The cue weights could learn
BEAM's phrasing rather than general regularities, and conversations at
other sizes share those templates. A held-out size tests transfer to new
conversations, not to new kinds of question or other people's records.

## Held-out result (one run; `beam_learn_results.json`)

Recall@10 with directives; 95% intervals by conversation:

| | Hybrid | Own words first | **Learned** | Median tokens (hybrid / own / learned) |
|---|---|---|---|---|
| 500K (training, in-sample) | 57.5 | 61.5 | 65.3 | 4,069 / 1,268 / 609 |
| 100K | 63.4 [60.3, 66.5] | 65.8 [62.5, 68.8] | **68.1** [65.6, 70.4] | 4,519 / 1,377 / **645** |
| 1M | 46.4 [44.5, 48.4] | 50.3 [48.4, 52.4] | **53.1** [50.6, 55.7] | 3,893 / 1,061 / **454** |
| 10M | 33.0 [29.5, 36.5] | 36.1 [32.3, 39.7] | 36.1 [32.9, 39.2] | 3,548 / 1,139 / **844** |

At 100K and 1M the learned weighting recalls the most evidence while
sending the fewest tokens. At 1M that is 454 tokens against 3,893 for the
hybrid, about 8.6 times fewer, with 6.7 points more evidence. At 10M it
ties own words first in recall, using fewer tokens.

**What the weights learnt.** No rule was written in; the data set these:
- **Atypical of its topic:** +1.86 overall but -0.79 for summary
  questions. Facts sit where a topic's pattern breaks; gist sits where it
  holds. These are the two routes to memory that schema research
  describes: prediction error, and schema fit.
- **The person's own words:** +3.01 for ordering questions and -2.55 for
  summaries. "What did I bring up" is answered by the person's turns;
  summaries are answered by the assistant's syntheses.
- **Surprise when written:** +1.00 overall and +0.89 more for "when"
  questions, but -0.64 for ordering and -0.77 for counts.
- **Position in time:** negative for "when" and count questions, so
  earlier messages are preferred there.

**Limits.**
- One training set, one run.
- BEAM's question templates are shared across sizes. The cue weights
  could carry BEAM phrasing that would not transfer to other people's
  questions.
- The gain shrinks at 10M.
- Ordering and summaries stay low: the evidence is not among the
  candidates (84.5% overall reach, much less for those two).

## Also tried: changing the projection (`beam_project.py`)
A rank-16 adjustment of the meaning space, trained contrastively on
which messages answered which questions. Validated inside 500K only (25
conversations trained, 10 held aside): dense recall@10 moved from 47.8 to
50.1, with overlapping intervals.
- Instructions rose from 25.0 to 54.2 and preferences from 40.8 to 52.5.
- Multi-session questions fell from 64.0 to 54.0.

This is not a result. It was not run on the held-out sizes, so as not to
spend the one held-out run on a weak signal. A larger training set, or
learning from real use (which pointers were expanded and cited), is the
better route for it.
