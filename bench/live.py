"""A live loop: a simulated person (A) and an assistant (B) that knows A only
through Gnomon, over many sessions. Protocol in live.md.

    CF_ACCOUNT_ID=... CF_API_TOKEN=... python bench/live.py run --provider workers-ai --out DIR
    ANTHROPIC_API_KEY=... python bench/live.py run --provider anthropic --out DIR
    python bench/live.py run --provider fake --out DIR     (mechanics only, no model)
    python bench/live.py score --out DIR
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))
from gnomon import Memory  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from gnomon.mcp import Server  # noqa: E402

SESSIONS, TURNS, MAX_TOOLS = 8, 3, 4
B_SYSTEM = """You are a helpful assistant talking with one person across many separate conversations. You do not
remember earlier conversations yourself, but you have a memory you can consult with these tools:
- situation: what the person has said about their own situation, in their words.
- context {"query": "..."}: what the record holds that bears on a question.
- expand {"pointer": "..."}: the whole turn a [pointer] names.
To use a tool, reply with only: <tool>{"name": "...", "args": {...}}</tool>
You will get the result and can then use another tool or answer. Use your memory whenever earlier
conversations could matter. Answer the person briefly and naturally, applying what you know about them.
Never mention the tools or the memory."""
B_NONE = """You are a helpful assistant. Answer the person briefly and naturally."""
TOOL = re.compile(r"<tool>\s*(\{.*?\})\s*</tool>", re.S)


# ------------------------------------------------------------------ providers
class Provider:
    def __init__(self, kind: str, model: str | None):
        self.kind, self.calls, self.tokens_in, self.tokens_out = kind, 0, 0, 0
        self.neurons = 0.0
        if kind == "workers-ai":
            # Through a small Worker with an AI binding (bench/live.md), so no API token is needed.
            self.model = model or "@cf/google/gemma-4-26b-a4b-it"
            self.url = os.environ["PROXY_URL"]
            self.headers = {"x-key": os.environ["PROXY_KEY"], "Content-Type": "application/json",
                            "CF-Access-Client-Id": os.environ.get("CF_ACCESS_ID", ""),
                            "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_SECRET", "")}
        elif kind == "anthropic":
            self.model = model or "claude-haiku-4-5"
            self.url = "https://api.anthropic.com/v1/messages"
            self.headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                            "content-type": "application/json"}
        else:
            self.model = "fake"

    def chat(self, system: str, messages: list, max_tokens: int = 400) -> str:
        self.calls += 1
        if self.kind == "fake":
            last = messages[-1]["content"]
            if "assistant" in system and "<tool>" not in last and "Tool result" not in last and random.random() < 0.5:
                return '<tool>{"name": "situation", "args": {}}</tool>'
            return f"(reply {self.calls}) " + " ".join(last.split()[:12])
        if self.kind == "workers-ai":
            body = {"model": self.model, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
                    "messages": [{"role": "system", "content": system}] + messages}
        else:
            body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        for attempt in range(5):
            try:
                req = urllib.request.Request(self.url, json.dumps(body).encode(), self.headers)
                with urllib.request.urlopen(req, timeout=120) as r:
                    out = json.loads(r.read())
                break
            except Exception as e:  # rate limits, capacity: back off
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))
                print("  retry:", e, flush=True)
        if self.kind == "workers-ai":
            if "error" in out:
                raise RuntimeError(out["error"])
            u = out.get("usage", {})
            self.tokens_in += u.get("prompt_tokens", 0)
            self.tokens_out += u.get("completion_tokens", 0)
            self.neurons += u.get("neurons", 0) or 0
            return out["choices"][0]["message"]["content"] or ""
        u = out.get("usage", {})
        self.tokens_in += u.get("input_tokens", 0)
        self.tokens_out += u.get("output_tokens", 0)
        return "".join(c.get("text", "") for c in out["content"])


# ------------------------------------------------------------------ the loop
def b_reply(prov, server, convo, log, use_memory=True):
    """B answers the last message of `convo` ([(speaker, text)]), with tool calls when it has memory."""
    msgs = [{"role": "user" if s == "A" else "assistant", "content": t} for s, t in convo]
    if not use_memory:
        return prov.chat(B_NONE, msgs), []
    calls = []
    for _ in range(MAX_TOOLS + 1):
        out = prov.chat(B_SYSTEM, msgs)
        m = TOOL.search(out)
        if not m or len(calls) >= MAX_TOOLS:
            return TOOL.sub("", out).strip(), calls
        try:
            call = json.loads(m.group(1))
            text, structured = server.tool(call["name"], call.get("args", {}))
        except Exception as e:
            call, text = {"raw": m.group(1)}, f"Tool error: {e}"
        calls.append({"call": call, "result_tokens": len(text) // 4})
        log.append({"tool": call, "result": text[:4000]})
        msgs += [{"role": "assistant", "content": out}, {"role": "user", "content": f"Tool result:\n{text[:12000]}"}]
    return out, calls


def run(a):
    cards = json.loads((HERE / "live_cards.json").read_text())["personas"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    prov = Provider(a.provider, a.model)
    random.seed(20261022)
    results = []
    for p in cards:
        log_path = out / f"{p['name']}.jsonl"
        if log_path.exists():
            log_path.unlink()
        m = Memory(log_path)
        m.register("person", "person", party="person", kind="human", capabilities=["owner"], compartments=["chat"],
                   record_time="2026-01-01T00:00:00Z")
        m.register("person", "assistant", party="test", kind="model", model=prov.model, operator="person",
                   capabilities=["write"], compartments=["chat"], record_time="2026-01-01T00:00:01Z")
        server = Server(str(log_path), "assistant", embed=None)
        transcript = []
        for s in range(1, SESSIONS + 1):
            when = f"2026-{1 + (s - 1) // 4:02d}-{1 + 7 * ((s - 1) % 4):02d}T18:00:00Z"
            topic = p["topics"][(s - 1) % len(p["topics"])]
            to_say = [f["say"] for f in p["facts"] if f["session"] == s]
            probes = [f for f in p["facts"] if f["probe_session"] == s]
            convo, tlog = [], []
            # Probes open the session: B answers each with memory and, as the control, without.
            for f in probes:
                first = [("A", f["probe"])]
                with_mem, calls = b_reply(prov, server, first, tlog, True)
                without, _ = b_reply(prov, server, first, tlog, False)
                results.append({"persona": p["name"], "fact": f["id"], "session": s, "probe": f["probe"],
                                "with_memory": with_mem, "without": without, "tool_calls": calls,
                                "check": f["check"]})
                convo += [("A", f["probe"]), ("B", with_mem)]
            a_sys = (p["card"] + f"\nThis conversation is about: {topic}." +
                     (" Somewhere in it, mention naturally that " + "; and that ".join(to_say) + "." if to_say else ""))
            for t in range(TURNS):
                a_msgs = [{"role": "assistant" if sp == "A" else "user", "content": tx} for sp, tx in convo]
                if not a_msgs:
                    a_msgs = [{"role": "user", "content": "(Start the conversation.)"}]
                elif a_msgs[-1]["role"] == "assistant":
                    a_msgs.append({"role": "user", "content": "(Continue.)"})
                said = prov.chat(a_sys, a_msgs, 200).strip()
                convo.append(("A", said))
                reply, _ = b_reply(prov, server, convo, tlog, True)
                convo.append(("B", reply))
            record_messages(m, "assistant", [("person" if sp == "A" else "assistant", tx) for sp, tx in convo],
                            compartment="chat", record_time=when)
            transcript.append({"session": s, "when": when, "topic": topic, "conversation": convo, "tools": tlog})
            print(f"{p['name']} session {s}: {len(convo)} messages, {len(tlog)} tool calls, "
                  f"{prov.calls} model calls, {round(prov.neurons)} neurons so far", flush=True)
        (out / f"{p['name']}_transcript.json").write_text(json.dumps(transcript, indent=1), encoding="utf-8")
    (out / "probes.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (out / "usage.json").write_text(json.dumps({"provider": prov.kind, "model": prov.model, "calls": prov.calls,
                                                "tokens_in": prov.tokens_in, "tokens_out": prov.tokens_out,
                                                "neurons": prov.neurons}))
    print("usage:", prov.calls, "calls,", prov.tokens_in, "tokens in,", prov.tokens_out, "out,",
          round(prov.neurons), "neurons")


def score(a):
    out = Path(a.out)
    res = json.loads((out / "probes.json").read_text())
    hit = lambda text, words: any(w.lower() in text.lower() for w in words)  # noqa: E731
    w = sum(hit(r["with_memory"], r["check"]) for r in res)
    n = sum(hit(r["without"], r["check"]) for r in res)
    used = sum(bool(r["tool_calls"]) for r in res)
    print(f"{len(res)} probes: applied the planted fact with memory {w}, without {n}; "
          f"memory consulted on {used}")
    for r in res:
        print(f"  {r['persona']:6s} {r['fact']:9s} s{r['session']}  with {'Y' if hit(r['with_memory'], r['check']) else '.'}"
              f"  without {'Y' if hit(r['without'], r['check']) else '.'}  tools: "
              + ", ".join(c["call"].get("name", "?") for c in r["tool_calls"]))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["run", "score"])
    p.add_argument("--provider", default="fake", choices=["fake", "workers-ai", "anthropic"])
    p.add_argument("--model")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    {"run": run, "score": score}[a.cmd](a)
