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
