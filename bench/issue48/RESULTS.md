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
