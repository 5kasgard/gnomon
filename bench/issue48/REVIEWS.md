# Reviews of the #48 test

## Pre-registration, Claude Haiku (separate agent), 2026-10-29, verbatim

1. Channels missed. I checked each by running the native library in a scratch directory, not in the repo.
- a. Write return. A process writing as shared writer x in B gets back `x/5` after four A-only events, so the count leaks to any B-side process writing as x. P14 checks views only.
- b. Existence. Naming hidden `wa` gives E_COMPARTMENT; a missing name gives E_UNKNOWN_WRITER. An A-side spawn flips that bit. Measure 6 wrongly calls this non-signalling, and no arm closes it.
- c. `through`. Kinds `lease`, `release`, `revoke` and `schema_change` fall to `_ => true` in query.rs `event_visible`. An A-only lease appears in B's query as `s/1` with its time. P15 has no lease in its base.
- d. E_CLOCK. A later A-only event makes B's earlier-dated write fail. This is a timing channel.

2. Arm by arm.
- S1 closes P11. The same session may now sit on two writers.
- I1 passes P12 only because the probe registers both spawners first. Reverse the order: c takes `c-x-0` before c-x exists, then c-x's own id gets E_DUPLICATE.
- I2 cannot be built as written. The sender must learn n from a refusal or a return, and that is the count. The probe's A-side register is not wrapped in code(), so any refusal crashes it.
- O1 covers P10's views and Datalog only.
- O2 as written covers instances only, so P10's owner-registered bot still crosses. Applied to all writers, P10 crashes (crosses=None), and its refusal could name the operator's compartments.
- Q1 fixes view gaps, not returns, error texts or `through`. It is also a protocol bump.

3. Loose measures.
- P14 returns True and P13 returns False unconditionally. Q1 can never pass P14.
- Crosses is judged on one world, so a changed return or error text still passes.
- Measure 1 counts a crash as closed.
- P15 markers miss bare writer names, operators, sessions and leases.
- The decision rule ranks arms on the target probe only.

4. Before any build.
- Make crosses a two-world test: run each probe with and without the hidden A event. Any difference in B's full output counts (returns, codes, text, views, `through`). Exceptions count as not closed.
- Add probes for a to d, the reversed I1 order, and I2's gap count.
- Use one refusal for "not visible" and "does not exist" on every writer-naming path, as P8 already does for bindings.
- Q1 must cover event ids, returns, seq errors and `through`, or be dropped as a P14 fix.
- Give the `_ => true` kinds their own arm.
- State O2 for all writers, and have P10 expect the refusal code.

Handling (checked by `review_checks.py` and two ad hoc runs on main, 629d7e1):

- a, c, d: reproduced exactly (two worlds differ). Added as channels R, V, C with arms.
- b: not on the path named. Bindings, claim speakers, spawn ids and operators answer hidden
  and missing alike. It does reproduce on the **lease** path: a B scheduler's lease to a
  hidden A writer is accepted, to a missing one refused; `E_LEASED` names an A lease's holder.
  Added as channel L with arm L1.
- S1 allowing one session on two writers in different compartments: accepted as the arm's
  cost; measure 5 keeps the same-compartment rule.
- I1 reversed order: correct; P12r added.
- I2 unbuildable: disputed (the parent sees its own instances); built as written.
- O2, Q1, two-world measure, exceptions, P13/P14 fixed returns: all applied in amendment 1.
- P15 markers: not changed; the two-world measure replaces marker matching for the new channels.

