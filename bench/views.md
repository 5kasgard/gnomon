# Views: what text the reader actually holds

boundaries.md found that the unit shown under a token budget matters as
much as the ranking. Evidence recall counts a cut line as holding its
whole message, though, and a fact past the cut is not in hand. This test
measures the text itself.

## Protocol, fixed before running

- **Records and questions:** as in boundaries.md (BEAM's 4 sizes, LoCoMo,
  LongMemEval-S, ConvoMem). Only questions with a gold answer count: BEAM
  answer, ideal answer or ideal summary; LongMemEval and LoCoMo answer;
  ConvoMem answer.
- **One ranking for all arms:** hybrid (words and meaning fused, from the
  cache). Only the presentation of each message differs. Budget 2,000
  tokens; items are taken in order while they fit.
  - **A:** whole messages.
  - **H:** each message's first 60 tokens.
  - **K, keyword in context:** each message's 60-token window that
    starts at its sentence sharing the most rare words with the question
    (an idf sum over the record's messages; ties and no overlap: the
    first sentence).
- **Primary measure, judge-free:** answer words in view. That is the share
  of the gold answer's content words found anywhere in the view's text
  (lower-cased; words of 3 or more letters or any number; excluding stop
  words and words in the question). Questions whose answer has no such
  words are dropped.
- **Secondary:** evidence recall (pointer), as before.
- **Questions:** does K hold more answer text than A and H? Per dataset,
  paired K − A and K − H with cluster bootstrap intervals.
- **Not tuned:** 60 tokens, 2,000 tokens and the window rule are fixed
  here, and everything is run once.

## Next, fixed before running: foraging

Information foraging theory (Pirolli and Card) treats a reader as a
forager. Each item shown is a patch. The marginal value theorem says to
stay in a patch only while its yield per unit of effort beats the
environment's average, then move on. Under a token budget, that means
each piece of text should be worth its tokens wherever it comes from.
Whole messages overstay in long patches. A fixed 60-token cut leaves
short ones early, or cuts the wrong part.

Gnomon's `context` already does something close: it ranks sentences from
the top 50 messages by rare words shared with the question, discounted by
the message's rank, and spends the budget on the best sentences
(`select_sentences`). It was measured on BEAM by reader F1
(beam_compress.md), never across datasets.

- **Arms,** one ranking (hybrid), 2,000 tokens:
  - **A:** whole messages.
  - **K:** keyword in context, as above.
  - **K′, length-aware:** the whole message if it is at most 60 tokens,
    otherwise K's window, moved back so it is a full 60 tokens.
  - **S:** Gnomon's sentence view (`select_sentences` over the top 50,
    with the record's sentence frequencies).
- **Measures:** answer words in view (primary) and evidence recall, as
  above.
- **Choice and confirmation:** the arm with the highest mean answer
  words, over the 7 rows (4 BEAM sizes, LoCoMo, LongMemEval-S,
  ConvoMem, each weighted equally), is then tested once against A on
  data never used in this project. That is QASPER's test split (CC BY 4.0;
  questions on scientific papers, the paper's paragraphs as the record,
  the hybrid of paragraph meaning and words as the ranking, and the
  annotated answers).

## Result (run once): whole, first 60 tokens, keyword in context

Answer words in view at 2,000 tokens (`views_results.json`):

| | A, whole | H, first 60 | K, keyword in context | K − A |
|---|---|---|---|---|
| BEAM 100K (275) | 52.4 | 59.6 | 58.8 | +6.4 [3.8, 9.4] |
| BEAM 500K (488) | 51.0 | 58.1 | 58.5 | +7.5 [5.5, 9.6] |
| BEAM 1M (485) | 46.4 | 53.5 | 55.0 | +8.6 [6.3, 11.1] |
| BEAM 10M (136) | 44.9 | 52.2 | 51.7 | +6.7 [2.5, 11.1] |
| LoCoMo (1,480) | **60.5** | 59.9 | 57.9 | −2.6 [−3.6, −1.5] |
| LongMemEval-S (461) | **65.5** | 51.5 | 65.4 | −0.1 [−2.8, 2.6] |
| ConvoMem (1,058) | **67.4** | 65.8 | 64.1 | −3.4 [−4.3, −2.5] |

- **No view wins everywhere.** Cutting puts more answer text in view on
  BEAM (+6 to +9) and less on LoCoMo and ConvoMem (−3).
- **Where the cut lands matters where messages are long.** On
  LongMemEval, keyword in context holds 13.9 points more than the first 60
  tokens (K − H +13.9 [10.5, 17.2]), drawing level with whole messages.
- **K has a flaw on short messages.** For a message shorter than its
  window, K starts at the best sentence and drops what came before it.
  The foraging arm K′ fixes that. It was designed after seeing this
  run's results for every dataset but ConvoMem, so it is not a clean
  test on these datasets. That is why the chosen arm is confirmed on
  QASPER, which no step has seen.

## Result: foraging, and the held-out confirmation fails

Answer words in view at 2,000 tokens (evidence recall), from
`views_forage_results.json`:

