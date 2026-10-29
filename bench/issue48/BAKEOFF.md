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
