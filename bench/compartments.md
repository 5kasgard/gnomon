# Compartments: which mechanism suits which record, and how to find out

Measured 2026-10-22 from the cached features of every earlier run. No
model calls. Recall@10 with directives. Scripts: `landscape.py`,
`router.py`, `calibrate.py`.

## 1. The landscape (`landscape_results.json`)

**Within a dataset, categories differ little in which mechanism wins.** A
perfect router by question category gains 0.3-2.2 points over the best
single mechanism:

| Dataset | Best single | Perfect category router |
|---|---|---|
| BEAM 500K | 62.7 | 63.0 |
| BEAM 1M | 50.3 | 50.7 |
| LongMemEval-S | 83.6 | 85.8 |
| ConvoMem | 72.7 | 73.9 |

**Across datasets the winner flips, by a lot:**

| Dataset | Words | Meaning | Hybrid | Own first | Invariant |
|---|---|---|---|---|---|
| LoCoMo | **70.4** | 26.0 | 47.5 | 43.8 | 51.8 |
| LongMemEval-S | 69.3 | 78.2 | 78.6 | **83.6** | **83.6** |
| ConvoMem | 66.2 | 56.5 | 69.9 | 71.3 | **72.7** |

On LoCoMo, adding meaning to words costs 18.6 points: the static
embeddings barely separate its casual two-person chat (26.0 alone). On
LongMemEval, meaning alone beats words by 8.9. So what decides the right
mechanism is the environment, not the question type.

## 2. A query-time router does not find it (`router_results.json`)

**Setup.** The router uses signals visible when a question is asked:
- agreement between the words and meaning top 10;
- meaning's best cosine and its fall to the 10th;
- how many candidates words found;
- question length and cues.

It predicts each arm's recall (one ridge regression per arm) and picks the
best. It was tested leave-one-dataset-out: trained on three datasets,
scored on the fourth.

**Result.** It lost to the invariant model everywhere:

| Test set | Invariant | Routed |
|---|---|---|
| BEAM | 62.7 | 58.0 |
| LoCoMo | 51.8 | 51.8 (it never chose words) |
| LongMemEval-S | 83.6 | 80.1 |
| ConvoMem | 72.7 | 68.1 |

A per-question oracle shows 4-21 points of headroom. But three training
environments are three examples of the switch, too few to learn it from
query signals.

## 3. Per-memory calibration from use (`calibrate_results.json`)

A real memory gets feedback: which pointers the reader expanded and cited.
Simulated by showing n labelled questions from one environment. The
default stays the invariant model. The system switches to another
mechanism only on a clear paired win: a mean difference over the feedback
questions more than 2 standard errors above zero. Results are the mean
over 200 random draws per n, scored on the remaining questions:

| Environment | Default | n=5 | n=10 | n=20 | n=50 | Best possible |
|---|---|---|---|---|---|---|
| BEAM 500K | 62.7 | 62.7 | 62.7 | 62.6 | 62.7 | 62.7 |
| BEAM 1M | 50.1 | 50.1 | 50.1 | 50.1 | 50.1 | 50.3 |
| LoCoMo | 51.8 | 51.8 | 55.2 | 61.1 | **69.1** | 70.4 |
| LongMemEval-S | 83.6 | 83.6 | 83.6 | 83.6 | 83.6 | 83.6 |
| ConvoMem | 72.7 | 72.7 | 72.7 | 72.7 | 72.6 | 72.7 |

**Without the gate,** picking whichever arm did best on the feedback
reaches LoCoMo 70.0 at n=20, but loses 0.5-2.3 points where the default is
already best. The gate removes the losses (at most 0.1) and keeps most of
the gain once feedback accumulates.

**The design this supports:**
- a default mechanism that held across environments;
- per memory, a record of which mechanism ranked the evidence the reader
  actually used;
- a statistical gate before changing.

The change itself is an event in the log, so the memory's choice of how to
see is auditable and can be undone.

**In the product** (`gnomon.rank.Calibration`, MCP server with `--meaning`):
- Every `expand` of a pointer that `context` offered counts as one
  observation.
- The gate needs at least 10 observations. Below that the default stays,
  which also rules out switching on a few identical observations with zero
  variance; the simulation above applies the same minimum.
- A switch is written into the memory as a derived claim in the frame
  `gnomon/ranking`.
- On a LoCoMo conversation the product's default and words arms reproduce
  the bench (default 43.2 = 43.2; words 71.5).
