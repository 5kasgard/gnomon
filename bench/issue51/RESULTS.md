# Issue #51: results

Test: `PREREG.md` (with amendment 1). Arms and harness built by a Claude Sonnet sub-agent;
numbers from `run.py` via `collect.py`, saved in `results.json`. Rerun independently by the
planner (Claude Opus) on every branch: identical on every measure (only the recorded commit
id differs, as the results were committed after the run). No model was called; $0.

## First test (pre-registered)

| Arm | Roads 1-8 leaking | Widening | u1 | u2 | u3 | u4 | Lines +/- | Existing suite (290) |
|---|---|---|---|---|---|---|---|---|
| 0 (main) | all 8 | no | yes | 6/6 | yes | yes | 0 | pass |
| A one-compartment writers | 8 | no | yes | **0/6** | yes | yes | +8/-0 | pass |
| B refuse mixed turns | 8 | no | yes | 4/6 | yes | yes | +144/-0 | pass |
| C file where it came from | **all 8** | **yes** | yes | 6/6 | yes | yes | +168/-16 | pass |
| C2 (extra, not registered) | 8 | **yes** | yes | 2/6 | yes | yes | +168/-16 | pass |
| D one-compartment sessions | **none** | no | yes | 6/6 | yes | yes | +21/-14 | **7 fail** |

Arm 0 leaks on every road, so every scenario exercises the road it names.

By the decision rule, only D closes all eight roads with no widening. But D breaks seven
existing tests, which the pre-registered use measures (u1-u4) did not cover: they test a
documented feature, a `--per-session` MCP server used **without** the hook, which today starts
its own writer on the first tool call. D makes that server refuse every tool until `attach`,
and without the hook there is no token to attach with. That gap in the measures is the
author's; it was found by running the existing suite, not by u1-u4.

What the data says about each arm:

- **A** stops recording (u2 0/6) and still leaks by write-back (road 8): the model still reads X
  through the MCP server and writes it into Y itself.
- **B** leaks by write-back, costs 144 lines, and drops a third of the clean turns once a
  session has read X (taint carries forward).
- **C** does what the prior predicted: it files X content into Y as well, so it closes nothing,
  and widens (Y content becomes visible to X-only readers). C2 widens too.
- **B and C** detect reads by matching text word for word, so a paraphrase would pass them;
  that was not tested (no model), and A and D do not depend on detection.

Matches the outside prior (whole-session taint tracking suffers label creep; splitting into
single-compartment processes is the standard remedy), but the decisive numbers are these.

## Follow-up test D1 (registered after the first results, before D1 is built)

Hypothesis: D's break is only in its MCP change, which can be narrowed. **D1** = D, except the
per-session MCP server refuses unattached use only for a template that auto mode runs (the hook
records that it runs it, e.g. a marker under `<log>.d/`); for any other template, unattached use
starts an instance as today.

Measures: the same harness (roads 1-8, widening, u1-u4, size), plus the full existing suite,
plus **u5**: a template used both by auto mode and by a standalone per-session server: does the
standalone server still work? (Expected: no; reported either way, as D1's known cost.)

Decision: D1 replaces D if it closes all eight roads with no widening and passes the existing
suite unchanged; otherwise both D and D1 go to the owner with their costs.

## Follow-up test D2 (registered after D1's results and the Gemini review, before D2 is built)

**D2** = D1 without the marker file: the per-session MCP server refuses unattached use only when
started with `--require-attach`, and auto mode's documented MCP configuration (README, and
`settings()` if it prints one) includes it. A resumed session whose instance holds more than Y
gets a fresh one-compartment instance instead of going unrecorded, with a note on stderr.

Measures: as D1 (roads 1-8 with the flag set, widening, u1-u5, size, full suite), plus **u6**: a
resumed pre-upgrade session is recorded, and nothing from X reaches Y. Road 7 is also run
**without** the flag and reported, as the misconfiguration case.

Decision: D2 replaces D1 if it closes all eight roads with the flag set, with no widening, u1-u4
and u6 true, and the suite passing; u5 is expected true.
