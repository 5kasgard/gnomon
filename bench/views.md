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