| | A, whole | K | K′, length-aware | S, Gnomon's sentence view |
|---|---|---|---|---|
| BEAM 100K | 52.4 (45.1) | 58.8 (70.0) | 59.2 (69.6) | 59.1 (60.0) |
| BEAM 500K | 51.0 (43.7) | 58.5 (64.9) | 58.2 (64.1) | 56.1 (56.9) |
| BEAM 1M | 46.4 (32.4) | 55.0 (54.3) | 55.0 (53.9) | 53.3 (44.0) |
| BEAM 10M | 44.9 (29.6) | 51.7 (44.0) | 51.7 (43.9) | 50.9 (38.7) |
| LoCoMo | 60.5 (76.2) | 57.9 (78.8) | 60.3 (76.6) | 48.8 (66.7) |
| LongMemEval-S | 65.5 (74.0) | 65.4 (90.6) | 65.2 (90.1) | 65.8 (83.1) |
| ConvoMem | 67.4 (81.1) | 64.1 (82.6) | 66.4 (81.2) | 61.2 (79.4) |
| Mean of the 7 rows | 55.4 | 58.8 | **59.4** (chosen) | 56.5 |

**Held out, run once: QASPER test** (1,069 questions on 391 papers, the
paragraphs as the record):

| | A | K | K′ | S |
|---|---|---|---|---|
| Answer words in view | **80.7** | 73.8 | 74.9 | 68.8 |
| Evidence recall | 72.2 | 93.5 | 92.3 | 72.0 |
| Against A | | −6.9 [−8.7, −5.1] | **−5.8 [−7.6, −4.1]** | −11.8 [−13.6, −10.1] |

The chosen view loses on data it had not seen. On papers, the
paragraph that holds the answer is found more often when cut (92% against
72%). But the answer often sits outside the 60-token window chosen by the
question's words, and the whole paragraph holds it.

What holds across all 8 environments:

- **No fixed view is best.** Cutting wins on BEAM by 6 to 9 points, ties
  on LongMemEval, and loses 1 to 6 points on LoCoMo, ConvoMem and
  QASPER.
- **Gnomon's current `context` view (S) is the weakest where messages are
  short or the answer sits away from the question's words:** −11.7 on
  LoCoMo, −6.2 on ConvoMem and −11.8 on QASPER against whole messages.
  That is a finding about the product, and it needs acting on.
- **This is the same shape as ranking** (compartments.md): the best
  mechanism depends on the record, and nothing in the question predicts
  it. There, gated calibration from use worked.

## Next, fixed before running: calibrate the view per memory

The simulation follows calibrate.py, with views as the arms. Each
environment is treated as one memory:

- the 7 environments above plus QASPER;
- default A (whole messages);
- alternatives K, K′ and S.

Each question is one observation of every arm's answer words in view (in
use: whether the text the reader cited or expanded was in each view).
The rule is the product's: switch only after at least 10 observations, to
the arm whose mean paired advantage over the default is above 2 standard
errors. After n = 10, 20 and 50 observations (200 random orders), score
the chosen view on the environment's other questions, against the
default and against the best arm in hindsight.

## Result: calibrating the view per memory works

Answer words in view on each environment's other questions after n
observations (200 random orders, `views_calibrate_results.json`):

| | Default (A) | Best in hindsight | n = 10 | n = 20 | n = 50 | Switched at 10 / 20 / 50 |
|---|---|---|---|---|---|---|
| BEAM 100K | 52.4 | K′ 59.2 | 54.5 | 55.0 | 56.8 | 33% / 40% / 72% |
| BEAM 500K | 51.0 | K 58.5 | 53.3 | 54.4 | 56.8 | 38% / 54% / 88% |
| BEAM 1M | 46.4 | K 55.0 | 49.7 | 52.2 | 54.0 | 42% / 74% / 96% |
| BEAM 10M | 44.9 | K′ 51.7 | 46.5 | 47.8 | 49.9 | 25% / 49% / 88% |
| LoCoMo | 60.5 | A 60.5 | 60.5 | 60.5 | 60.5 | 0% |
| LongMemEval-S | 65.5 | S 65.8 | 65.5 | 65.5 | 65.5 | 2–4% |
| ConvoMem | 67.4 | A 67.4 | 67.5 | 67.4 | 67.5 | 0% |
| QASPER | 80.7 | A 80.7 | 80.6 | 80.7 | 80.7 | 0% |

Where cutting pays, the gated rule finds it: by 50 observations it
recovers 60 to 90% of the gain. Where it does not, it almost never
switches, and costs at most 0.1. This is the second time the same shape
appears: no mechanism is best everywhere, nothing in the question
predicts which is, and a cautious per-memory choice from use captures
most of the difference without harm.

## What changes in the product (post hoc, stated as such)

Over all 8 environments, mean answer words in view are:

| View | Mean | Largest loss to the best fixed view |
|---|---|---|
| K′ (length-aware window) | **61.4** | −5.8 (QASPER) |
| A (whole messages) | 58.6 | −8.6 (BEAM 1M) |
| S (the current sentence view) | 58.0 | −11.8 (QASPER) |

K′ has both the highest mean and the smallest worst-case loss, so it
becomes `context`'s default view. Whole messages and sentences stay
available as views. Calibrating the view from use needs a signal of
whether the text shown sufficed. `expand` alone does not give one,
because a reader may expand for other reasons. That is left open,
not guessed.