## arm48-combo2, Gemini (Argon, thinking high; non-Claude, via the owner's chat), 2026-10-30, verbatim

 * Untested leaks in touched paths:
   * P12r regression in I2b (register): In ledger.rs, st.writers.contains_key(r.writer) runs before r.writer != "{writer}-{k}", after checking only the "{writer}-" prefix. When hidden spawner c-x (in A) registers c-x-1, spawner c (in B) registering c-x-1 passes the prefix check and gets E_DUPLICATE if c-x-1 exists, versus E_SPAWN if not.
   * T1 as_of cross-compartment blinding: Because as_of stops at the first event dated after the cut, an A-only event with a future record_time truncates a B-only reader's as_of(now) replay, hiding subsequent B events dated before now.
   * T1 clock bleed (check, touched): floor_v7 takes the maximum across all of w.compartments, not the event's target compartment. An A-only write advances clock["A"], making a {A, B} writer's B-write fail with E_CLOCK. Similarly, touched on lease, release, and revoke updates all compartments of the target writer, and any event kind omitted from touched hits _, advancing the global "" clock checked by everyone.
   * V1 multi-compartment and release leaks (event_visible): If writer w holds {A, B}, A-only lease, revoke, or register events on w still shift a B-only reader's through. Also, if commit removes released leases from st.leases, st.leases.get(field("lease")) returns None, hiding releases everywhere.
 * Diff bugs and risks:
   * Broken instance_for under restricted views (session.py): The gate computes k from global st.writers, whereas instance_for reads caller-visible writers. If c-1 is owner-registered in hidden compartment A, a B-restricted caller never sees c-1, computes k=1 (c-1), and fails with E_DUPLICATE (with the retry loop removed). instance_for also applies v7 k-counting unconditionally to v6 logs.
   * as_of truncation: Prefix-stopping breaks as_of once timestamps are ordered only within compartments.
 * Experimental flaws:
   * Masking global seq: The builder found write returns leak global seq (total log events) and excluded it from the comparator so arms could pass.
   * Inconsistent test rules: Rejecting Q1 (2 failures) and O2 (3 failures) because existing tests hardcoded leaky expectations—while updating 2 tests in combo2 for I2b/G—is inconsistent.
   * Dropping L1: Discarding L1 left E_UNKNOWN_WRITER existence probing and E_LEASED holder/expiry leaks unpatched despite zero test failures.
   * Unrestricted f1: f1 tested instance_for on an unrestricted Memory, masking the restricted-view bug.
 * Keeping P14/R and deferring L:
   * Not defensible. Global seq reveals exact hidden write volume on every write, and fixing E_UNKNOWN_WRITER plus E_LEASED metadata requires no owner decision.
   * Instead: Merge L1 now and key leases by (compartment, frame); strip global seq from restricted returns; and adopt Q1 (updating the two hardcoded test assertions).
 * Verdict: Merge with changes:
   * Require r.writer to match "{writer}-{n}" (integer n, no extra hyphens) before checking E_DUPLICATE.
   * Scope floor_v7 to the event's target compartment(s), and filter as_of by timestamp rather than stopping at the first out-of-order event.
   * Include L1, strip global seq from restricted returns, and increment k on E_DUPLICATE retries in instance_for.

Handling (each claim run two-world by `gemini_checks.py`, on main and on arm48-combo2;
outputs `gemini_checks_main.txt`, `gemini_checks_combo2.txt`):

- **P12r variant (G1): reproduces.** `c` asking for `c-x-1` gets `E_DUPLICATE` or `E_SPAWN`
  depending on a hidden spawn; I2b does not close it. Fix accepted (integer suffix first).
- **as_of (G2): reproduces, and is worse than stated.** Under T1 an A event dated later cuts
  B's `as_of` view short, for every reader (a correctness bug, not only a leak). On main the
  same scenario crosses through `E_CLOCK` instead. So T1 moves channel C into `as_of`.
- **Clock bleed via leases (G3a): reproduces,** on main and under T1 alike. With G2, T1 does
  not close C. The `_` fall-through is `schema_change` only: owner-only and global by design.
  A two-compartment writer blocked by its own A write (G3b) reproduces, but that writer holds
  A; it is the R family (a B-side process acting as a shared writer).
- **V1 and a writer in A and B (G4): reproduces;** it comes from leases having no compartment
  (channel L). **Releases hidden everywhere: does not reproduce** (released leases are kept,
  marked ended; G4r).
- **instance_for under restricted views (G5): does not reproduce.** `instance_for` reads
  `memory.writers()` unrestricted, as the hook and MCP server call it; with a hidden
  owner-registered `c-1` it starts `c-2` and succeeds. The retry on `E_DUPLICATE` is still in
  place. The id differs only by what an owner registered (owner as sender). On version-6 logs
  the k it computes is accepted by the old rule (any free id).
- **Global seq in returns (G6): reproduces** (seq 6 vs 3 for a B-only writer). Excluded from
  the harness comparison openly in the builder's report and stated in the spec's limit; fair
  that it is stronger than R (any writer, not only a shared one).
