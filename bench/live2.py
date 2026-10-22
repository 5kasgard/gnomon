"""Run 2 of the live loop (live.md): native tools, salience, forced and pushed memory,
verified planting. Run 1 is live.py.

A live loop: a simulated person (A) and an assistant (B) that knows A only
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
TOOLS = [
    {"type": "function", "function": {"name": "situation", "description":
        "What the person has said about their own situation (commitments, constraints, plans, preferences, "
        "their life), in their words, oldest first.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "context", "description":
        "What the record of earlier conversations holds that bears on a question.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "expand", "description": "The whole turn a [pointer] names.",
        "parameters": {"type": "object", "properties": {"pointer": {"type": "string"}}, "required": ["pointer"]}}},
]
NATIVE = """You are a helpful assistant talking with one person across many separate conversations. You do not
remember earlier conversations yourself, but you have memory tools. Answer briefly and naturally, applying
what you know about the person. Never mention the tools or the memory.{salience}"""
PUSH2 = """You are a helpful assistant talking with one person across many separate conversations.
Below is what your memory holds about them. Use whatever bears on their message; ignore the rest.
Answer briefly and naturally. Never mention the memory.

{memory}"""


def salience(server, n_sessions, last):
    if not n_sessions:
        return ""
    n = server.tool("situation", {})[1].get("lines", 0)
    return (f"\nYou have talked with this person {n_sessions} times before (most recently {last[:10]}); "
            f"your memory holds {n} things they have told you about themselves.")


def b_native(prov, server, convo, mode, sal=""):
    """B's reply in mode none / native / salient / forced / push. Returns (reply, tool calls)."""
    msgs = [{"role": "user" if s == "A" else "assistant", "content": t} for s, t in convo]
    if mode == "none":
        return prov.chat(B_NONE, msgs), []
    if mode == "push":
        mem_s, _ = server.tool("situation", {"budget": 3000})
        mem_c, _ = server.tool("context", {"query": convo[-1][1], "budget": 1500, "directive_budget": 0})
        return prov.chat(PUSH2.format(memory=mem_s + "\n\n" + mem_c), msgs), []
    system = NATIVE.format(salience=sal if mode in ("salient", "forced") else "")
    calls = []
    for step in range(MAX_TOOLS + 1):
        choice = "required" if (mode == "forced" and step == 0) else ("none" if step == MAX_TOOLS else "auto")
        m = prov.message(system, msgs, TOOLS, choice)
        tcs = m.get("tool_calls") or []
        if not tcs:
            return (m.get("content") or "").strip(), calls
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": tcs})
        for tc in tcs:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
                text, _ = server.tool(name, args)
            except Exception as e:
                args, text = {}, f"Tool error: {e}"
            calls.append({"name": name, "args": args, "result_tokens": len(text) // 4})
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", name), "content": text[:12000]})
    return "", calls


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
                            "User-Agent": "gnomon-live/1.0",  # Cloudflare refuses urllib's default agent
                            "CF-Access-Client-Id": os.environ.get("CF_ACCESS_ID", ""),
                            "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_SECRET", "")}
        elif kind == "anthropic":
            self.model = model or "claude-haiku-4-5"
            self.url = "https://api.anthropic.com/v1/messages"
            self.headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                            "content-type": "application/json"}
        else:
            self.model = "fake"

    def message(self, system: str, messages: list, tools=None, tool_choice=None, max_tokens: int = 400) -> dict:
        """The whole reply message (content and tool_calls), Workers AI only."""
        self.calls += 1
        body = {"model": self.model, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": system}] + messages}
        if tools:
            body["tools"], body["tool_choice"] = tools, tool_choice or "auto"
        for attempt in range(5):
            try:
                req = urllib.request.Request(self.url, json.dumps(body).encode(), self.headers)
                with urllib.request.urlopen(req, timeout=120) as r:
                    out = json.loads(r.read())
                if "error" in out:
                    raise RuntimeError(out["error"])
                break
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))
                print("  retry:", e, flush=True)
        u = out.get("usage", {})
        self.tokens_in += u.get("prompt_tokens", 0)
        self.tokens_out += u.get("completion_tokens", 0)
        self.neurons += u.get("neurons", 0) or 0
        return out["choices"][0]["message"]

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
        transcript, planted = [], {}
        for s in range(1, SESSIONS + 1):
            when = f"2026-{1 + (s - 1) // 4:02d}-{1 + 7 * ((s - 1) % 4):02d}T18:00:00Z"
            topic = p["topics"][(s - 1) % len(p["topics"])]
            to_say = [f["say"] for f in p["facts"] if f["session"] == s]
            probes = [f for f in p["facts"] if f["probe_session"] == s]
            convo, tlog = [], []
            # Probes open the session: B answers each with memory and, as the control, without.
            sal = salience(server, s - 1, transcript[-1]["when"] if transcript else "")
            for f in probes:
                first = [("A", f["probe"])]
                row = {"persona": p["name"], "fact": f["id"], "session": s, "probe": f["probe"], "check": f["check"],
                       "planted": planted.get(f["id"], False), "answers": {}, "calls": {}}
                for mode in ("none", "native", "salient", "forced", "push"):
                    reply, calls = b_native(prov, server, first, mode, sal)
                    row["answers"][mode], row["calls"][mode] = reply, calls
                results.append(row)
                convo += [("A", f["probe"]), ("B", row["answers"]["salient"])]
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
                reply, calls = b_native(prov, server, convo, "salient", sal)
                tlog += calls
                convo.append(("B", reply))
            # Verify planting: a fact whose key word A never said gets up to two explicit reminders.
            for f in [f for f in p["facts"] if f["session"] == s]:
                for _ in range(2):
                    if any(k in t.lower() for sp, t in convo if sp == "A" for k in f["key"]):
                        break
                    a_msgs = [{"role": "assistant" if sp == "A" else "user", "content": tx} for sp, tx in convo]
                    a_msgs.append({"role": "user", "content": f"(In your next message, mention naturally that {f['say']}.)"})
                    said = prov.chat(p["card"], a_msgs, 200).strip()
                    convo.append(("A", said))
                    reply, calls = b_native(prov, server, convo, "salient", sal)
                    tlog += calls
                    convo.append(("B", reply))
                planted[f["id"]] = any(k in t.lower() for sp, t in convo if sp == "A" for k in f["key"])
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
    modes = ("none", "native", "salient", "forced", "push")
    ok = [r for r in res if r["planted"]]
    print(f"{len(res)} probes, {len(ok)} with the fact planted")
    for m in modes:
        called = sum(bool(r["calls"][m]) for r in res)
        applied = sum(hit(r["answers"][m], r["check"]) for r in ok)
        toks = sum(c["result_tokens"] for r in res for c in r["calls"][m])
        print(f"  {m:8s} called memory on {called:2d}/{len(res)}   applied {applied}/{len(ok)}   memory read {toks} tokens")
    for r in ok:
        print(f"  {r['persona']:6s} {r['fact']:9s} " + "  ".join(
            f"{m} {'Y' if hit(r['answers'][m], r['check']) else '.'}{'*' if r['calls'][m] else ''}" for m in modes))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["run", "score"])
    p.add_argument("--provider", default="fake", choices=["fake", "workers-ai", "anthropic"])
    p.add_argument("--model")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    {"run": run, "score": score}[a.cmd](a)
