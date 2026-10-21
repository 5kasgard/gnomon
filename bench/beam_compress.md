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

## The 1M run, a fault in the check, and v2

Run once at 1M as fixed above. The rubric parser read only "should
mention:". BEAM also writes "should contain:" and "should state:", and at
1M almost every rubric uses those (1 of 399 lines matched). v1 therefore
scored 72 questions at 1M, and the evidence messages held only 13.9% of
those facts. The v1 numbers are kept in git history and the run log.

v2 reads all three phrasings. It changes which facts are scored, not the
selection or the check. It was then run on 100K, 500K and 1M. 100K was
never used for development.

Results (`beam_compress_results.json`; % of facts present):

| Tokens | 100K messages | 100K sentences | 500K messages | 500K sentences | 1M messages | 1M sentences | 1M random |
|---|---|---|---|---|---|---|---|
| 250 | 9.0 | **30.3** | 11.3 | **31.8** | 7.6 | **26.6** | 6.7 |
| 500 | 10.1 | **37.8** | 12.9 | **37.8** | 10.8 | **33.7** | 9.9 |
| 1,000 | 21.0 | **45.9** | 22.5 | **46.7** | 21.0 | **41.1** | 14.8 |
| 2,000 | 39.9 | **55.5** | 39.2 | **54.3** | 33.7 | **50.7** | 18.7 |
| 4,000 | 54.7 | **64.0** | 49.6 | **60.5** | 46.2 | **59.1** | 28.1 |
| 8,000 | 63.9 | **70.7** | 61.4 | **65.6** | 58.7 | **67.9** | 34.5 |

At 1M, 2,000 tokens of sentences hold more of the needed facts than 4,000
tokens of whole messages. 500 tokens of sentences hold about what 2,000
tokens of messages do. The gain is largest at small budgets and narrows
as the budget grows. Every sentence keeps its message id, so nothing is
lost to the record: the full message can always be fetched back.

## Cost per query (1M-token history, base-price input-token equivalents)

Prompt caching bills a cache hit at 0.1x the input price (0.05x on Opus
and Sonnet 5.5). Directives (median 841 tokens at 1M) go in the cached
prefix.

| Context | Per query | vs row 1 |
|---|---|---|
| Whole history, cached (if a model accepted 1.09M tokens) | 109,330 | 1x |
| Last 200K tokens, cached | 20,000 | 5.5x less |
| Gnomon whole messages 4,000 + cached directives | 4,084 | 27x less |
| Gnomon sentences 2,000 + cached directives | 2,084 | 52x less |

These are input tokens only. Facts present in a view are not the same as
correct answers: a reader run on the sentence view is the next test.
