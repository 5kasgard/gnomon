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
