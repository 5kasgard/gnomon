You are an independent reviewer of a security fix and the experiment that chose it. Be critical; you are not asked to agree.

Context: Gnomon is a hash-chained memory log (Rust core, Python bindings). Writers hold "compartments"; a reader sees only events in compartments it holds. Issue #48: information still crosses between writers who share no compartment, through side channels (refusal codes on chosen names, operator names in views, event-count gaps, leases, a query's "through" field, the clock rule). Several fixes were tested on a two-world harness (same script with and without a hidden action in compartment A; any difference in what the B side gets back = crosses). Chosen: S1, I2b, O1, V1, T1, with G (O1, V1, S1 apply to every log version). P14/R (event-count gaps) is kept as a documented limit; L (leases on a frame name) goes to the owner. Below: the pre-registration, the results and decisions, then the code diff (tests omitted).

Answer in under 450 words, as a numbered list:
1. Is there a way information still crosses between compartments through the paths these fixes touch (registration, operator, session ids, instance ids, lease/release/revoke visibility, the clock rule) that the tested channels miss? Be concrete: which call, which field or refusal.
2. Bugs or risks in the diff: races, upgrades from older protocol versions, replay of old logs, snapshot compatibility (T1 changes the snapshot magic), the as_of side effect of T1, error handling.
3. Is anything in the experiment unfair, or does any decision not follow from the data (including the follow-ups registered after results)?
4. Is keeping P14/R as a documented limit, and sending L to the owner, defensible? What would you do instead?
5. Verdict: merge / merge with changes (which) / do not merge.

===== PREREG.md =====
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

## Amendment 1 (2026-10-29, before any arm is built or run)

From a review by a separate Claude Haiku agent (verbatim in `REVIEWS.md`), each claim checked
by `review_checks.py` before acting. Changes:

- **Two-world measure.** Every channel is now tested by running the same script twice, with
  and without the hidden A-side action, and comparing everything the B side gets back
  (returns, refusal codes and text, views, query `through`). Any difference = crosses. An
  exception in a probe counts as not closed. The old P13 and P14 returned fixed values; both
  are rewritten this way. Measure 2 covers P1-P23 (there are 23 probes, not 22).
- **New channels** (all confirmed by `review_checks.py` on main):
  - **R, write returns:** a shared writer's B-side write returns `x/4` after three A events,
    `x/1` without. Same root as P14; Q1 must close both or it does not qualify.
  - **L, leases:** a B-side scheduler leasing to a hidden A writer is accepted, to a missing
    one refused (`E_UNKNOWN_WRITER`), so an A-side spawn signals a bit; and `E_LEASED` names an
    A-side lease's holder and expiry. (The reviewer's own example, bindings, did not reproduce:
    bindings already answer hidden and missing alike, P8.)
  - **V, `through`:** `lease`, `release`, `revoke` and `schema_change` are visible to every
    reader (`query.rs` `event_visible`, `_ => true`), so a B query's `through` shows an A-only
    lease event, its id and time.
  - **C, clock:** an A-only event with a later time makes a B write at an earlier time fail
    with `E_CLOCK`.
  - **P12r:** P12 with the order reversed (c takes `c-x-0` before `c-x` exists).
- **New arms:**
  - **L1:** a lease's target must share a compartment with the scheduler, with one refusal
    for hidden and missing; `E_LEASED` names neither holder nor expiry when the holder is
    hidden from the scheduler.
  - **V1:** lease and release are visible to a reader iff the lease's target shares a
    compartment with the reader; revoke iff its target does; schema_change to all (a global,
    owner-made protocol event).
  - **T1:** the clock rule compares a new event's time only with events the writer can see
    (its compartments). **T0:** the rule as today, stated as a known limit.
