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

## Result: cues do not bridge it

Run on 50 questions where the evidence is in the situation model. Coverage
in the top k facts:

| Ranked by | Top 5 | Top 10 | Top 20 | Top 50 |
|---|---|---|---|---|
| Fact, static embeddings | 10% | 12% | 18% | 30% |
| Cue, static embeddings | 12% | 12% | 18% | 28% |
| Fact and cue fused | 10% | 14% | 22% | 32% |
| Fact, bge-small (a transformer, ONNX on CPU) | 8% | 14% | 20% | 24% |
| Cue, bge-small | 6% | 12% | 18% | 26% |

The bar set in advance (top-20 coverage double the raw figure) is not met,
so there is no reader test for cues.

Linking a request to the fact that should shape it ("set a stand-up time"
to "the intern works 9 AM to 1 PM") is an inference. Neither similarity
method makes it, with or without request-shaped cues. Writing the cues
cost about 520K Haiku tokens.

## Next: ask the reader to select before answering (fixed before running)

Condition B2 changes only the instruction. The reader gets the same
situation model, with its lines numbered. For each question it first
lists up to 3 line numbers that should change its answer, then answers
applying them.

- **Measured directly:** whether a chosen line is evidence.
- **Measured as before:** whether the answer uses the evidence (2+
  distinctive words).
- **Compared with** Haiku A (28.3%) and B (35.0%) on test history 0, 60
  questions.

## Result: select-then-answer (B2), test history 0

| | A (top 10) | B (situation model) | B2 (select, then answer) |
|---|---|---|---|
| Uses the evidence | 28.3% | 35.0% | 36.7% |
| B2 against A, paired | | | better 9, worse 4 |

Of the 50 questions whose evidence was in the model, the reader picked an
evidence line for 40%. Finding the one relevant line among about 1,900 is
the bottleneck, whether a model or an embedding does it. Cost: about 242K
Haiku tokens.

## Next: consolidation into a profile (B3), fixed before running

Human memory condenses many episodes into a compact schema. Here, one
model pass reads history 0's situation model (about 48K tokens) and writes
a profile of at most about 120 lines, grouped under headings for areas of
life and work. Each line states one standing circumstance in plain words
and cites the numbers of the situation lines it rests on, so it can be
checked against the record.

- **The reader (Haiku)** gets the profile and the top 10, with the same
  prompt as B.
- **Measured:**
  - uses the evidence (2+ distinctive words);
  - whether the profile cites an evidence line (presence after
    consolidation);
  - the profile's size in tokens.
- **Compared with** A, B and B2 on the same 60 questions.
