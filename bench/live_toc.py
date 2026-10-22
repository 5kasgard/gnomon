"""Run 3 of the live loop (live.md): what the hook should push. Re-uses run 2's histories.

    python bench/live_toc.py --out DIR_run2
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "python"))
from gnomon.mcp import Server  # noqa: E402
from live import B_NONE, Provider  # noqa: E402
from live2 import NATIVE, PUSH2, b_native  # noqa: E402
from live_probes import AsOf  # noqa: E402
from situation_reader import STOP  # noqa: E402

WORD = re.compile(r"[a-z][a-z'-]{2,}")
MODES = ("none", "push full", "push situation", "toc + tools")


def toc(server, as_of):
    text, _ = server.tool("situation", {"as_of": as_of})
    lines = [l.split("] ", 1)[1] for l in text.splitlines() if l.startswith("[") and "] " in l]
    df = Counter(w for l in lines for w in set(WORD.findall(l.lower())))
    words = []
    for l in lines:
        ws = [w for w in dict.fromkeys(WORD.findall(l.lower())) if w not in STOP and len(w) > 3]
        for w in sorted(ws, key=lambda w: df[w])[:2]:
            if w not in words:
                words.append(w)
    return words[:30]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--reps", type=int, default=2)
    a = p.parse_args()
    out = Path(a.out)
    prov = Provider("workers-ai", None)
    cards = json.loads((HERE / "live_cards.json").read_text())["personas"]
    rows = []
    for persona in cards:
        tr = json.loads((out / f"{persona['name']}_transcript.json").read_text())
        when = {s["session"]: s["when"] for s in tr}
        server = Server(str(out / f"{persona['name']}.jsonl"), "assistant")
        for f in persona["facts"]:
            as_of = when[f["probe_session"] - 1]
            srv = AsOf(server, as_of)
            mem_s, _ = srv.tool("situation", {"budget": 3000})
            mem_c, _ = srv.tool("context", {"query": f["probe"], "budget": 1500, "directive_budget": 0})
            words = toc(server, as_of)
            note = ("\nYour memory holds things this person has told you about themselves, including: "
                    + ", ".join(words) + ". Look them up when they could matter.")
            msgs = [{"role": "user", "content": f["probe"]}]
            hit = lambda t: any(w.lower() in t.lower() for w in f["check"])  # noqa: E731
            for rep in range(a.reps):
                ans = {"none": (prov.chat(B_NONE, msgs), []),
                       "push full": (prov.chat(PUSH2.format(memory=mem_s + "\n\n" + mem_c), msgs), []),
                       "push situation": (prov.chat(PUSH2.format(memory=mem_s), msgs), [])}
                # toc + tools: NATIVE prompt with the note in place of the salience line
                ans["toc + tools"] = b_native(prov, srv, [("A", f["probe"])], "salient", note)
                rows.append({"persona": persona["name"], "fact": f["id"], "rep": rep,
                             "applied": {m: hit(ans[m][0]) for m in MODES},
                             "calls": {m: [c["name"] for c in ans[m][1]] for m in MODES},
                             "pushed_tokens": {"push full": (len(mem_s) + len(mem_c)) // 4,
                                               "push situation": len(mem_s) // 4, "toc + tools": len(note) // 4},
                             "answers": {m: ans[m][0] for m in MODES}})
                r = rows[-1]
                print(f"{persona['name']:6s} {f['id']:9s} rep {rep}  " + "  ".join(
                    f"{m} {'Y' if r['applied'][m] else '.'}{'*' if r['calls'][m] else ''}" for m in MODES), flush=True)
    n = len(rows)
    print()
    for m in MODES:
        tok = [r["pushed_tokens"].get(m, 0) for r in rows]
        print(f"{m:15s} applied {sum(r['applied'][m] for r in rows)}/{n}   called tools {sum(bool(r['calls'][m]) for r in rows)}/{n}"
              f"   pushed ~{sum(tok) // n} tokens")
    print("usage:", prov.calls, "calls,", round(prov.neurons), "neurons")
    (HERE / "live_results" / "run3_probes.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
