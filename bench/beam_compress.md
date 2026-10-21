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

## v3: a fairer message view (correction)

The message view in v1 and v2 stopped at the first message that did not
fit, so at small budgets it often used only part of its tokens. That
flattered the sentence view. v3 cuts the overflowing message to fill the
budget. The v2 column is kept beside it. Same check, same sizes:

| Tokens | 1M messages v2 | 1M messages, filled | 1M sentences | 1M random |
|---|---|---|---|---|
| 250 | 8.5 | 20.8 | **26.6** | 6.7 |
| 500 | 11.5 | 23.9 | **33.7** | 9.9 |
| 1,000 | 22.0 | 29.4 | **41.1** | 14.8 |
| 2,000 | 35.1 | 39.3 | **50.7** | 18.7 |
| 4,000 | 47.9 | 49.4 | **59.1** | 28.1 |
| 8,000 | 59.7 | 60.1 | **67.9** | 34.5 |

100K and 500K follow the same pattern (`beam_compress_results.json`).
The fair gap at 1,000 tokens is about 12 points, not 20.

## Answers: the reader test (`beam_qa/make_views.py`, `score_views.py`)

Haiku answered the same 100 questions from 10 random 1M conversations as
`beam_qa`, given one view at a fixed budget. The prompt was fixed in
`beam_qa/PROMPTS.md` before any reader ran. Judge-free, with 95% intervals
by conversation:

| | Messages, 1,000 | Sentences, 1,000 | Messages, 2,000 | Sentences, 2,000 |
|---|---|---|---|---|
| F1 | 18.1 | **31.3** | 22.7 | **35.2** |
| Every gold number right | 20.9% | **52.2%** | 26.9% | **58.2%** |
| Wrongly "not mentioned" | 55.0% | **28.8%** | 42.5% | **25.0%** |
| Abstains when absent (n=20) | 70% | 75% | 74% | 63% |

Paired by question, sentences minus messages:
- 1,000 tokens: F1 +13.2 [+7.0, +19.7]. Better on 39 questions, worse on
  21. Numbers right on 35 against 14 of 67.
- 2,000 tokens: F1 +12.6 [+6.6, +18.9]. Better on 44, worse on 18.
  Numbers right on 39 against 18.

For scale, the earlier run gave Haiku the top 10 whole messages (about
5,500 tokens) and reached F1 33.6 with 54% of numbers right. Sentences
reach about that at 1,000 tokens, roughly a fifth of the tokens.

Abstention on the 20 unanswerable questions does not differ beyond the
noise. One run, one reader model; one answer is missing in each
2,000-token condition.
