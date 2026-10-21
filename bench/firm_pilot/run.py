"""The firm pilot: real work by Claude models, graded by the computer,
recorded in a memory, and the plans that follow from it.

    python bench/firm_pilot/run.py

Twenty-four Python tasks with exact specifications (`easy/tasks.py`,
`hard/tasks.py`) were given to Claude Haiku 5.5 and Claude Sonnet 5.5
sub-agents, one task per agent, in Claude Code. Hidden tests
(`*/grade.py`, checked against reference solutions) graded what each
wrote (`solutions/`). `results.json` holds every run: the grade, the
tokens the sub-agent used, its tool calls and its time.

This script regrades the solutions, records the work in a fresh memory as
the firm's books (a work order for each task, each worker's result
answering it, the test runner's verdict on each result), and prints the
plan for more of the same work before and after that record.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "python"))

from gnomon import Memory, claim, firm  # noqa: E402


def regrade(runs: list) -> list:
    for r in runs:
        out = subprocess.run([sys.executable, f"{r['set']}/grade.py", r["task"],
                              f"solutions/{r['set']}/{r['tier']}/{r['task']}.py"],
                             cwd=HERE, capture_output=True, text=True, timeout=900)
        r["regraded"] = out.stdout.strip()
    return runs


def books(runs: list, path: str) -> Memory:
    m = Memory(path)
    t0 = "2026-01-01T00:00:00Z"
    m.register("john", "john", party="john", kind="human", name="John", capabilities=["owner"],
               compartments=["firm"], record_time=t0)
    m.register("john", "tests", party="test-runner", kind="tool", model="pytest-like-1", operator="john",
               capabilities=["verify"], compartments=["firm"], record_time=t0)
    for tier in sorted({r["tier"] for r in runs}):
        m.register("john", f"{tier}-staff", party=f"{tier}-staff", kind="model", model=f"claude-{tier}-5-5",
                   operator="john", name=f"{tier.capitalize()} engineer", capabilities=["write"],
                   compartments=["firm"], record_time=t0)
    orders = {}
    for r in runs:
        key = (r["set"], r["task"])
        if key not in orders:
            orders[key] = m.ask("john", f"Implement '{r['task']}' to its specification ({r['set']} set).",
                                frame="firm/code", compartment="firm")
        result = m.remember(f"{r['tier']}-staff", claim(
            f"{r['task']} ({r['set']}) implemented by {r['tier']}.", speaker=f"{r['tier']}-staff", frame="firm/code",
            compartment="firm", stance="asserted", truth="true", origin="derived", confidence=7000,
            about=[{"claim": orders[key], "relation": "answers"}]))
        m.verdict("tests", result, "supports" if r["regraded"] == "PASS" else "refutes", "test",
                  note=r["regraded"][:200])
    return m


def main() -> int:
    runs = regrade(json.loads((HERE / "results.json").read_text()))
    print("Results (regraded now):")
    for s in ("easy", "hard"):
        for tier in ("haiku", "sonnet"):
            xs = [r for r in runs if r["set"] == s and r["tier"] == tier]
            if xs:
                passed = sum(r["regraded"] == "PASS" for r in xs)
                print(f"  {s:5} {tier:7} {passed}/{len(xs)} passed; sub-agent tokens median "
                      f"{statistics.median(r['tokens'] for r in xs):,.0f}; time median "
                      f"{statistics.median(r['ms'] for r in xs) / 1000:.1f} s")
    with tempfile.TemporaryDirectory() as d:
        m = books(runs, str(Path(d) / "firm.jsonl"))
        rec = firm.record_from(m, "john")
        task = firm.Task("code", 6000, 1500, stakes_usd=2.0, check="exact", parts=24)
        print("\nBefore the record (priors only):")
        print(firm.plan(task).summary())
        print("\nAfter the record:")
        print(firm.plan(task, rec).summary())
        print("\nAgainst a single Sonnet agent (measured, like Haiku):")
        print(firm.plan(task, rec, single="sonnet").summary())
        print("\nBelief now:", ", ".join(f"{x['tier']} {x['p_right']:.3f} ({x['on_record']} judged)"
                                        for x in firm.explain(rec, ["code"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
