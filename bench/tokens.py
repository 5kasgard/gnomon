"""Context cost: how many tokens a model spends reading what Gnomon tells it.

    python bench/tokens.py [--data path/to/locomo10.json]

Builds a memory from the first LoCoMo conversation (its sessions as
recorded conversations, claims extracted from them by Gnomon's rules), adds
a handoff, and renders what the MCP server would send a model: a resume
packet, recall results for ten questions, an open-questions list and a
query's answers. For each it reports the characters and estimated tokens
(four characters each; tokenizers differ, the ratio between runs is what
matters) and the tokens per item shown (a turn, a claim, an answer).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "bench"))

from gnomon import Memory  # noqa: E402
from gnomon.ingest import ingest  # noqa: E402
from gnomon.mcp import Server, estimate_tokens  # noqa: E402

QUESTIONS = ["When did Caroline go to the LGBTQ support group?", "What did Melanie paint recently?",
             "Where did Melanie go camping?", "What career is Caroline pursuing?",
             "What books has Melanie read?", "When did Melanie run a charity race?",
             "What instruments does Melanie play?", "Who supports Caroline?",
             "What did Caroline research?", "How long has Caroline had her friends?"]


def build(data_path: str, d: str) -> tuple[str, Memory]:
    from locomo import build as locomo_build, load
    sample = load(Path(data_path))[0]
    m, reader, ids = locomo_build(sample, Path(d))
    log = m.path
    m.register("owner", "claude", party="anthropic", kind="model", model="claude-opus-5-5", operator="owner",
               name="Claude", capabilities=["write", "verify", "spawn"], compartments=["chat"])
    eps = sorted(m.state()["state"]["episodes"].items(), key=lambda kv: kv[1]["order"])
    claims = []
    for eid, _ in eps:
        rep = ingest(m, "owner", eid, frame="friends", compartment="chat")
        claims += [c for c, _ in rep.written]
    m.handoff("owner", frame="friends", compartment="chat", summary="Catching up on Caroline and Melanie's news.",
              next=["ask about the adoption plans"], open=["did Melanie finish the painting?"], refs=claims[:5])
    return log, m


def measure(srv: Server) -> dict:
    out = {}

    def add(name, text, items):
        out[name] = {"chars": len(text), "tokens": estimate_tokens(text), "items": items,
                     "tokens_per_item": round(estimate_tokens(text) / max(1, items), 1)}
    text, r = srv.tool("resume", {"frame": "friends", "budget_tokens": 100000})
    full = srv.memory.resume(srv.writer, "friends")
    items = (len(full["recent_turns"]) + len(full["since"]["claims"]) + len(full["decisions"])
             + len(full["open_questions"]) + len(full["rests_on"]))
    add("resume (all of it)", text, items)
    texts, n, words = [], 0, 0
    for q in QUESTIONS:
        t, rr = srv.tool("recall", {"query": q, "k": 10})
        texts.append(t)
        n += len(rr["hits"])
        words += sum(len(h["text"]) for h in srv.memory.recall(srv.writer, q, k=10)["hits"])
    add("recall (10 questions x 10 hits)", "\n".join(texts), n)
    joined = "\n".join(texts)
    out["recall overhead (all but the words said)"] = {
        "chars": len(joined) - words, "tokens": estimate_tokens(joined) - (words + 3) // 4, "items": n,
        "tokens_per_item": round((estimate_tokens(joined) - (words + 3) // 4) / max(1, n), 1)}
    t, rr = srv.tool("query", {"program": "?- statement(C, S), speaker(C, P).", "explain": 0})
    add("query (statements and speakers)", t, min(50, len(rr["answers"])))
    t, rr = srv.tool("open_questions", {})
    add("open questions", t, len(rr["questions"]))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(Path(tempfile.gettempdir()) / "locomo" / "data" / "locomo10.json"))
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as d:
        log, m = build(a.data, d)
        srv = Server(log, "owner")
        res = measure(srv)
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    print(f"{'output':<36}{'chars':>9}{'tokens':>9}{'items':>7}{'tokens/item':>13}")
    for k, v in res.items():
        print(f"{k:<36}{v['chars']:>9}{v['tokens']:>9}{v['items']:>7}{v['tokens_per_item']:>13}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
