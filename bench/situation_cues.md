# Retrieval cues written at encoding time

Fixed 2026-10-22 before any cue was written. Committed before the run.

**Why.** In the reader test (`situation_reader.md`), neither Haiku nor
Sonnet applied a situation model of about 1,900 sentences well. On test
history 0:

| Reader | Without the situation model | With it |
|---|---|---|
| Haiku | 28.3% | 35.0% |
| Sonnet | 38.3% | 40.0% (8 better, 7 worse) |

The figures are the share of answers that use the evidence. Choosing a
per-question slice by similarity fails: the evidence is in the 10 most
similar sentences for 15% of questions, because applicability is not
similarity.

**Idea: encoding specificity** (Tulving and Thomson, 1973). Recall
succeeds when the cue at retrieval matches what was stored at encoding.
So at write time, each situation fact is stored with the kinds of future
requests it should shape. At question time, the question (a request) is
matched against those cues (requests) by meaning, so like is compared
with like.

**Method.**
- Test history 0's distilled situation model (1,902 sentences) is split
  into 4 parts.
- Each part goes to one Haiku sub-agent, which writes for each sentence
  one short line: "requests this should shape: ...".
- Each fact is embedded as its cue line (static embeddings), and the
  question as itself.

**Free check first.** Evidence coverage in the top k facts (k = 5, 10,
20, 50), ranked by question-to-cue similarity, against question-to-fact
similarity. 50 questions where the evidence is in the situation model.

**Next only if the check shows** top-20 coverage at least double the
raw-similarity figure: a reader test giving each question its top-20
facts.
