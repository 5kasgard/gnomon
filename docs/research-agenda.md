# What is measured, what is missing, what to research next

Written 2026-10-21 after a full pass over the project, the 2026 literature,
the published memory leaderboards and audits, and the owner's other
projects (Mycel, Axiom, RabbitHole, the Convergence Lab). Every claim below
is either measured in this repository (with the command that reproduces it)
or cited. Where something is a guess, it says so.

## 1. Where Gnomon stands, by the numbers

| Axis | Measured | How |
|---|---|---|
| Recall, turn level (LoCoMo, held out) | 74.4% of evidence turns in the top 10, 2 ms per question | `python bench/locomo.py --split test` |
| Recall, session level (LoCoMo, all) | 86.7% top 5, 92.1% top 10 | same, `session@k` columns |
| …against a corrected answer key | within half a point | `--audit errors.json` (dial481/locomo-audit) |
| End-to-end answers (LoCoMo 5-9, 200 questions + 50 unanswerable) | F1 54.6 from Gnomon's top 10 (521 tokens) vs 61.7 from the whole conversation (22,818 tokens); abstains on 86% of unanswerable vs 78% | `bench/qa/score.py` |
| Recall where the history cannot be read (BEAM, 100K-10M tokens) | top 10 (~5,500 tokens) holds 38.7 / 27.0 / 19.9% of evidence at 500K / 1M / 10M, against 13.5 / 3.1 / 0% in the last 200K tokens; at 100K the window wins (96.3 vs 45.1) | `python bench/beam.py --data BEAM` (`bench/beam_results.json`) |
| Standing directives loaded with every recall (BEAM, fixed on 500K) | instructions 4-6% -> 91-94% and preferences 15-42% -> 43-74% at 100K and 1M held out; all questions +13-14 points for ~1,000 more tokens; at 10M only +4 (the store outgrows its 1,500-token budget) | `python bench/beam.py --consolidate`, `bench/beam_consolidate.md` |
| Answers at 1M tokens (BEAM, Haiku, 100 questions / 10 conversations) | F1 33.6 and every number right on 54% from Gnomon's top 10, vs F1 8.8 and 5% from the last 128K tokens (same 3 conversations: +23 F1, ahead in all 3) | `bench/beam_qa/score.py` |
| Cheap workers vs an LLM (spam, star ratings, sentiment) | see README "The floor" | `python bench/floor/run.py`, protocol fixed in `bench/floor/PROTOCOL.md` |
| Form learner under change | change detection leaves regret in an unchanging world as it was (0.004-0.019) and cuts it after a change (0.13-0.20 to 0.08-0.16) | `tests/test_forms_stress.py` (simulated worlds, seeds held out) |

## 2. Blind spots, and what the evidence says about them

**The memory benchmarks are weak instruments.** LoCoMo's answer key is
6.4% wrong, its usual LLM judge accepted 63% of deliberately wrong answers,
and a full-context prompt beats several memory systems on it (Penfield
Labs audit, 2026; github.com/dial481/locomo-audit). LongMemEval-S fits in
a modern context window. Vendor scores are irreproducible: Mem0's published
93.4% on LongMemEval reproduced at 73.8% in a competitor's harness
(maximem.ai, 2026), and independent re-runs of the same systems differ
by tens of points (memoryatlas.dev). Consequences for Gnomon:
- keep judge-free metrics (evidence recall, token F1, abstention) as the
  headline, and never quote an LLM-judged score alone;
- report the full-context baseline next to every memory result (done in
  `bench/qa`: the memory reader is behind on F1 but uses ~44x fewer
  tokens);
- the benchmark where memory must win is one whose history cannot fit in
  context: BEAM and LongMemEval-M. BEAM's conversations are in git and
  are now measured (`bench/beam.py`, retrieval only, judge-free): the
  crossover is between 100K and 500K tokens, and answers follow
  (`bench/beam_qa`: memory ahead by 23 F1 at 1M). Still to do: a larger
  answer run with a second reader model, and LongMemEval-M and
  static embedding models, which are on Hugging Face, which this
  workspace's network does not allow (claude.ai Settings > Capabilities).

**Unanswerable questions are scored by almost no one.** LoCoMo's 446
adversarial questions are left out of every published score. Gnomon now
reports abstention on them (`bench/qa`), and it is a place a verified
record should be strong: "not in the record" is a checkable answer.

**Our own weak points, stated:**
- probes of 250-300 items give intervals of ±3-6 points; claims below that
  resolution are not claims;
