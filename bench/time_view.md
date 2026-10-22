# Time in view: can the reader tell which statement is the latest?

People update what they said: a new personal best, a new job, a moved
date. A memory that keeps both statements, exactly as said, is right to
keep both; superseding is a judgement about meaning, and the record
should not make it. But the reader has to make it, and it can only do so
if it can see which statement came later.

The shipped view (`context`, length-aware windows over the hybrid order)
shows lines in rank order, with opaque pointers and no dates. On
LongMemEval-S's 78 knowledge-update questions, a diagnosis run before
this protocol (exploratory, not a result) found:

- the latest statement is in the 2,000-token view for 97% of questions,
  and an earlier one for 100%;
- the latest is ranked below an earlier one for 49%.

So retrieval is not the problem. Both values are in front of the reader,
in an order that says nothing about time, half the time with the stale
one first. Supersession belongs to presentation, and presentation is
code.

## Protocol, fixed before running

- **Questions:** LongMemEval-S cleaned (MIT).
  - every **knowledge-update** question with answer words: 72 of the 78
    (primary; the other 6 have no content word outside the question);
  - 80 **temporal-reasoning** questions, drawn with seed 20261022;
  - 40 **single-session-user** questions, drawn with the same seed, as a
    control where time should not matter.
- **Lines:** for each question, the lines the shipped view selects at
  2,000 tokens. The same lines in every arm; only their presentation
  differs.
- **Arms:**
  - **P (shipped):** rank order, no dates.
  - **D:** rank order, each line prefixed with its date (YYYY-MM-DD).
  - **T:** record order, oldest first, each line dated.
  The question carries its asking date in every arm, as LongMemEval
  gives it.
- **Reader:** Haiku sub-agents. Each agent reads one arm only, about 40
  questions in shuffled order, and answers each from its view only in
  one short sentence. No agent sees the same question twice.
- **Primary measure:** answer words in the answer (the share of the gold
  answer's content words in the reader's answer, judge-free), per
  stratum, paired against P with bootstrap 95% intervals.
- **Secondary:** a blinded Haiku judge. It sees the question, the gold
  answer and the three answers in shuffled order without arm labels, and
  marks each correct or not. Also token F1.
- **Prediction:** T > P and D > P on knowledge-update and
  temporal-reasoning; no difference on the control.
- **Decision:** ship the arm with the best knowledge-update gain whose
  interval excludes 0, provided the control is not more than 3 points
  worse. Otherwise the view stays as it is.
- **Cost:** the dates add about 4 tokens per line; the lines are not
  reduced to pay for them, and the added tokens are reported.
- **Run once.**

## Result (run once): dates help, but the primary test fell short

Haiku read 192 questions in each arm (15 reader agents, about 2.6M
tokens), then 4 blinded judge agents graded all 576 answers. The dates
added 87 tokens per view on average (`time_view_results.json`).

| | Arm | Answer words in answer | F1 | Judge: correct | Answer words, vs P | Judge, vs P |
|---|---|---|---|---|---|---|
| Knowledge-update (72) | P | 65.4 | 32.0 | 70.8 | | |
| | D | 74.5 | 34.7 | 83.3 | +9.0 [−2.0, +20.5] | **+12.5 [+1.4, +23.6]** |
| | T | 59.8 | 34.4 | 68.1 | −5.7 [−17.6, +6.7] | −2.8 [−15.3, +9.7] |
| Temporal-reasoning (80) | P | 26.6 | 20.3 | 45.0 | | |
| | D | 40.6 | 26.5 | 65.0 | **+14.0 [+5.4, +22.4]** | **+20.0 [+7.5, +32.5]** |
| | T | 37.8 | 25.7 | 62.5 | **+11.2 [+2.3, +20.3]** | **+17.5 [+3.8, +31.2]** |
| Control: single-session-user (40) | P | 85.1 | 50.6 | 90.0 | | |
| | D | 83.6 | 50.2 | 90.0 | −1.5 [−7.9, +3.7] | +0.0 [−10.0, +10.0] |
| | T | 78.0 | 43.8 | 87.5 | **−7.2 [−15.4, −1.2]** | −2.5 [−15.0, +7.5] |

- **Dates in rank order (D)** gain on every time-bound measure and cost
  nothing on the control. On knowledge-update the judge's gain (+12.5)
  excludes 0, but the primary, judge-free gain (+9.0) does not.
- **Record order (T)** helps temporal questions about as much as dates
  alone, but loses on knowledge-update and costs 7 points on the control.
  Rank order carries information the reader uses; time order throws it
  away.
- **The prediction held for D and failed for T** on knowledge-update.

By the rule fixed above, nothing ships from this run: the primary
interval for knowledge-update includes 0. D is the only candidate, and
it gets one confirmation on questions this run did not use.

## Confirmation, fixed before running

- **Arms:** P and D only, built as above.
- **Questions,** none used above:
  - LongMemEval-S: 80 **multi-session** questions (seed 20261022) and
    the **23 temporal-reasoning** questions not drawn above;
  - LoCoMo (evaluation only; nothing is learnt from it): 80
    **temporal** questions, seed 20261022, dated by their sessions.
- **Reader, judge and measures:** as above.
- **Primary:** answer words in the answer, D − P, pooled over all the
  confirmation questions, bootstrap 95% interval.
- **Decision:** dates are added to `context`'s view if the pooled
  interval excludes 0 and neither dataset is more than 3 points worse.
  Otherwise the view stays as it is.
- **Run once.**

## Confirmation result (run once): dates ship

10 reader agents answered the 183 held-out questions in each arm, and 4
blinded judge agents graded them (`time_view_confirm_results.json`).

| | P | D | Answer words, D − P | Judge, D − P |
|---|---|---|---|---|
| LongMemEval-S, multi-session and temporal (103) | 50.3 (judge 63.1) | 49.7 (68.0) | −0.7 [−7.3, +5.8] | +4.9 [−3.9, +13.6] |
| LoCoMo, temporal (80) | 10.0 (7.5) | 71.3 (66.2) | **+61.3 [+52.9, +69.4]** | **+58.8 [+47.5, +70.0]** |
| **Pooled (183, primary)** | 32.7 (38.8) | 59.1 (67.2) | **+26.4 [+19.6, +33.4]** | **+28.4 [+20.2, +36.6]** |

The pooled interval excludes 0, and neither dataset is more than 3 points
worse (LongMemEval −0.7). By the rule fixed above, dates are added to
`context`'s view: each line reads `[pointer] YYYY-MM-DD speaker: text`, in
rank order.

What the numbers mean:

- **LoCoMo's gain is mostly mechanical.** Its temporal questions ask
  "when did ...?", and the answer is the date of the session. Without
  dates the reader cannot answer at all (7.5%). That is a real failure of
  the old view, not an artefact. A person's "when did I ...?" has the same
  shape.
- **On LongMemEval's held-out questions the gain is small or none.** Most
  are multi-session counts, which dates do not help.
- **Across both runs, dates never cost more than 1.5 points** on any set of
  questions, and the judge never scored them lower.
- **The first run's primary test fell short.** The knowledge-update gain
  (+9.0) had an interval including 0. The confirmation was pooled over
  different question types, so it confirms dates in general, not the
  knowledge-update gain in particular. The judge's +12.5 [+1.4, +23.6] is
  the best evidence for that, and it is a secondary measure.
