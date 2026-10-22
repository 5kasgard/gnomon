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
