# Issue #48 bake-off: independent specs, independent builders, every build judged alike

Pre-registered 2026-10-30, before any spec is written. Cost bound: $0 API (Claude sub-agents
on the owner's plan; Gemini through the owner's chat). Owner pastes: one spec, one red team.

## Why

Three review rounds (Haiku, the planner, Gemini) each found new real problems in the builds,
so another round would too: review alone has no stopping point. Outside prior: independently
built versions from one spec fail together far more than independence predicts, mostly where
the spec is hard (Knight and Leveson, IEEE TSE 1986). So diversity is sought in the **specs**,
and every build is judged by the same exact tests, including tests it never saw.

## Arms

- **K, combo3:** the planner-led build (`arm48-combo3`), as it stands.
- **SC:** a spec by a fresh Claude Opus agent.
- **SG:** a spec by Gemini (Argon, thinking high), via the owner.

Both spec authors get the same prompt (`spec-prompt.md`: the problem, the channels found so
far, main's code) and nothing else. Each delivers rules, pitfalls and two-world probes.
Each spec is built by a fresh Claude Sonnet agent that sees **only that spec's rules and
pitfalls**, never any probes, arms, reviews or results (held-out tests). Branches
`arm48-SC`, `arm48-SG` from main. A non-Claude builder was considered: Gemini cannot push
code here without the owner relaying every step, and the free open models are out of
allowance; recorded as a limit (all three builds are by Claude).

## Tests (frozen when both specs are in, before any build exists)

The union: `run.py` (channels, P1-P23, suites, replay), `review_checks.py`,
`gemini_checks.py`, and both specs' probes (`probes_SC.py`, `probes_SG.py`, as delivered;
fixed only for API misuse, each fix recorded).

## Measures (all by script)

1. **Gate:** cargo and Python suites pass; every old log replays to main's state hash. An arm
   failing this is out.
2. **Crossings:** the number of two-world probes, across the union, that cross. Each spec's
   declared limits are reported beside it; a probe counts whatever a spec declared.
3. **Side effects:** any probe whose world without the hidden action differs from main's
   (a behaviour change for normal use), listed.
4. **Size:** net lines in crates, python/gnomon and spec.

## Decision

Among arms passing measure 1: fewest crossings; within two crossings of the best, fewest side
effects, then fewest lines. Ties go to the smaller diff.

## Stopping rule

The winner gets one red team: a fresh Claude Haiku agent and Gemini (via the owner), each
given its diff and spec, each writing new two-world probes. A probe that reproduces is fixed,
and the fix is rerun on every test; then **one** more red team, the last. Whatever still
crosses after it becomes a stated limit in the spec, and the PR goes up.

## Log

- 2026-10-30: spec SC delivered (fresh Claude Opus, from `spec-prompt.md` only; 1,283 words,
  16 probes). Held outside the repo until the builds are done. sha256: `spec_SC.md`
  17eaf7935f13..., `probes_SC.py` 22872cc4f930..., builder's copy `spec_SC_rules.md` (rules and
  pitfalls only, the probe pointer cut) 1fa7d168316c...
- 2026-10-30: build SC delivered (fresh Claude Sonnet, from `spec_SC_rules.md` and main only),
  branch `arm48-SC`. Builder's report: all seven rules; 198 Rust and 299 Python tests pass;
  **one existing test edited** (`test_auto.py` test_8 renamed a spawner `claude-x` to
  `claudex`, which rule 3 forbids beside `claude`). Scored as a side effect: main's unchanged
  suite is run on every build too, and its failures are listed.
- 2026-10-30: Gemini (Argon) disconnected three times while thinking on `spec-prompt.md`, even
  when asked to answer across several messages; no spec. **Amendment (before any non-Claude
  spec exists):** arm SG is replaced by specs from other non-Claude families in the owner's
  second chat, same prompt unchanged: first **SK, Kimi K3.1 Pro** (Moonshot); optionally a
  second, **SO, GPT-5.4 mini** (OpenAI) or **SZ, GLM 5.2** (Zhipu). Chosen by family diversity,
  not by any measured strength (no trusted comparison of these versions was found); the
  bake-off's tests decide. Each gets its own fresh builder, as SC did. The red team's
  non-Claude member is whichever of them did not write the winning spec.
