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
