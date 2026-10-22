"""Probes again, on the recorded histories, in three conditions: none (no memory),
pull (the model may call Gnomon's tools) and push (the harness puts Gnomon's
situation and context into the prompt before the model answers). Each probe
sees only what was recorded before its session. Protocol in live.md.

    python bench/live_probes.py --out DIR [--provider workers-ai]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "python"))
from gnomon.mcp import Server  # noqa: E402
from live import B_NONE, Provider, b_reply  # noqa: E402

PUSH = """You are a helpful assistant talking with one person across many separate conversations.
Below is what your memory holds about them. Use whatever bears on their message; ignore the rest.
Answer briefly and naturally. Never mention the memory.

{memory}"""


class AsOf:
    """A Server whose memory tools see only what was recorded before `as_of`."""

    def __init__(self, server, as_of):
        self.server, self.as_of = server, as_of

    def tool(self, name, args):
        if name in ("context", "situation"):
            args = dict(args, as_of=self.as_of)
        return self.server.tool(name, args)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--provider", default="workers-ai")
    p.add_argument("--model")
    a = p.parse_args()
    out = Path(a.out)
    prov = Provider(a.provider, a.model)
    cards = json.loads((HERE / "live_cards.json").read_text())["personas"]
    rows = []
    for persona in cards:
        tr = json.loads((out / f"{persona['name']}_transcript.json").read_text())
        when = {s["session"]: s["when"] for s in tr}
        server = Server(str(out / f"{persona['name']}.jsonl"), "assistant")
        for f in persona["facts"]:
            s = f["probe_session"]
            as_of = when[s - 1]  # the previous session's record time: everything before this session
            mem_s, _ = server.tool("situation", {"as_of": as_of, "budget": 3000})
            mem_c, _ = server.tool("context", {"query": f["probe"], "as_of": as_of, "budget": 1500,
                                               "directive_budget": 0})
            push = prov.chat(PUSH.format(memory=mem_s + "\n\n" + mem_c), [{"role": "user", "content": f["probe"]}])
            pull, calls = b_reply(prov, AsOf(server, as_of), [("A", f["probe"])], [], True)
            none = prov.chat(B_NONE, [{"role": "user", "content": f["probe"]}])
            hit = lambda t: any(w.lower() in t.lower() for w in f["check"])  # noqa: E731
            rows.append({"persona": persona["name"], "fact": f["id"], "session": s, "probe": f["probe"],
                         "none": none, "pull": pull, "push": push, "pull_calls": [c["call"] for c in calls],
                         "applied": {"none": hit(none), "pull": hit(pull), "push": hit(push)},
                         "memory_tokens": (len(mem_s) + len(mem_c)) // 4})
            r = rows[-1]
            print(f"{persona['name']:6s} {f['id']:9s} none {'Y' if r['applied']['none'] else '.'}  "
                  f"pull {'Y' if r['applied']['pull'] else '.'} ({len(calls)} calls)  "
                  f"push {'Y' if r['applied']['push'] else '.'}  memory {r['memory_tokens']} tokens", flush=True)
    for c in ("none", "pull", "push"):
        print(f"{c:5s} applied {sum(r['applied'][c] for r in rows)}/{len(rows)}")
    print("usage:", prov.calls, "calls,", round(prov.neurons), "neurons")
    (out / "probes3.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
