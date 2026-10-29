#!/usr/bin/env python3
"""Run bench/issue51/run.py on every arm (each in its own git worktree, so no
branch is switched) and write bench/issue51/results.json.

    python3 bench/issue51/collect.py [--out bench/issue51/results.json]

Arm 0 is the base branch (= main's code). Arm C2 is an extra variant, not in
the pre-registration. Requires the native core built in this checkout
(bash scripts/build.sh); the library is copied into each worktree.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "auto-one-compartment"
ARMS = [("0", BASE), ("A", "arm-A"), ("B", "arm-B"), ("C", "arm-C"), ("C2", "arm-C2"), ("D", "arm-D"), ("D1", "arm-D1"), ("D2", "arm-D2")]


def run(*cmd, cwd=ROOT, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "bench/issue51/results.json"))
    ap.add_argument("--no-tests", action="store_true")
    a = ap.parse_args()
    so = ROOT / "python/gnomon/libgnomon_core.so"
    results = {"arms": {}, "note": "arm 0 = auto-one-compartment (main's code); C2 = extra variant, not pre-registered"}
    with tempfile.TemporaryDirectory() as tmp:
        for label, branch in ARMS:
            wt = Path(tmp) / f"wt-{label}"
            r = run("git", "worktree", "add", "--detach", str(wt), branch)
            if r.returncode:
                print(r.stderr, file=sys.stderr)
                return 1
            try:
                for f in so.parent.glob("libgnomon_core*"):
                    shutil.copy(f, wt / "python/gnomon" / f.name)
                cmd = [sys.executable, "bench/issue51/run.py", "--base", BASE] + ([] if not a.no_tests else [])
                if not a.no_tests:
                    cmd.append("--tests")
                p = run(*cmd, cwd=wt)
                if p.returncode:
                    print(f"arm {label}: harness failed\n{p.stderr[-2000:]}", file=sys.stderr)
                    return 1
                d = json.loads(p.stdout)
                d["branch"], d["arm"] = branch, label
                results["arms"][label] = d
                print(f"arm {label}: leaks on {d['leaks']}", file=sys.stderr)
            finally:
                run("git", "worktree", "remove", "--force", str(wt))
    Path(a.out).write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
