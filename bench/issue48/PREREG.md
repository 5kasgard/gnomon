# Issue #48: which fixes close the four side channels between compartments?

Pre-registered 2026-10-29, before any arm is built or run. Amend only before data exists.
Cost bound: $0 API (Claude sub-agents on the owner's plan; no model is called by the harness).

## Reproduction on main (2026-10-29, commit 629d7e1, before this file)

The archived probes (`polymechania/bench/archive/map/probes.py`) rerun against current main:
P10, P11, P12 and P14 still cross; P13 stays sealed. #57, #60 and #61 changed none of them.

## Question

Information still crosses between writers who share no compartment:

- **P10 operator:** a B-only reader sees `operator='secretop'` of a B writer whose operator
  exists only in A (in `writers()` and as a Datalog `identity` fact).
- **P11 session ids:** two spawners of one party, in A and in B, signal bits by choosing session
  ids; B decodes them from `E_IDENTITY` refusals.
- **P12 writer ids:** parent `c` (A) signals parent `c-x` (B) by registering `c-x-<i>`; B decodes
  from `E_DUPLICATE` refusals.
- **P14 sequence gaps:** a B-only reader sees `x/1, x/5` and so counts x's hidden events in A.
  Documented today as a known limit (spec section 5).

For each, which fix closes it at least cost to existing use and to the protocol?

## Prior (frozen before our data)

The same problem in multilevel-secure databases: a uniqueness constraint checked across
levels is a covert channel, because a refusal tells the low writer that a high row exists. The
standard remedy is to scope the key per level ("polyinstantiation": Denning et al., SeaView,
1987; Jajodia and Sandhu, IEEE S&P 1990), at the cost of the same key meaning different things
at different levels. So the prior favours scoping (S1) and disjoint namespaces (I1, I2) over
refusing with a uniform message, which cannot help when the sender chooses the colliding name.

## Arms (each built on its own branch from main; each is one small change)

- **0, main:** baseline; P10, P11, P12, P14 should cross.
- **S1, session check scoped to shared compartments:** the duplicate-session refusal considers
  only writers that share a compartment with the one being registered. (P11)
- **I1, disjoint namespaces:** an instance's id must start `<parent>-` and must not start
  `<w>-` for any other writer `w` that holds `spawn` (the longest owning prefix wins), so no two
  spawners can ever name the same id. (P12)
- **I2, gate-assigned instance ids:** an instance's id must be exactly `<parent>-<n>`, n being one
  more than the number of instances that parent has registered; the sender chooses nothing. (P12)
- **O1, operator shown only if visible:** views (`writers`, state, Datalog facts) leave out a
  writer's operator when the operator shares no compartment with the reader. (P10)
- **O2, operator must be on hand:** the gate refuses an instance whose operator does not hold
  every compartment the instance holds. (P10)
- **Q1, per-compartment numbering:** a writer's events are numbered per compartment, so a
  reader's view has no gaps. (P14) The alternative, **Q0**, is the documented limit as today.

The builder may find an arm cannot be built as written; it is then reported as not buildable,
with the reason, never dropped silently or quietly redefined.

## Measures (all by script)

1. **Closes:** the arm's target probe no longer crosses (P10, P11, P12, P14 as in the archive).
2. **No regressions:** every other probe in `probes.py` (P1-P22) gives the same `crosses` as on
   main.
3. **Existing suites:** `cargo test --release` and `python3 -m unittest discover -s python/tests`
   pass unchanged (count of failures reported).
4. **Old logs:** every log under `tests/vectors` and every log the suites build on main replays
   on the arm to an identical state hash. If not, the arm needs a protocol bump; reported.
5. **Intended rule still holds** (one per arm, defined here):
   - S1: two writers of one party and surface registering one session in the **same**
     compartment: the second is still refused.
   - I1, I2: a spawner can still register instances, and the hook's per-session instances
     (`python/gnomon/hook.py`, `mcp.py --per-session`) still start.
   - O1: a reader who shares a compartment with the operator still sees it.
   - O2: an owner-registered writer with an operator in all its compartments is accepted.
   - Q1: citations, supersessions and handoffs across a writer's events still resolve.
6. **Static existence probe (reported, not decisive):** can a spawner learn that a hidden writer
   exists, from any refusal, by naming it? (Not a signalling channel, as the receiver does not
   modulate it; recorded for each arm.)
7. **Size:** net lines added (Rust, Python, spec, tests separately).

## Decision (per channel)

An arm qualifies if measures 1-5 hold (4 may hold with a protocol bump, counted as a cost).
Among qualifying arms for a channel, prefer no protocol bump, then fewest net lines.
For P14: Q1 replaces the documented limit only if it qualifies and adds at most 300 net lines;
otherwise the limit stays and is re-stated plainly in the spec.
If no arm qualifies for a channel, every arm goes to the owner with its costs.
The chosen arms are then combined on one branch, and measures 1-5 are rerun on the combination
before any PR.

## Review plan

Before the build: a separate Claude Haiku agent reviews this file (verbatim notes kept).
After the results: a non-Claude reviewer (Gemini via the owner, or the door's free models)
reviews the chosen diff and the results, as for #51.

## Limits stated in advance

- Probes use the library directly; someone with file access bypasses the gate entirely (#50).
- Bandwidth is not measured, only whether a bit crosses.
- The author of the arms and the probes is the same planner (Claude Opus); the builder is a
  Claude Sonnet sub-agent. Reviews from outside the family are the check on that.
