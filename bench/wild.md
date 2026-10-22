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

## Result (run once)

Medians over records (`wild_results.json`):

| | Records | Person's message (tokens) | Other side's | Other side over 60 tokens | Nearest earlier echo within 2 conversations | Same, order shuffled | Locality | Echo cosine | Messages echoing an earlier conversation | Conversations per record |
|---|---|---|---|---|---|---|---|---|---|---|
| **WildChat (real)** | 283 | 38 | 318 | 94% | 45% | 22% | **2.06** | 0.56 | 52% | 26 |
| BEAM 1M | 35 | 35 | 995 | 100% | 8% | 1% | 5.43 | 0.60 | 96% | 928 |
| LoCoMo | 10 | 27 | 26 | 5% | 23% | 22% | 1.00 | 0.46 | 85% | 29 |
| LongMemEval-S | 100 | 46 | 420 | 97% | 15% | 15% | 0.98 | 0.30 | 66% | 48 |
| ConvoMem | 20 | 34 | 39 | 17% | 4% | 4% | 1.09 | 0.40 | 99% | 300 |

Where real use sits:

- **Length: like BEAM and LongMemEval, unlike LoCoMo and ConvoMem.** The
  assistant's replies are long (median 318 tokens; 94% over 60), and the
  person's messages are short. Cutting long replies to the part that
  matters paid in this regime (views.md), so the window default fits
  real use.
- **Locality: real, and missing from most benchmarks.** In real
  histories, the most similar earlier message comes from the last two
  conversations twice as often as chance (2.06). BEAM has it in excess
  (5.43). LoCoMo, LongMemEval and ConvoMem have none (about 1.0): their
  histories are assembled, with conversations mixed among unrelated ones.
  Those benchmarks cannot reward links through time, but real use would.
  That is the link reach.md found strongest at scale.
- **Return: real people echo earlier conversations less than the
  benchmarks assume.** 52% of messages, against 66 to 99%. The benchmarks
  are built so that memory matters; real histories are half new.

**Caveats.**
- A hashed IP can merge several people, or split one person.
- One shard covers a few weeks of 2023 ChatGPT use, not years of one
  person's history.
- These are properties of records, not tests of mechanisms. They say
  which benchmark results are likely to carry over to real use: the
  window view and time links. They do not prove it.