- **Inconsistent test rules: partly.** The two tests changed in combo2 are the experiment's own
  new arm tests (`test_issue_48_i2.py`, `test_issue_48_o1.py`); O2 and Q1 broke pre-existing
  tests, which measure 3 holds unchanged. Whether those pre-existing tests only pin leaky
  expectations was not checked; noted.
- **L1: fair.** It closes the existence half with no failures; the rule required the whole
  channel. Taken in follow-up 3 (below).
- **Count family (P14, R, seq) as a limit: disputed, recorded.** Counts carry no content; Q1
  does not cover seq, and hiding seq means changing every restricted view. Kept as a limit,
  open to the owner.

## Bake-off red team 1 on arm48-SZ, Gemini (Argon; non-Claude, via the owner's chat), 2026-10-30, verbatim

* Clock leak via multi-compartment writers (commit updates all writer compartments, not touched compartments)
   * Setup: Register humans w_ab (party "p", compartments ["a", "b"], capabilities ["write"]) and w_b (party "p", compartments ["b"], capabilities ["write"]) at 2026-01-01T00:00:00Z.
   * Hidden A action: m.remember("w_ab", claim("s", "w_ab", "f", "a"), record_time="2026-01-01T02:00:00Z").
   * B call: m.remember("w_b", claim("s2", "w_b", "f2", "b"), record_time="2026-01-01T01:00:00Z").
   * Result: World 1 advances compartment_clocks["b"] to 02:00 (because w_ab is in "b") and fails with E_CLOCK; World 2 succeeds.
 * Lease and through leak via multi-compartment grantor (live_lease and event_visible check l.by)
   * Setup: owner (human, party "p", compartments ["a", "b"]) registers humans w_a (party "pa", compartments ["a"]), w_b (party "pb", compartments ["b"], capabilities ["schedule"]), and w_b2 (party "pb", compartments ["b"]) at 2026-01-01T00:00:00Z.
   * Hidden A action: m.lease("owner", "w_a", "f", "2026-01-02T00:00:00Z", record_time="2026-01-01T01:00:00Z").
   * B call: m.lease("w_b", "w_b2", "f", "2026-01-02T00:00:00Z", record_time="2026-01-01T01:00:00Z") and m.query("w_b", "...").
   * Result: Because l.by (owner) is in "b", World 1 refuses m.lease with E_LEASED and advances query.through to the A-lease event; World 2 succeeds and keeps through at w_b2's registration.
 * revoke leak and correctness bug (event_visible checks current st.writers compartments)
   * Setup: owner (compartments ["a", "b"]) registers humans w_ab (party "p", compartments ["a", "b"]) and w_b (party "pb", compartments ["b"]) at 2026-01-01T00:00:00Z.
   * Hidden A action: m.revoke("owner", "w_ab", ["a"], "secret").
   * B call: m.query("w_b", "...") and m.state(compartments=["b"]).
   * Result: Because w_ab remains in "b", World 1 advances through to the A-revoke and exposes it in state; World 2 does not.
   * Correctness bug: Revoking ["b"] from "b"-only w_b empties w_b.compartments, making event_visible hide w_b's register and revoke events from "b" readers.
 * as_of / apply_subset silently drops valid events (Correctness bug and leak)
   * Setup: owner (human, party "p", compartments ["a"]) at 2026-01-01T00:00:00Z.
   * Hidden A action: owner writes in "a" at 2026-01-01T10:00:00Z, then registers human w_b (party "pb", compartments ["b"], capabilities ["write"]) at 2026-01-01T10:00:00Z (World 2 registers w_b at 2026-01-01T01:00:00Z). compartment_clocks["b"] stays unset because owner is only in "a".
   * B call: m.remember("w_b", claim("x", "w_b", "f", "b"), record_time="2026-01-01T08:00:00Z"), then m.state(as_of="2026-01-01T09:00:00Z", compartments=["b"]).
   * Result: World 1 skips w_b's 10:00 registration during as_of, so apply_subset fails check() (E_UNKNOWN_WRITER) and drops the 08:00 claim; World 2 returns it.
do not merge (or merge with changes: track/check clocks per event-affected compartments including newly registered writers, check revoke event compartments rather than post-state writer compartments, and avoid scoping leases to multi-compartment grantors when the lessee is disjoint).