- 2026-10-30: two more non-Claude specs from the owner's second chat, same prompt unchanged
  (Kimi was unavailable, so Kimi is dropped):
  **SO, GPT (the full app model, not mini; OpenAI):** rules and pitfalls; its probes call two
  helpers it never defined (`_fresh_v7`, `_hidden_action`), so they cannot run as delivered.
  **SZ, GLM 5.2 (Zhipu):** spec and a runnable probe file, with API misuse (kind `bot`, models
  without `model`/`operator`, a `position` body missing fields). sha256 (12): `spec_SO_full.txt`
  be439e219d15, builder's `spec_SO_rules.md` 7287e2371c7e, `spec_SZ.md` ec1119ffdec8, builder's
  `spec_SZ_rules.md` 21876a839732, `probes_SZ.py` f421d79070a3. Held out until the builds are done.
- **Amendment (before any SO or SZ build, before any probe has run):** probe repair is done by
  a fresh Claude Haiku agent that has seen no build, from main's API only: API misuse fixed,
  SO's two helpers written from SO's own text, each change logged; it may not change what a
  probe tests. Every repaired probe is run on main first: a probe that does not cross on main
  (other than a declared limit, which should) tests nothing and is reported, not scored. SO's
  probes are labelled "completed by Claude" (weaker evidence than probes run as delivered).
- 2026-10-30, owner: Gemini Argon stays a reviewer (and for small questions), not a builder or
  long spec writer. **Amendment:** the red team's non-Claude member is Gemini Argon, given only
  the winning diff and its spec (a review-sized prompt).
- 2026-10-30: probes repaired by a fresh Claude Haiku agent (no build seen): SC 16, SZ 14, SO 11
  probes; 37 fixes logged (largest class: compartments and parties lowercased, as the gate
  requires). On main 35 cross; 6 do not and are **not scored**: SC `clock_far_future`, SZ
  `through_lease`, SO `operator`, `operator_datalog`, `through`, `failed_write_side_effect`
  (the SO operator setup never makes a B writer act for a hidden operator). Every limit probe
  crosses on main. Frozen, sha256 (12): `probes_SC.py` fb49c7b8b012, `probes_SZ.py`
  142531377ed6, `probes_SO.py` 51dbe1a58902, `FIXES.md` c2a8a835fc63, `run_all.py` 7462416c4e4c.
- 2026-10-30: build SZ delivered (fresh Claude Sonnet, `spec_SZ_rules.md` and main only), branch
  `arm48-SZ`: all eight rules; per-compartment clock with a whole-log `as_of` for v7 (which
  skips the writer_seq check and drops events that cannot apply); snapshot magic `GNSNAP5`.
  **One existing test changed** (`test_identity.ReviewM6.setUp` pinned to v6, as rule 1 refuses
  its owner-registered writer's spawn at v7). Scored as a side effect.
- 2026-10-30: build SO delivered (fresh Claude Sonnet, `spec_SO_rules.md` and main only), branch
  `arm48-SO`: generic `E_REGISTER` for non-owner duplicates; seq dropped from non-owner
  receipts; hidden leases neither block nor fence; clock a stated limit, no code. **One
  existing test changed** (`test_identity.ReviewM6` fixture pinned to v6). **Independence
  slip, self-reported:** a `ps` listing showed another arm's test text; the builder says it was
  not used. Recorded; not tested.
- 2026-10-30, **scoring rule set before any build's held-out results were read** (main's were):
  11 SC probes also return a `setup` record of what the hidden side's own calls returned, which
  is not what B observes, so it would count a leak on any build. A probe crosses if its two
  worlds differ **with the `setup` field removed**. On main this changes no probe's verdict.

## Results (`score.py` on `results/bakeoff/`; held-out probes run on every build)

| Arm | Held-out crossing (of 35) | run.py channels crossing | Own suites | Main's unchanged tests | Old logs | Lines R/Py/spec |
|---|---|---|---|---|---|---|
| main | 35 | all 9 | pass | pass | – | 0 |
| K combo3 (planner-led) | 22 | P14 R L C | pass | pass | same | 87/2/32 |
| SC (Claude spec) | 19 | P14 R C | pass | 1 error (spawner named `claude-x`) | same | 123/25/49 |
| SO (GPT spec) | 22 | P12 P12r P14 R C | pass | 3 errors (operator rule) | same | 108/0/36 |
| **SZ (GLM 5.2 spec)** | **18** | P12 P12r P14 R V | pass | 3 errors (operator rule) | same | 149/0/40 |

- Every arm passes measure 1 (own suites, replay).
- **Measure 3 (side effects) is uninformative:** 19-20 for every arm, almost all from the
  protocol bump itself (`schema_version`, hashes, digests). It cannot separate the arms.
