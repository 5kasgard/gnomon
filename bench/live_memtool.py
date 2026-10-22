"""A memory tool a model can run from a shell, over a recorded live history, limited to what was
recorded by --as-of. Every call is appended to --calls, so the test can see what was asked.

    python bench/live_memtool.py --log Sam.jsonl --as-of T --calls calls.jsonl situation
    python bench/live_memtool.py ... context "a question"
    python bench/live_memtool.py ... expand POINTER [--around N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))
from gnomon.mcp import Server  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--log", required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--calls", required=True)
    p.add_argument("tool", choices=["situation", "context", "expand"])
    p.add_argument("arg", nargs="?")
    p.add_argument("--around", type=int, default=0)
    a = p.parse_args()
    s = Server(a.log, "assistant")
    if a.tool == "situation":
        args = {"as_of": a.as_of, "budget": 3000}
    elif a.tool == "context":
        args = {"query": a.arg or "", "as_of": a.as_of, "budget": 1500, "directive_budget": 0}
    else:
        args = {"pointer": a.arg or "", "around": a.around}
    try:
        text, _ = s.tool(a.tool, args)
    except Exception as e:  # noqa: BLE001
        text = f"error: {e}"
    with open(a.calls, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "tool": a.tool, "arg": a.arg, "tokens": len(text) // 4}) + "\n")
    print(text)


if __name__ == "__main__":
    main()
