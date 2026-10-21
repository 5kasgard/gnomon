# BEAM: salience at write time — the source first, then surprise

Fixed 2026-10-21 on BEAM 500K and committed before the one run on 100K,
1M and 10M. Nothing below changes after that run.

## The idea and how it was found
A test of event segmentation theory: the points where a concept sequence
changes, which are where a next-concept model's prediction fails. The test
asked whether those points are the messages that BEAM's ordering
questions ask about.
- **Ordering: no.** The novelty of a user message (1 minus its closest
  cosine to earlier messages) barely separates evidence from other user
  messages (AUC 0.546).
- **Factual and summary questions: yes, unexpectedly.** AUC 0.80 for
  information-extraction evidence and 0.69 for summaries, among the user's
  messages only. It holds with session-opening messages excluded (0.775
  and 0.666), so it is not an artefact of where sessions start.

Novelty as a third fused ranking raised recall@10 from 57.5 to 61.7 and
halved the tokens. The controls then showed where most of that came from.
Shortest-first scored 61.0 and the user's own messages first 61.5, while a
random third ranking scored 56.0: most of the gain is about preferring the
person's own words. Novelty adds on top of that for factual questions
(68.2 against 63.2).

## Fixed choices
- **Novelty:** 1 minus the highest cosine (static embeddings, as in
  `beam_dense.md`) to any of the previous 2,000 messages. It is computed
  once when a message is written and needs no query.
- **Salience ranking:** the hybrid's top 200, sorted by primary source
  first (the person's own messages before the assistant's), then by
  novelty, highest first.
- **Fusion:** reciprocal rank fusion of words, meaning and salience
  (k = 60, no weights).
- **Also reported on the held-out sizes, for comparison only:** hybrid,
  hybrid + novelty alone, and user-first alone. These are the controls
  that decide whether novelty earns its place.

## Development (500K, recall@10)

| | Hybrid | + novelty | User first | Random | **Source + novelty** |
|---|---|---|---|---|---|
| All | 57.5 | 61.7 | 61.5 | 56.0 | **62.6** [59.2, 65.9] |
| Information extraction | 56.4 | 68.2 | 63.2 | 58.2 | 68.2 |
| Multi-session | 52.8 | 60.6 | 60.1 | 50.7 | 61.9 |
| Temporal | 74.5 | 83.6 | 83.5 | 73.8 | 84.3 |
| Summaries | 11.5 | 10.8 | 9.6 | 10.7 | 10.8 |
| Median tokens of top 10 | 4,069 | 2,187 | 1,268 | 4,114 | 1,840 |

## A caution recorded in advance
BEAM asks mostly about facts the user stated. In summaries, 64% of the
evidence is the assistant's, and summaries do not gain. A record where the
answers live in other people's or tools' words would weigh sources
differently. "Primary source" should mean the root of the information,
not "the human".
