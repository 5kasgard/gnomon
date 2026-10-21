# BEAM: sentences with pointers instead of whole messages

Fixed 2026-10-21 on BEAM 500K, committed before the one run on 1M.
Method and check are in `beam_compress.py`; nothing in them will change
after the 1M run.

**Question:** at a fixed token budget, does giving the reader the
sentences that match the question (each tagged `[m<id>]` so the whole
message can be fetched back exactly) carry more of the needed facts than
whole messages?

**Check (judge-free):** BEAM rubrics name the facts an answer must
mention. A fact is present when every number in it, and two thirds of its
other content words not used by the question, appear in the view. Two
controls are reported beside the views:
- random sentences of the conversation at the same budget, as the chance
  level;
- the evidence messages alone, as the check's own ceiling.

**Selection:** from the top 50 recall hits, sentences scored by the summed
idf of question words they contain over √(message rank + 1), each with the
sentence after it.

## Development (500K, 279 questions, 548 facts; % of facts present)

| Tokens | Whole messages | Sentences | Random sentences |
|---|---|---|---|
| 250 | 10.7 | **30.4** | 7.6 |
| 500 | 11.8 | **36.3** | 10.6 |
| 1,000 | 22.9 | **45.0** | 14.7 |
| 2,000 | 38.8 | **53.6** | 19.5 |
| 4,000 | 49.3 | **60.8** | 29.1 |
| 8,000 | 60.5 | **66.0** | 31.3 |

**Limits:**
- The check is loose on large views: chance reaches about 30% at 4,000
  tokens or more. Compare views only at equal budgets.
- The evidence messages alone hold 44.6%. Rubric facts are often
  paraphrased, or stated again elsewhere in the conversation, so this
  ceiling is not an upper bound on the views.
- Facts present in the view are not answers. A reader run is the test
  that matters.
