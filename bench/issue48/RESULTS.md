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
