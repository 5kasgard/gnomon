# Wild: where real use sits

Every benchmark so far is synthetic or semi-synthetic: BEAM, LoCoMo and
ConvoMem were written by language models, and LongMemEval's histories
were assembled. Three properties of a record decided which mechanism won
on them, and none needs answer labels:

- **message length:** cutting long messages pays (views.md);
- **locality:** what is related was said close together, so the time link
  pays at scale (reach.md);
- **return:** whether people come back to earlier things at all, which
  is what makes memory matter.

WildChat (Allen AI, ODC-BY) holds about a million real conversations
between people and ChatGPT. Each carries a hashed IP and a timestamp, so
conversations from one address form a history.

This analysis measures the three properties on real histories and on the
benchmarks. Only aggregates are reported. No conversation text leaves
the machine or enters the repository.

## Protocol, fixed before running

- **Real histories:** WildChat-1M, shard 0 (about 60,000 conversations).
  English conversations, grouped by hashed IP. Addresses with 10 to 200
  conversations count as histories: fewer gives no history, and more is
  likely shared or automated. The 300 largest qualifying histories are
  used.
- **Benchmarks:**
  - BEAM 1M: all conversations; a session is a conversation.
  - LoCoMo: all; a session is a conversation.
  - LongMemEval-S: the first 100 histories.
  - ConvoMem: the 20 implicit-connection cases of the earlier tests.
  - The person's messages only ("user"; speaker A in LoCoMo).
- **Measures, per record, then the median over records:**
  1. **Length:** the median length of the person's messages and of the
     other side's, in tokens; and the share of the other side's messages
     over 60 tokens.
  2. **Locality across conversations:** for each of the person's
     messages from the 3rd conversation on, find the most similar
     earlier message of theirs in an earlier conversation (cosine,
     potion-retrieval-32M). How many conversations back is it? Reported:
     the share within 2 conversations back, against the same after
     shuffling the order of conversations (10 shuffles). The ratio is the
     locality.
  3. **Return:** the same nearest earlier message's cosine. Reported: its
     median, and the share above that record's own 95th percentile of
     cosines between unrelated pairs (pairs drawn from random
     conversations). That is the share of messages that clearly echo
     something said in an earlier conversation.
- **Not tuned:** the thresholds above are fixed here. Run once.
