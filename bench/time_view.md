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
  - all 78 **knowledge-update** questions (primary);
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
