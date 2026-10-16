# Gnomon

**Lossless, verifiable memory for people and AI models.**

A gnomon is the upright of a sundial: its shadow tells the time. The Greek
word means *one who knows*. Gnomon keeps everything that was said and done,
knows when each thing was known, and can say why anything is believed. That
is the record every claim is read against.

It is the foundation of a larger design: private memory, a verified commons
shared between AI systems and people, and a canvas over the tools where work
happens. This repository is the first milestone: **the record itself.**

## What works now

- **Canonical JSON (RFC 8785) and SHA-256** in Rust with zero dependencies,
  checked against the FIPS vectors, RFC 8785's own examples, Python's
  `hashlib`, and an independent Python implementation.
- **Claim schema v0.1** ([spec](spec/claim-protocol-v0.1.md)): statement
  kinds, origin (observed, derived, guessed…), stance (a decision is never
  confused with a suggestion), four-valued truth, validity domains, two
  clocks, compartments.
- **One hash-chained, append-only log behind one write gate.** Replay
  rebuilds identical state. Changed bytes, reordered or removed lines,
  truncation (with checkpoints) and torn writes are all detected. "As of"
  queries return exactly what was known at any moment.
- **Verification without self-judgement.** No writer, or party, can verify
  its own claim, and each party gives one verdict per claim.
- **Disputes that stay honest.** A contest flags a claim as disputed without
  changing its truth. A party with a stake can never judge. Each party gets
  one argument per claim; after that, only evidence counts. Every dispute
  has a way to end.
- **Three ways back for a refuted claim:** appeal on new outside evidence,
  appeal when the balance of verdicts favours it, or restate it as a new
  claim. A neutral party decides appeals.
- **Compartments that don't leak**, not even through error codes, digests or
  event counts.
- **Safe under concurrency:** writers in any thread or process, through any
  path or symlink, are serialised by a lock on the log itself.
- **Python package** over the Rust core through `ctypes` (standard library
  only).
- **Ingestion that the computer does exactly** (M2): transcripts and
  documents are parsed by plain code, sentences classified by named rules,
  and anything uncertain is set aside rather than guessed. Every extracted
  claim quotes its source word for word, and the core refuses any quote that
  is not really there.
- **Queries with proofs** (M3): a small Datalog engine in the core answers
  questions over the memory ("which decisions still stand, and who made
  them?"), as of any moment and only through the reader's compartments.
  Every answer comes with a proof: each step names its rule, and each leaf
  names the log events it was read from, by hash. `gnomon.proofs` re-checks
  proofs without the core.
- **Native checkers as verifiers:** arithmetic is checked with exact
  fractions and dates against the real calendar. Checkers are their own
  parties, so their verdicts pass through the same rules as anyone else's.

## How it was tested

Every past failure from earlier projects was written as a failing test before
any code existed (`tests/incidents.rs`). Then an independent reviewer that
did not write the code attacked it in six rounds. Each confirmed finding
became a regression test (`tests/review1.rs` … `tests/review6.rs`), and
mutation checks confirm the tests fail when the rules they guard are removed.

The query engine (M3) went through five more review rounds (19 findings,
each now a test in `tests/query.rs` or `python/tests/test_query.py`). Its
answers are checked against a deliberately naive second evaluator on
hundreds of random programs, and every proof against an independent checker
that re-derives disputes from the log itself.

## Build and test

Requires Rust (stable) and Python 3.10+. Nothing else.

```bash
cargo test --release            # Rust: 152 tests
bash scripts/build.sh           # builds the core into the Python package
python3 -m unittest discover -s python/tests -v
```

## Use from Python

```python
import sys; sys.path.insert(0, "python")
import gnomon
from gnomon import Memory, claim

m = Memory("memory.jsonl")
m.register("alex", "alex", party="alex", kind="human",
           capabilities=["owner"], compartments=["lab"])
m.register("alex", "claude-c1", party="anthropic", kind="model",
           capabilities=["write", "verify"], compartments=["lab"])

cid = m.remember("alex", claim(
    "Core is written in Rust with Python on top",
    speaker="alex", frame="gnomon stack", compartment="lab",
    stance="decided", truth="true", confidence=9500, origin="manual"))

print(m.state()["state"]["claims"][cid])
print(m.state(as_of="2026-10-14T00:00:00Z"))   # what was known then
print(m.verify())

# Which decisions stand, and why?
from gnomon import proofs
r = m.query("alex", "?- decision(C), statement(C, S).")
print(proofs.explain(r))        # the rules used, down to log events
print(proofs.check(r, "?- decision(C), statement(C, S).", gnomon.VIEWS, "memory.jsonl"))  # [] = checks out
```

## Layout

```
crates/gnomon-core/       Rust core: json, sha256, schema, ledger, store, ffi
  tests/incidents.rs      past failures from earlier projects, as tests
  tests/log.rs            rebuild, tamper, torn write, checkpoint, as-of
  tests/appeals.rs        dispute flags and the three ways back
  tests/review1-6.rs      every confirmed finding from independent review
  tests/vectors.rs        shared vectors every implementation must match
  tests/query.rs          the query engine, views and proofs
python/gnomon/            Python package (ctypes binding)
  ingest.py               exact parsing and rule-based extraction
  checkers.py             arithmetic and calendar verifiers
  proofs.py               independent proof checker and explanations
python/tests/             oracle, end-to-end and multi-process tests
spec/                     claim protocol v0.1
tests/vectors/            shared canonical-JSON vectors
scripts/                  build and vector generation
```

## Status and what's next

Milestones M0–M3 of the v1 plan: the record, ingestion, and queries with
proofs. Next are an MCP server so any model can use it, and a benchmark
against the best existing memory systems. Open design
questions are listed in the [spec](spec/claim-protocol-v0.1.md).

## Licence

Not yet chosen. Until a licence is added, the code is visible but no
permission to copy, modify or redistribute it is granted.