- **Decision by the rule:** SZ has the fewest crossings (18); SC is within two (19). The next
  tie-breaks (side effects, then lines) both favour SZ (19 vs 20; 189 vs 197 lines), so the
  verdict does not hinge on the noisy measure alone. **SZ goes to the red team.** One probe
  apart is a direction, not a clear win.
- Where SZ and SC differ: SZ closes the clock, `as_of`, the name channel and leases advancing
  the clock; SC closes writer ids (three probes) and lease-release guessing. Every arm leaks
  the count family (event ids, view and write sequences).

## Red team 1 (on SZ)

**Claude Haiku (fresh; given only SZ's spec and diff; ran its probes on the build):** five
findings, each **reproduced by the planner** (`redteam_haiku.py`, output `redteam_haiku_SZ.txt`):
1. The clock is keyed by the writer's compartments, so a two-compartment writer's A write
   blocks a different, B-only writer (`E_CLOCK`): channel C is not closed after all.
2. A write into a frame leased by a hidden writer: `E_LEASE` names the holder and expiry.
3. Naming a hidden lease: `E_LEASE ... held by 'h'` vs `E_REF` (existence and holder).
4. A revoke of a hidden compartment moves a B reader's `through` (follows SZ's own rule 6).
5. **Correctness:** a write dated before its writer's registration is accepted; `as_of` then
   drops it (the full view has it).
Verdict: do not merge as is. Gemini Argon's red team is pending.

**Gemini Argon (given only SZ's spec and diff; verbatim in `REVIEWS.md`):** four findings, each
**reproduced by the planner** (`redteam_argon.py`, written from Argon's exact steps; ids changed
from `_` to `-`, which the gate requires; output `redteam_argon_SZ.txt`):
1. Clock: the same as Haiku's 1 (`commit` advances every compartment of the writer).
2. Leases: an owner (in A and B) leasing in A blocks a B lease on the same frame name
   (`E_LEASED`), because the conflict scope counts the grantor; the lease also moves `through`.
3. Revoke: the same as Haiku's 4. Its second part (revoking a writer's only compartment hides it
   from that compartment's readers) **behaves the same on main**: existing behaviour from #49,
   out of scope here, filed separately.
4. `as_of`: a write dated before its writer's registration is accepted and then dropped (on
   main the global clock refuses it). The same root as Haiku's 5.
Verdict: do not merge, or merge with changes.

## Fix round (registered before any fix is built)

Three attempts at per-compartment clocks (T1, then SZ twice) each produced a correctness bug.
So the fix round adds an exact guard and a fallback.

Fixes on SZ (branch `arm48-SZ2` from `arm48-SZ`):
- **F1, clock:** an event advances only the compartments it touches (its own compartment; a
  registration, the new writer's; a lease, release or revoke, the lessee's or target's). The
  floor for a write is the maximum over the compartments it touches **and** the writer's own last
  event, its registration included (a write may not be dated before its writer exists).
  `as_of` must never drop an event silently: an event that cannot apply is an error.
- **F2, lease messages:** `E_LEASE` names a holder only if visible to the writer; a named lease
  the writer cannot see answers as a missing one (`E_REF`).
- **F3, lease scope:** a live lease is "visible" to a writer iff its **lessee** shares a
  compartment with it (the grantor no longer counts), for conflicts, fencing and `through`.
- **F4, revoke `through`:** a revoke counts for a reader iff its revoked compartments meet the
  reader's.

Measures: everything in the bake-off (held-out probes, earlier checks, suites, main's tests,
replay), both red teams' probes, and **an `as_of` property test**: 2,000 random valid logs
(seeded; 2-4 compartments, 3-6 writers, mixed kinds and out-of-order times), checking for every t
that `as_of(t)` equals the state built from exactly the events dated at or before t, in log
order, with no error.

Decision: SZ2 goes to red team 2 (the last) if the property test passes, every red-team probe
1-5 and Argon 1, 2, 4 is closed, and nothing else gets worse. **Fallback:** if the property test
fails, or F1 adds more than 150 net lines, the clock goes back to the global one (channel C a
stated limit, as SC and combo3 have) and the bake-off is re-scored on that basis.
- `as_of` property test written by a fresh Claude Sonnet (main's API only; the fixer never sees
  it), `asof_prop.py`, sha256 bd198718a52c, frozen before SZ2 exists. Validated: **main** 2,000 logs,
  88,249 checks, **0 failures** (the global clock refuses every out-of-order write); **SZ**
  2,000 logs, **44 failures in 5 logs**, every one a release that depends on a later-dated
  lease or registration (rebuild refused: E_CLOCK 34, others 10). It compares the whole
  `["state"]`, removing nothing.
