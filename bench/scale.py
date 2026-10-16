"""Speed and size at scale: how Gnomon's costs grow with the memory.

    python bench/scale.py [--events 20000] [--json]

Builds a memory of conversations and claims (about 1 KB per episode), then
measures, in one long-lived process (as an MCP server is):

* append: time to record one event;
* cold: a full audit of the log (verify) and a new process's first read
  (which replays the log through the gate before it answers);
* warm reads: the whole state, a Datalog query (proofs for the first 100
  answers, as the MCP server asks; the same with those proofs checked
  independently, as the server does; and with every answer proved), recall,
  resume;
* after another process appends: the first read in this process;
* size on disk per event.

Numbers are wall-clock medians on this machine; compare runs, not machines.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from gnomon import VIEWS, Memory, claim, proofs  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402

WORDS = ("database schema backup migration latency cache index query replica shard cost budget "
         "deadline design review test deploy rollback incident customer feature metric dashboard "
         "invoice supplier contract meeting roadmap hiring security audit privacy model memory").split()


def sentence(rng: random.Random, n: int = 12) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n)).capitalize() + "."


def build(path: str, events: int, seed: int = 7) -> float:
    rng = random.Random(seed)
    m = Memory(path)
    m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
               compartments=["work", "private"], record_time="2025-01-01T00:00:00Z")
    for w, model in (("claude", "claude-opus-5-5"), ("gpt", "gpt-6")):
        m.register("john", w, party=w, kind="model", model=model, operator="john", name=w.capitalize(),
                   capabilities=["write", "verify"], compartments=["work"], record_time="2025-01-01T00:00:01Z")
    n, day, t0 = 3, 0, time.perf_counter()
    claims = []
    while n < events:
        day += 1
        when = f"2025-{1 + (day // 28) % 12:02d}-{1 + day % 28:02d}T{(day // 336) % 24:02d}:00:00Z"
        # Clock must never run backwards: use a strictly growing timestamp.
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1735689600 + day * 3600))
        msgs = [("John" if i % 2 == 0 else "Claude", sentence(rng, 15)) for i in range(8)]
        record_messages(m, "claude", msgs, compartment="work", bindings={"Claude": "claude"}, record_time=when)
        n += 1
        for _ in range(4):
            if n >= events:
                break
            w = rng.choice(["claude", "gpt"])
            body = claim(sentence(rng), speaker=w, frame=f"project-{rng.randrange(40)}", compartment="work",
                         stance=rng.choice(["asserted", "decided", "suggested"]), truth="true",
                         origin="derived", confidence=6000)
            claims.append(m.remember(w, body, record_time=when))
            n += 1
        if claims and n < events and rng.random() < 0.3:
            c = rng.choice(claims)
            try:
                m.verdict("gpt" if rng.random() < 0.5 else "claude", c, "supports", "test", record_time=when)
                n += 1
            except Exception:
                pass
    return (time.perf_counter() - t0) / max(1, n)


def timed(fn, reps: int = 5) -> float:
    xs = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - t0)
    return statistics.median(xs)


def measure(events: int) -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "m.jsonl")
        per_append = build(path, events)
        size = os.path.getsize(path)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
        cold_code = f"import time,sys;from gnomon import Memory;t=time.perf_counter();Memory({path!r}).verify();" \
                    "print(time.perf_counter()-t)"
        first_code = (f"import time,sys;from gnomon import Memory;t=time.perf_counter();"
                      f"Memory({path!r}).frames('john');print(time.perf_counter()-t)")
        cold = float(subprocess.run([sys.executable, "-c", cold_code], capture_output=True, text=True,
                                    env=env, check=True).stdout)
        first = float(subprocess.run([sys.executable, "-c", first_code], capture_output=True, text=True,
                                     env=env, check=True).stdout)
        m = Memory(path)
        m.verify()
        q = "?- decision(C), statement(C, S)."
        out = {
            "events": events,
            "bytes_per_event": round(size / events),
            "append_ms": round(per_append * 1000, 2),
            "cold_audit_ms": round(cold * 1000, 1),
            "cold_first_read_ms": round(first * 1000, 1),
            "warm_state_ms": round(timed(lambda: m.state(compartments=["work"])) * 1000, 1),
            "warm_query_ms": round(timed(lambda: m.query("john", q, proofs=100)) * 1000, 1),
            "warm_query_all_proofs_ms": round(timed(lambda: m.query("john", q), 3) * 1000, 1),
            "warm_query_checked_ms": round(timed(lambda: proofs.check(m.query("john", q, proofs=100), q, VIEWS, path,
                                                                     require_hashes=True, proofs_requested=100), 3)
                                           * 1000, 1),
            "warm_recall_ms": round(timed(lambda: m.recall("john", "database migration rollback", k=10)) * 1000, 1),
            "warm_resume_ms": round(timed(lambda: m.resume("john", "project-3"), 3) * 1000, 1),
        }
        add = (f"from gnomon import Memory, claim;Memory({path!r}).remember('gpt', claim('External note.', "
               "speaker='gpt', frame='x', compartment='work'))")
        subprocess.run([sys.executable, "-c", add], env=env, check=True)
        t0 = time.perf_counter()
        m.query("john", q, proofs=100)
        out["after_external_append_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--events", default="2000,20000")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    rows = [measure(int(n)) for n in a.events.split(",")]
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    keys = list(rows[0])
    print("".join(f"{k:>26}" for k in ["metric"] + [str(r["events"]) for r in rows]))
    for k in keys[1:]:
        print(f"{k:>26}" + "".join(f"{r[k]:>26}" for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
