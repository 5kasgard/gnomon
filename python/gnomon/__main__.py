"""Command line: set up a memory, register writers, verify, query.

    python -m gnomon init     --log memory.jsonl --owner john --compartments lab,private
    python -m gnomon register --log memory.jsonl --by john --writer claude-c1 \\
                              --party anthropic --kind model --model claude-opus-5-5 \\
                              --capabilities write,verify --compartments lab
    python -m gnomon verify   --log memory.jsonl
    python -m gnomon query    --log memory.jsonl --reader john "?- decision(C), statement(C, S)."

Every command goes through the core's write gate, like everything else.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import VIEWS, GateError, Memory, proofs


def _list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m gnomon", description="Gnomon: verifiable memory.")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="start a new memory with its owner (a human)")
    i.add_argument("--log", required=True)
    i.add_argument("--owner", required=True, help="the owner's writer id (also their party)")
    i.add_argument("--compartments", required=True, help="comma-separated, e.g. lab,private")
    i.add_argument("--name", help="how you are named in records, e.g. 'John Doe'")

    r = sub.add_parser("register", help="register a writer (by an owner)")
    r.add_argument("--log", required=True)
    r.add_argument("--by", required=True, help="an owner who registers it")
    r.add_argument("--writer", required=True)
    r.add_argument("--party", required=True, help="who answers for it (e.g. anthropic, openai, john)")
    r.add_argument("--kind", required=True, choices=["human", "model", "tool"])
    r.add_argument("--capabilities", required=True, help="comma-separated: write, verify, govern, owner")
    r.add_argument("--compartments", required=True, help="comma-separated")
    r.add_argument("--name", help="how to address it, e.g. 'Claude' or 'John Doe'")
    r.add_argument("--model", help="models: the model it runs, as its runtime reports it (e.g. claude-opus-5-5)")
    r.add_argument("--operator", help="models and tools: the human it acts for (default: --by)")
    r.add_argument("--surface", help="where it runs, e.g. claude-code, claude-app, api")
    r.add_argument("--session", help="the runtime's id for this session or agent")

    v = sub.add_parser("verify", help="replay the log through every rule")
    v.add_argument("--log", required=True)

    q = sub.add_parser("query", help="ask a Datalog question; prints answers with their proofs")
    q.add_argument("--log", required=True)
    q.add_argument("--reader", required=True)
    q.add_argument("--as-of")
    q.add_argument("--json", action="store_true", help="print the full result as JSON")
    q.add_argument("program")

    h = sub.add_parser("hooks", help="print Claude Code settings for auto mode (records every conversation)")
    h.add_argument("--log", required=True)
    h.add_argument("--writer", required=True, help="a model writer with 'spawn'; each session is an instance")

    a = p.parse_args(argv)
    if a.cmd == "hooks":
        from .hook import settings
        log = str(Path(a.log).expanduser().resolve())
        print(json.dumps(settings(log, a.writer, sys.executable, str(Path(__file__).resolve().parent.parent)),
                         indent=2))
        print("\nAdd this to ~/.claude/settings.json (or .claude/settings.json in a project), and start the "
              "MCP server with --per-session.", file=sys.stderr)
        return 0
    log = Path(a.log)
    existed = log.exists() and log.stat().st_size > 0
    if a.cmd != "init" and not existed:
        print(f"{a.log} does not exist (start one with: python -m gnomon init)", file=sys.stderr)
        return 1
    m = Memory(a.log)
    try:
        if a.cmd == "init":
            if Path(a.log).exists() and Path(a.log).stat().st_size > 0:
                print(f"{a.log} already exists; not touching it", file=sys.stderr)
                return 1
            ev = m.register(a.owner, a.owner, party=a.owner, kind="human", capabilities=["owner"],
                            compartments=_list(a.compartments), name=a.name)
            print(f"created {a.log}: owner {a.owner} ({ev['event_id']})")
        elif a.cmd == "register":
            operator = a.operator or (a.by if a.kind != "human" else None)
            ev = m.register(a.by, a.writer, party=a.party, kind=a.kind, capabilities=_list(a.capabilities),
                            compartments=_list(a.compartments), name=a.name, model=a.model, operator=operator,
                            surface=a.surface, session=a.session)
            print(f"registered {a.writer} ({ev['event_id']})")
        elif a.cmd == "verify":
            r = m.verify()
            print(f"ok: {r['count']} events, head {r['head']}")
        elif a.cmd == "query":
            r = m.query(a.reader, a.program, a.as_of)
            problems = proofs.check(r, a.program, VIEWS, a.log, require_hashes=True)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                print(f"{len(r['answers'])} answer(s) as of {r.get('as_of')}; proofs "
                      + ("check out" if not problems else f"FAIL: {problems[:3]}"))
                for k in range(len(r["answers"])):
                    print()
                    print(proofs.explain(r, k))
            return 0 if not problems else 3
    except GateError as e:
        # A failed first write must not leave an empty log behind.
        if not existed and log.exists() and log.stat().st_size == 0:
            log.unlink()
        print(f"refused: {e.code}: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