- one reader model (Claude Haiku) and one run per condition;
- the SMS spam rules were written by a model that may have seen the data;
- the learner was stress-tested in simulated worlds we designed (each task
  type's payoffs independent, no shared structure); a real record of form
  outcomes does not exist yet;
- the preregistration check proves order within a log, not wall-clock
  time: the log's head hash must be published somewhere public to stop
  whoever holds the file from rebuilding it in another order;
- the turn-level recall ceiling is lexical: multi-hop (46% at top 10) and
  open-domain (36%) questions need meaning, not words. A diversity rerank
  was tried and lowered recall; the next candidate is a semantic layer,
  which needs model weights (blocked as above);
- on BEAM, recall falls with length (45 / 39 / 27 / 20% at top 10 from
  100K to 10M) and is near zero where the question cannot share words
  with its evidence: standing instructions and preferences (0-42%) and
  orderings and summaries spread over many messages (under 10% at 1M).
  Standing directives now cover instructions at 100K-1M (above); at 10M
  the store outgrows its budget and needs supersession. Coverage by part
  of the record was tried and did nothing for orderings and summaries:
  about 70% of their evidence is outside the top 200 by words, so they
  need dated summaries written once (an offline model pass) or meaning.
  At 10M the first query builds the index (p95 7.9 s).

## 3. Mechanisms that are missing or only half built

Ranked by what they unlock. Sources in brackets: the owner's own repos are
cited where they already contain a tested design.

1. **A real multi-step goal run through the company** (M9's open item),
   with forms chosen by `design_team`, outcomes recorded, and the result
   compared to one agent alone. Everything about the firm is priced or
   simulated until this exists.
2. **Shadow mode for new forms** [Axiom: SHADOW/ACTIVE/SAFE authority
   modes]: a candidate form runs alongside the incumbent on real work, its
   output judged but not delivered, until the record says it is safe to
   promote. This is how the learner should explore without risking
   delivery.
3. **Work-order leases, retry ceilings and budgets** [RabbitHole
   `perpetual_runtime`: leases, explicit budgets, abstention, exact
   replay]: the firm's spin-up and spin-down, so a worker that stalls loses
   its order and an order cannot run forever.
4. **Causal attribution of outcomes to structure** [RabbitHole `causal.py`:
   identification before estimation]: a bandit learns what pays, not why;
   a form that wins on easy work looks good for the wrong reason.
   Point-in-time features [Axiom: point-in-time learning examples] stop
   the learner using what was only known afterwards; Gnomon's two clocks
   already record both times.
5. **Choosing what to measure** [RabbitHole `experiment.py`:
   discriminating test selection; Axiom: information-value ranking]: the
   probe a floor is checked on should be the items that best tell the
   candidate workers apart, not a random sample.
6. **Costs kept in their dimensions** [RabbitHole `resources.py`]: tokens,
   dollars, latency and risk are collapsed into one number in the planner;
   they should be stored apart and combined only by a stated policy.
7. **Independence by root source** [RabbitHole `provenance.py`]: Gnomon
   counts copies of one model once; a summary of a summary should count
   once too.
8. **Load-bearing tests** [RabbitHole `organs.py`/`assurance.py`:
   architectural mutation]: a capability is real only if removing the part
   that claims it breaks it. Gnomon has ablations for recall; every forms
   rule and every firm mechanism should have one.
9. **A units-and-intervals checker** [RabbitHole `core.py`: interval
   arithmetic with dimensional algebra]: another exact verifier alongside
   arithmetic and dates.
10. **Exchange without consensus laundering** [RabbitHole `federation.py`]:
    the design for multi-writer merge and the commons (spec section 16).
11. **Tabular cheap workers** [Axiom: TabPFN]: a pretrained tabular model is
    a strong floor worker for structured data, untested here.
12. From the spec's own list: signatures, segmented storage with
    encrypted sync (M10), a hosted connector (M11), a web viewer,
    aggregation in queries, HTTP transport, a persistent recall index.

## 4. Questions worth research time

- Does a verified record change how often a reader abstains correctly,
  compared with plain retrieval? (Measurable now on LoCoMo category 5.)
- At what history length does memory overtake full context, for Claude
  models? (ConvoMem puts it near 150 conversations for others.)
- Can the floor's confidence be made to track errors on fuzzy work
  (conformal calibration), or is that a property of the task?
- Do dumb-node substrates like Mycel earn a place on text, given the right
  encoding? Through a 64-dimensional projection, Mycel's own frozen
  configuration reached 97.4% on spam and 86.9% on sentiment, level with a
  swarm of boosted stumps and a point or more below one linear model; a
  configuration of our choosing barely learnt. Where it was designed to
  work (numeric streams) it matched an MLP at a tenth of the energy on one
  of three tasks (Mycel round 5). The encoding and the setup decide it.
- What does an organisation's structure cost when its members are copies
  of one model: how much of the measured gain from model diversity
  survives once correlated errors are counted?