- **O2** is restated for every registration (owner's too); P10 under O2 expects the refusal,
  and the refusal must not name the operator's compartments.
- **I2:** the reviewer judged it unbuildable (the sender must learn n). Disputed, not acted on:
  n counts the parent's own instances, which the parent can see. Built as written; if the
  builder finds otherwise, it is reported as not buildable.
- **Q1** must make event ids, write returns and `E_WRITER_SEQ` messages per compartment, or
  it does not qualify for P14 and R.
- The decision rule is unchanged, applied per channel (P10, P11, P12 with P12r, P14 with R, L,
  V, C). A combination is chosen only from qualifying arms and rerun on all channels together.

===== RESULTS.md =====
# Issue #48: results

Test: `PREREG.md` with amendment 1. Harness (`run.py`) and arms built by a Claude Sonnet
sub-agent, each arm on its own branch (`arm48-<arm>`). Every branch rerun independently by the
planner (Claude Opus) in fresh worktrees: identical on every channel, probe, suite, replay and
rule measure (`collect.py` compares the two runs; the only differences are the branch name and
two main-only bookkeeping fields under a detached checkout, and the bench line count, which
counts the committed replay logs). No model was called; $0.

## Per arm (two-world channels; X = crosses)

| Arm | P10 P11 P12 P12r P14 R L V C | Own rule holds | Probe changes vs main | Suites fail (cargo/python) | Old logs | Protocol bump | Net lines R/Py/spec/tests |
|---|---|---|---|---|---|---|---|
| 0 (main) | X X X X X X X X X | – | – | 0/0 | – | – | 0 |
| S1 | X **.** X X X X X X X | yes | P11 | 0/0 | same | no | 4/0/0/68 |
| I1 | X X **.** X X X X X X | yes | P12 (probe errors) | 0/0 | same | 7 | 22/0/3/85 |
| I2 | X . **. .** X X X X X | yes | P11, P12 (probes error) | 0/0 | same | 7 | 18/4/2/93 |
| O1 | **.** X X X X X X X X | yes | P10 | 0/0 | same | 7 | 17/0/2/73 |
| O2 | **.** X X X X X X X X | yes | P10 (probe errors) | 0/**3** | same | 7 | 13/0/2/74 |
| L1 | X X X X X X **X** X X | yes | – | 0/0 | same | 7 | 15/0/3/77 |
| V1 | X X X X X X X **.** X | yes | – | 0/0 | same | no | 14/0/4/71 |
| T1 | X X X X X X X X **.** | yes | – | 0/0 | same | 7 | 56/0/8/99 |
| Q1 | X X X X **. .** X X X | yes | – | 0/**2** | same | 7 | 66/0/8/91 |

Main crosses on every channel and a negative control (an A-only claim by an A-only writer)
does not, so each scenario tests the road it names.

Notes on the data:
- **I2 and P11:** I2's P11 "closes" only because P11's sender uses ids I2 forbids, so the
  sender's step is refused in both worlds. With legal ids P11 still crosses under I2 (builder's
  check). Not counted as closing P11.
- **"Probe errors"** (I1, I2, O2): the archived probes call the refused step without catching
  it. The two-world channels in `run.py` catch every refusal and are what the decision uses.
- **O2** breaks three identity tests that register an operator in fewer compartments than its
  writer, a use the suite treats as normal.
- **Q1** breaks two tests that build event ids by hand; and registrations, leases, releases,
  revokes and schema changes still share one counter, so some gaps survive.
- **L1** closes the existence half of L but not the other: a hidden live lease on a frame name
  still makes a B scheduler's lease on that frame fail. Closing it needs leases per compartment
  or refusing every lease on such a frame: a design change.
- **T1** side effect: an `as_of` view can leave out an event appended later with an earlier
  time. A test pins it; the spec states it.
- **Not in the plan, found by the builder:** a returned event's global `seq` counts hidden
  events (same family as R). Excluded from the comparison; not fixed by any arm.

## Decision (by the pre-registered rule)

| Channel | Qualifying arms | Chosen | Why |
|---|---|---|---|
| P10 operator | O1 | **O1** | O2 fails 3 existing tests |
| P11 session ids | S1 | **S1** | no bump, 4 lines |
| P12, P12r writer ids | I2 | **I2** | I1 leaves P12r open |
| P14, R sequence gaps | none | **limit stays** | Q1 fails 2 existing tests; spec to restate the limit plainly, now including write returns and `seq` |
| L leases | none | **to the owner** | L1 closes only half; the rest is a design change |
| V `through` | V1 | **V1** | no bump |
| C clock | T1 | **T1** | the only arm |

Next, as pre-registered: S1 + I2 + O1 + V1 + T1 combined on one branch (one protocol bump),
every measure rerun on the combination, then a non-Claude review before any PR.

Version number: the arms bump to protocol 7. `docs/gnomon-50-options.md` in Polymechania
planned protocol 7 for signed appends (#50, waiting on the owner); whichever lands second
takes 8.

## Follow-ups I2b and G (registered after the combination's results, before either is built)

The planner's review of `arm48-combo` (Claude Opus, reading the diff and testing it) found:

1. **I2 can block a parent for good.** If any writer already holds `<parent>-<n>` (only an
   owner can register such an id; it may sit in a compartment the parent cannot see), the gate
   demands exactly that id, refuses it (`E_DUPLICATE`, which also tells the parent the hidden
   writer exists), and refuses every other id (`E_SPAWN`). Tested: `c` can then start no
   instance at all. The pre-registered rule check (measure 5) used a clean log only.
2. **I2 breaks the race retry.** Two sessions starting at once both compute `<parent>-<n>`; the
   loser now gets `E_SPAWN` (the I2 check runs before the duplicate check), but `instance_for`
   retries only on `E_DUPLICATE`, so that session does not start. Tested.
3. **Gating that weakens the fix.** O1 and V1 change only what views show, and S1 only relaxes
   a refusal; none can change how an old log replays. Gating them on version 7 leaves every
   existing log leaking on P10, V and P11 until it is upgraded.

Arms:

- **I2b:** I2, except (a) the duplicate check runs before the I2 rule, so a race loser gets
  `E_DUPLICATE` and retries; (b) the required id is `<parent>-<k>`, k the smallest number above
  the count of the parent's instances whose id is free. Skipping depends only on what owners
  registered, which is not a channel a non-owner can modulate. `instance_for` computes the same k.
- **G:** O1, V1 and S1 apply to every log, whatever its version; I2b and T1 stay gated.

Measures: every measure of the combination (channels, P1-P23, suites, replay of old logs,
own rules), plus **f1**: with an owner-registered `c-1` hidden from `c`, `instance_for(c)`
starts an instance; **f2**: two `Memory` objects on one log both start an instance of the same
parent, the second having read the writers before the first registered, and both succeed;
**f3**: on a version-6 log, P10, V and P11 do not cross under G.

Decision: the combination takes I2b if f1, f2 and every combination measure hold; it takes G
if f3 and every combination measure hold (replay identical included). Otherwise the
combination stays as it is and the failing follow-up is reported.

## Follow-up results (branch `arm48-combo2`; built by Claude Sonnet, rerun by Claude Opus: identical)

| | Channels closed | Still cross | P1-P23 changes | Suites fail (cargo/python) | Old logs | f1 | f2 | f3 |
|---|---|---|---|---|---|---|---|---|
| combo (S1+I2+O1+V1+T1) | P10 P11 P12 P12r V C | P14 R L | P10, P11, P12 | 0/0 | same | – | – | – |
| combo2 (+I2b, +G) | P10 P11 P12 P12r V C | P14 R L | P10, P11, P12 | 0/0 | same | pass | pass | pass |

I2b alone also replays old logs identically. Net lines of combo2 over issue-48: Rust +117,
Python +2, spec +28, tests +528. By the follow-up rule the combination takes **I2b** and **G**.

Builder's notes, kept: the duplicate check runs first only on version-7 logs; a duplicate
refusal can still tell a parent that an owner-registered writer holds some id (static, not
modulated by a non-owner); two arm tests were updated because I2b and G change what they pinned.

===== code diff (crates, python/gnomon, spec) =====
diff --git a/crates/gnomon-core/src/ledger.rs b/crates/gnomon-core/src/ledger.rs
index 05b7d10..0327caa 100644
--- a/crates/gnomon-core/src/ledger.rs
+++ b/crates/gnomon-core/src/ledger.rs
@@ -312,6 +312,10 @@ pub struct State {
     /// Leases by event id (protocol version 6).
     pub leases: BTreeMap<String, LeaseInfo>,
     pub schema_version: i64,
+    /// Latest record time of any event touching each compartment, and under
+    /// "" of any global event (protocol version 7, issue #48, T1). Derived,
+    /// like `successors`, and not part of the state digest.
+    pub clock: BTreeMap<String, String>,
 }
 
 /// What a caller submits; the ledger assigns sequence numbers and hashes.
@@ -456,7 +460,16 @@ impl Ledger {
             self.events.last(),
             computed,
         )?;
+        let touched = touched(&self.state, &env);
         commit(&mut self.state, checked, self.events.len());
+        if let Some(rt) = env.get("record_time").and_then(|v| v.as_str()) {
+            for c in touched {
+                let e = self.state.clock.entry(c).or_default();
+                if e.as_str() < rt {
+                    *e = rt.to_string();
+                }
+            }
+        }
         self.events.push(env);
         Ok(())
     }
@@ -815,6 +828,35 @@ fn visible_item_compartment<'a>(st: &'a State, w: &WriterInfo, id: &str) -> Gate
         .ok_or_else(|| not_found("evidence", id))
 }
 
+/// The compartments an event touches, for the clock (issue #48, T1); "" for a
+/// global event. Worked out from the state before the event is applied.
+fn touched(st: &State, env: &Value) -> Vec<String> {
+    let body = env.get("body");
+    let text = |k: &str| body.and_then(|b| b.get(k)).and_then(|v| v.as_str()).unwrap_or("");
+    let of_writer = |w: &str| st.writers.get(w).map(|x| x.compartments.clone()).unwrap_or_default();
+    let one = |c: Option<&str>| c.map(|c| vec![c.to_string()]).unwrap_or_default();
+    match env.get("kind").and_then(|v| v.as_str()).unwrap_or("") {
+        "claim" | "episode" | "handoff" | "position" | "appoint" => one(Some(text("compartment"))),
+        "verdict" | "appeal" => one(st.claims.get(text("claim")).map(|c| c.compartment.as_str())),
+        "supersede" => one(st.claims.get(text("old")).map(|c| c.compartment.as_str())),
+        "resolve" => one(
+            st.supersessions
+                .get(text("dispute"))
+                .map(|x| x.compartment.as_str())
+                .or_else(|| st.appeals.get(text("dispute")).map(|x| x.compartment.as_str())),
+        ),
+        "dismiss" => one(st.appointments.get(text("appointment")).map(|x| x.compartment.as_str())),
+        "register" => body
+            .and_then(|b| b.get("compartments"))
+            .and_then(|v| v.as_array())
+            .map(|a| a.iter().filter_map(|c| c.as_str().map(String::from)).collect())
+            .unwrap_or_default(),
+        "lease" | "revoke" => of_writer(text("writer")),
+        "release" => st.leases.get(text("lease")).map(|l| of_writer(&l.writer)).unwrap_or_default(),
+        _ => vec![String::new()],
+    }
+}
+
 fn commit(st: &mut State, checked: Checked, seq: usize) {
     match checked.change {
         Change::Register {
@@ -1232,10 +1274,23 @@ fn check(
             "record_time must be a real YYYY-MM-DDTHH:MM:SSZ time",
         ));
     }
-    if let Some(prev_rt) = last
-        .and_then(|e| e.get("record_time"))
-        .and_then(|v| v.as_str())
-    {
+    // From version 7 (issue #48, T1) the rule compares only with events the
+    // writer can see: those touching its compartments, and global ones. An
+    // event in a compartment it is not in cannot make its write fail.
+    let floor_v7;
+    let floor = if st.schema_version >= 7 && expected_seq > 0 {
+        let comps = st.writers.get(s(env, "writer")?).map(|w| w.compartments.as_slice()).unwrap_or(&[]);
+        floor_v7 = comps
+            .iter()
+            .chain(std::iter::once(&String::new()))
+            .filter_map(|c| st.clock.get(c))
+            .max()
+            .cloned();
+        floor_v7.as_deref()
+    } else {
+        last.and_then(|e| e.get("record_time")).and_then(|v| v.as_str())
+    };
+    if let Some(prev_rt) = floor {
         if record_time < prev_rt {
             return Err(err(
                 "E_CLOCK",
@@ -1352,6 +1407,34 @@ fn check(
                         format!("an instance's id starts with '{writer}-'"),
                     ));
                 }
+                // From version 7 (issue #48, I2b): the gate assigns the id. It
+                // is exactly '<parent>-<k>', k the smallest number above the
+                // instances this parent has started for which that id is
+                // free: skipping depends only on what owners registered. The
+                // duplicate check comes first, so a session that lost a race
+                // for an id is told so and retries.
+                if st.schema_version >= 7 {
+                    if st.writers.contains_key(r.writer) {
+                        return Err(err(
+                            "E_DUPLICATE",
+                            format!("writer '{}' already registered", r.writer),
+                        ));
+                    }
+                    let mut k = 1 + st
+                        .writers
+                        .values()
+                        .filter(|x| x.instance_of.as_deref() == Some(writer))
+                        .count();
+                    while st.writers.contains_key(&format!("{writer}-{k}")) {
+                        k += 1;
+                    }
+                    if r.writer != format!("{writer}-{k}") {
+                        return Err(err(
+                            "E_SPAWN",
+                            format!("an instance's id is '{writer}-<k>', the first free number above the instances '{writer}' has started"),
+                        ));
+                    }
+                }
                 if r.compartments.iter().any(|c| !w.in_compartment(c)) {
                     return Err(err(
                         "E_SPAWN",
@@ -1388,12 +1471,16 @@ fn check(
             }
             // One session, one writer: otherwise its words could be split
             // between names, or two sessions' words merged under one.
-            // (Checked within the party: a session id is the party's.)
+            // (Checked within the party: a session id is the party's. And only
+            // against writers sharing a compartment with this one: a refusal
+            // must not tell a writer that a session exists where it cannot
+            // see, issue #48, S1: on every log.)
             if let Some(sess) = r.session {
                 if st.writers.values().any(|x| {
                     x.party == r.party
                         && x.session.as_deref() == Some(sess)
                         && x.surface.as_deref() == r.surface
+                        && r.compartments.iter().any(|c| x.in_compartment(c))
                 }) {
                     return Err(err(
                         "E_IDENTITY",
@@ -3050,6 +3137,14 @@ impl State {
                     ("instance_of", &w.instance_of),
                 ] {
                     if let Some(v) = v {
+                        // Issue #48 (O1), on every log: a restricted view
+                        // names the operator only to a reader who shares a
+                        // compartment with it.
+                        if k == "operator"
+                            && !self.writers.get(v.as_str()).is_some_and(|o| o.compartments.iter().any(|c| visible(c)))
+                        {
+                            continue;
+                        }
                         fields.push((k, Value::str(v.clone())));
                     }
                 }
diff --git a/crates/gnomon-core/src/query.rs b/crates/gnomon-core/src/query.rs
index 794be52..f533fe4 100644
--- a/crates/gnomon-core/src/query.rs
+++ b/crates/gnomon-core/src/query.rs
@@ -533,6 +533,13 @@ impl<'a> Builder<'a> {
                 ("session", &w.session),
             ] {
                 if let Some(v) = v {
+                    // Issue #48 (O1), on every log: the operator is shown
+                    // only to a reader who shares a compartment with it.
+                    if k == "operator"
+                        && !st.writers.get(v.as_str()).is_some_and(|o| o.compartments.iter().any(|c| self.sees(c)))
+                    {
+                        continue;
+                    }
                     self.add(eng, "identity", vec![s(wid), s(k), s(v)], src)?;
                 }
             }
@@ -589,7 +596,8 @@ impl<'a> Builder<'a> {
 /// Can the reader see this event? Its compartment decides: a claim's,
 /// an episode's, a verdict's claim's, a dispute's, an appeal's; a
 /// registration is visible if the writer shares a compartment with the
-/// reader; a schema change belongs to everyone.
+/// reader; a lease, release or revocation if the writer it concerns shares one
+/// (issue #48); a schema change belongs to everyone.
 pub fn event_visible(ledger: &Ledger, visible: &[String], e: &Value) -> bool {
     let st = &ledger.state;
     let sees = |c: &str| visible.iter().any(|x| x == c);
@@ -606,6 +614,12 @@ pub fn event_visible(ledger: &Ledger, visible: &[String], e: &Value) -> bool {
             .map(|c| sees(&c.compartment))
             .unwrap_or(false)
     };
+    let shares = |w: &str| {
+        st.writers
+            .get(w)
+            .map(|w| w.compartments.iter().any(|c| sees(c)))
+            .unwrap_or(false)
+    };
     match e.get("kind").and_then(|v| v.as_str()).unwrap_or("") {
         "claim" => body.map(|b| claim_seen(&claim_id(b))).unwrap_or(false),
         "episode" => st
@@ -656,11 +670,18 @@ pub fn event_visible(ledger: &Ledger, visible: &[String], e: &Value) -> bool {
                     .map(|x| sees(&x.compartment))
                     .unwrap_or(false)
         }
-        "register" => st
-            .writers
-            .get(field("writer"))
-            .map(|w| w.compartments.iter().any(|c| sees(c)))
+        "register" => shares(field("writer")),
+        // Issue #48 (V1): a lease and its release are visible to a reader
+        // who shares a compartment with the leased writer; a revocation to
+        // one who shares a compartment with its target. (Not a state
+        // change: only what `through` can show.) On every log, whatever its version.
+        "lease" => shares(field("writer")),
+        "release" => st
+            .leases
+            .get(field("lease"))
+            .map(|l| shares(&l.writer))
             .unwrap_or(false),
+        "revoke" => shares(field("writer")),
         _ => true,
     }
 }
diff --git a/crates/gnomon-core/src/schema.rs b/crates/gnomon-core/src/schema.rs
index 758e436..c53891d 100644
--- a/crates/gnomon-core/src/schema.rs
+++ b/crates/gnomon-core/src/schema.rs
@@ -11,7 +11,7 @@ use crate::json::Value;
 /// to state their model and operator and resolves speakers strictly (see
 /// section 2a of the spec); version 1 logs replay under the rules they were
 /// written with, and move to version 2 with a `schema_change`.
-pub const SCHEMA_VERSION: i64 = 6;
+pub const SCHEMA_VERSION: i64 = 7;
 
 pub const STATEMENT_KINDS: &[&str] =
     &["describe", "promise", "permit", "command", "ask", "imagine"];
diff --git a/crates/gnomon-core/src/snapshot.rs b/crates/gnomon-core/src/snapshot.rs
index 8aa6a5e..9d4251c 100644
--- a/crates/gnomon-core/src/snapshot.rs
+++ b/crates/gnomon-core/src/snapshot.rs
@@ -45,7 +45,7 @@ use crate::ledger::{
 };
 use crate::sha256::digest;
 
-const MAGIC: &[u8; 8] = b"GNSNAP4\n";
+const MAGIC: &[u8; 8] = b"GNSNAP5\n";
 /// Magic, code hash, bytes covered, their hash, HMAC.
 const HEADER: usize = 8 + 32 + 8 + 32 + 32;
 
@@ -500,7 +500,8 @@ codec_struct!(State {
     appeals_by_claim,
     successors,
     leases,
-    schema_version
+    schema_version,
+    clock
 });
 codec_struct!(LeaseInfo {
     writer,
diff --git a/python/gnomon/__init__.py b/python/gnomon/__init__.py
index 93a9119..1a710bc 100644
--- a/python/gnomon/__init__.py
+++ b/python/gnomon/__init__.py
@@ -591,7 +591,7 @@ class Memory:
 
 
 #: The protocol version new memories start at (see the spec, section 2a).
-SCHEMA_VERSION = 6
+SCHEMA_VERSION = 7
 
 
 def _cut(text: str, n: int) -> str:
diff --git a/python/gnomon/session.py b/python/gnomon/session.py
index fea2ecf..394ac36 100644
--- a/python/gnomon/session.py
+++ b/python/gnomon/session.py
@@ -62,10 +62,12 @@ def instance_for(memory: Memory, parent: str, session: Optional[str] = None,
                 key = derived
     session = session or uuid.uuid4().hex[:16]
     key = key or session
-    for n in range(1, 1_000_000):
-        wid = f"{parent}-{n}"
-        if wid not in writers:
-            break
+    # The gate (protocol version 7, I2b) assigns the id: the first free
+    # `parent-<k>` with k above the count of this parent's instances.
+    k = 1 + sum(1 for w in writers.values() if w.get("instance_of") == parent)
+    while f"{parent}-{k}" in writers:
+        k += 1
+    wid = f"{parent}-{k}"
     if len(wid) > 32:
         raise GateError("E_BODY", f"'{parent}' is too long a writer id to start instances of (ids are at most "
                                   "32 characters, and an instance's is its parent's plus '-<n>')")
diff --git a/spec/claim-protocol-v0.1.md b/spec/claim-protocol-v0.1.md
index d2bbd87..455baf3 100644
--- a/spec/claim-protocol-v0.1.md
+++ b/spec/claim-protocol-v0.1.md
@@ -81,7 +81,7 @@ their meaning.
 | `model` | models: required; tools: optional | the model it runs as reported by its runtime (not verified), or a tool's version |
 | `operator` | models and tools: required | the registered human it acts for |
 | `surface` | models and tools, optional | where it runs: claude-code, claude-app, api… |
-| `session` | models and tools, optional | the runtime's id for this session or agent; one writer per party, session and surface (`E_IDENTITY`) |
+| `session` | models and tools, optional | the runtime's id for this session or agent; one writer per party, session and surface (`E_IDENTITY`); on every log (issue #48) only among writers sharing a compartment with the registrant, so a refusal never tells of a session where the registrant cannot see |
 
 Models, operators and sessions are printable ASCII without spaces; names
 follow the participant-name rules (no look-alike scripts, combining marks,
@@ -94,7 +94,11 @@ surface if it has one; an id that starts with the parent's id and a hyphen
 (`claude-3` for `claude`); only compartments the parent is in; only
 capabilities the parent has, never `owner`, `govern` or `spawn`. So an
 instance can never take another identity or more power, and cannot probe
-for ids outside its parent's namespace. The MCP server uses this
+for ids outside its parent's namespace. From protocol version 7 (issue #48) the
+gate assigns the id: exactly `<parent>-<k>`, k the smallest number above the
+instances the parent has started for which that id is free (so it depends only
+on what owners registered), and the duplicate check comes first, so a session
+that lost a race is told `E_DUPLICATE` and retries. The MCP server uses this
 (`--per-session`) so that every session gets its own writer, however the
 host is configured; a session that resumes keeps its writer.
 
@@ -416,11 +420,35 @@ that hidden content exists.
 Reads can be restricted to a set of compartments. A restricted view contains
 nothing from other compartments: no claims, episodes, supersessions or
 event ids, and writers are listed only with the restricted compartments and
-without `last_seq`. Its digest covers only what it shows, and it omits the
+without `last_seq`; on every log (issue #48) a writer's `operator` is
+shown (in the view and as an `identity` fact) only if the operator shares a
+compartment with the reader. Its digest covers only what it shows, and it omits the
 log's event count and head hash, which describe the whole log.
 
-Known limit: event ids are `<writer>/<writer_seq>`, so gaps in a writer's
-visible sequence reveal that it wrote hidden events (not what they were).
+On every log (issue #48) a query's `through` (where the reader's view
+ends) counts a lease or release only if the leased writer shares a compartment with
+the reader, and a revocation only if its target does; a schema change counts for
+everyone.
+
+From protocol version 7 (issue #48) the rule that record time never runs backwards
+compares an event's time only with events touching the writer's compartments (and
+global ones such as a schema change), so an event in a compartment the writer is
+not in cannot make its write fail. Cost: the log's record times are then ordered
+only within a compartment, and an `as_of` view, which replays a prefix of the log,
+ends at the first event dated after the cut and can omit a later-appended event
+dated before it.
+
+Known limit (P14, R; issue #48): a restricted reader can still learn how many
+events exist that it cannot see. Event ids are `<writer>/<writer_seq>`, so gaps in
+a writer's visible sequence reveal that it wrote hidden events (not what they
+were); the id and the return value of a write reveal how many events that writer
+holds in all, including hidden ones; and an event's global `seq` reveals how many
+events the whole log holds before it, visible or not. No rule here closes this:
+doing so would need per-compartment sequences, a design change.
+
+Known limit (channel L, open, with the owner): leases on a frame name are not
+compartment-scoped. A live lease on a frame in a compartment a scheduler cannot
+see still makes that scheduler's lease on the same frame name fail (`E_LEASED`).
 
 **Revocation (protocol version 5).** Access granted by mistake, or to a
 writer that later misbehaves, is withdrawn with `revoke` (`writer`,
