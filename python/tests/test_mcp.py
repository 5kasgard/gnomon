"""M4: the MCP server, driven as a real subprocess over stdio. Standard
library only.

Both protocol eras (modern per-request `_meta`, legacy `initialize`), every
tool, identity pinning (the model cannot write as anyone else), compartments,
and malformed input that must never kill the server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))

from gnomon import Memory, claim  # noqa: E402

V = "2026-07-28"
META = {"io.modelcontextprotocol/protocolVersion": V,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"}}


def t(minute: int) -> str:
    return f"2026-10-14T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"


class Proc:
    def __init__(self, *args: str):
        env = dict(os.environ, PYTHONPATH=str(PKG))
        self.p = subprocess.Popen([sys.executable, "-m", "gnomon.mcp", *args], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(PKG))
        self.n = 0

    def raw(self, line: bytes) -> dict:
        self.p.stdin.write(line + b"\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def send(self, method: str, params: dict | None = None, modern: bool = True, notify: bool = False):
        params = dict(params or {})
        if modern:
            params["_meta"] = dict(META, **params.get("_meta", {}))
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if notify:
            self.p.stdin.write(json.dumps(msg).encode() + b"\n")
            self.p.stdin.flush()
            return None
        self.n += 1
        msg["id"] = self.n
        return self.raw(json.dumps(msg).encode())

    def call(self, name: str, args: dict, modern: bool = True) -> dict:
        r = self.send("tools/call", {"name": name, "arguments": args}, modern)
        return r["result"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
        self.p.stdout.close()
        self.p.stderr.close()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "memory.jsonl")
        m = self.m = Memory(self.log)
        m.register("john", "john", party="john", kind="human", capabilities=["owner"],
                   compartments=["lab", "private"], record_time=t(0))
        m.register("john", "claude-c1", party="anthropic", kind="model", model="claude-c1", operator="john", capabilities=["write", "verify"],
                   compartments=["lab"], record_time=t(1))
        m.register("john", "gpt-g1", party="openai", kind="model", model="gpt-g1", operator="john", capabilities=["write", "verify"],
                   compartments=["lab"], record_time=t(1))
        m.register("john", "calc", party="gnomon-calc", kind="tool", operator="john", capabilities=["verify"],
                   compartments=["lab"], record_time=t(1))
        m.remember("john", claim("Supplier is Acme", speaker="john", frame="f", compartment="private",
                                 stance="decided", truth="true", origin="manual"), record_time=t(2))
        self.s = Proc("--log", self.log, "--writer", "claude-c1", "--checker", "calc")

    def tearDown(self):
        self.s.close()
        self.dir.cleanup()


class Modern(Fixture):
    def test_discover(self):
        r = self.s.send("server/discover")["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertIn(V, r["supportedVersions"])
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "gnomon")

    def test_versions_and_required_meta(self):
        r = self.s.send("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}})
        self.assertEqual(r["error"]["code"], -32022)
        self.assertEqual(r["error"]["data"]["requested"], "1900-01-01")
        self.assertIn(V, r["error"]["data"]["supported"])
        r = self.s.raw(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": V}}}).encode())
        self.assertEqual(r["error"]["code"], -32602)  # clientCapabilities is required
        r = self.s.send("tools/list", modern=False)
        self.assertEqual(r["error"]["code"], -32602)  # no _meta and no initialize

    def test_tools_list_is_valid_and_deterministic(self):
        a = self.s.send("tools/list")["result"]["tools"]
        b = self.s.send("tools/list")["result"]["tools"]
        self.assertEqual(a, b)
        for tool in a:
            self.assertRegex(tool["name"], r"^[A-Za-z0-9_.-]{1,128}$")
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_remember_then_query_with_checked_proofs(self):
        r = self.s.call("remember", {"statement": "We will use Rust for the core", "frame": "gnomon",
                                     "compartment": "lab", "truth": "true", "stance": "decided",
                                     "confidence": 9000, "origin": "manual"})
        self.assertFalse(r["isError"], r)
        cid = r["structuredContent"]["claim"]
        q = self.s.call("query", {"program": "?- decision(C), statement(C, S), author(C, W)."})
        self.assertFalse(q["isError"], q)
        sc = q["structuredContent"]
        self.assertEqual(sc["answers"], [[cid, "We will use Rust for the core", "claude-c1"]])
        self.assertTrue(sc["proofs_checked"])
        self.assertIn("by view: decision(C)", sc["explanations"][0])
        self.assertIn("proofs checked independently: OK", q["content"][0]["text"])

    def test_recall_finds_where_it_was_said(self):
        r = self.s.call("record_conversation", {
            "messages": [{"speaker": "John", "text": "Which database did we pick yesterday?"},
                         {"speaker": "Claude", "text": "We settled on SQLite for the prototype."}],
            "compartment": "lab", "frame": "chat", "extract": False})
        self.assertFalse(r["isError"], r)
        r = self.s.call("recall", {"query": "what database did John ask about", "kinds": ["turns"], "context": 1})
        self.assertFalse(r["isError"], r)
        hits = r["structuredContent"]["hits"]
        self.assertEqual(hits[0]["speaker"], "John")
        text = r["content"][0]["text"]
        self.assertIn("John: Which database did we pick yesterday? <", text)
        self.assertIn("yesterday=", text)
        self.assertIn("#1 Claude: We settled on SQLite for the prototype.", text)
        r = self.s.call("recall", {"query": "x", "k": 0})
        self.assertTrue(r["isError"])
        r = self.s.call("recall", {"query": "x", "kinds": ["pages"]})
        self.assertTrue(r["isError"])

    def test_context_tiers_and_expand(self):
        r = self.s.call("record_conversation", {
            "messages": [{"speaker": "John", "text": "Always give times in 24-hour format. We ship on March 3."},
                         {"speaker": "Claude", "text": "Always glad to help. Shipping is set for March 3."}],
            "compartment": "lab", "frame": "chat", "extract": False})
        self.assertFalse(r["isError"], r)
        r = self.s.call("context", {"query": "when do we ship", "budget": 300})
        self.assertFalse(r["isError"], r)
        text, sc = r["content"][0]["text"], r["structuredContent"]
        self.assertTrue(text.startswith("Standing instructions"))
        self.assertIn("John: Always give times in 24-hour format.", text)
        self.assertNotIn("Always glad to help", text.split("From the record")[0])
        self.assertTrue(sc["view"])
        r = self.s.call("expand", {"pointer": sc["view"][0]["pointer"]})
        self.assertFalse(r["isError"], r)
        self.assertIn("March 3", r["content"][0]["text"])
        r = self.s.call("expand", {"pointer": "nobody/1#0"})
        self.assertTrue(r["isError"])

    def test_record_conversation_extracts_and_leaves_residue(self):
        r = self.s.call("record_conversation", {
            "messages": [{"speaker": "John", "text": "Let's use Rust for the core. Maybe Python on top?"},
                         {"speaker": "Claude", "text": "Agreed."}],
            "compartment": "lab", "frame": "chat"})
        self.assertFalse(r["isError"], r)
        sc = r["structuredContent"]
        self.assertEqual({(c["speaker"], c["stance"]) for c in sc["claims"]}, {("John", "decided"), ("John", "asked")})
        self.assertEqual([x["sentence"] for x in sc["residue"]], ["Agreed."])
        q = self.s.call("query", {"program": '?- decision(C), speaker(C, "John").'})
        self.assertEqual(len(q["structuredContent"]["answers"]), 1)
        self.assertTrue(q["structuredContent"]["proofs_checked"])

    def test_the_model_cannot_be_anyone_else(self):
        r = self.s.call("remember", {"statement": "x", "frame": "f", "compartment": "lab", "truth": "true",
                                     "writer": "john"})
        self.assertTrue(r["isError"])
        self.assertIn("unknown field 'writer'", r["content"][0]["text"])
        # Attribution to someone else is hearsay: capped by the core.
        r = self.s.call("remember", {"statement": "John approved the budget", "frame": "f", "compartment": "lab",
                                     "truth": "true", "speaker": "john", "confidence": 10000})
        self.assertTrue(r["isError"])
        self.assertIn("E_ATTRIBUTION", r["content"][0]["text"])

    def test_compartments_hold(self):
        r = self.s.call("remember", {"statement": "secret", "frame": "f", "compartment": "private", "truth": "true"})
        self.assertTrue(r["isError"])
        self.assertIn("Refused by the memory's rules", r["content"][0]["text"])
        q = self.s.call("query", {"program": '?- statement(C, S), contains(S, "acme").'})
        self.assertEqual(q["structuredContent"]["answers"], [])

    def test_verdicts_and_self_verification(self):
        mine = self.s.call("remember", {"statement": "2 + 2 = 4", "frame": "f", "compartment": "lab",
                                        "truth": "true"})["structuredContent"]["claim"]
        r = self.s.call("give_verdict", {"claim": mine, "result": "supports", "method": "test"})
        self.assertTrue(r["isError"])
        self.assertIn("E_SELF_VERIFY", r["content"][0]["text"])
        other = self.m.remember("gpt-g1", claim("3 + 3 = 7", speaker="gpt-g1", frame="f", compartment="lab",
                                                truth="true"))
        r = self.s.call("give_verdict", {"claim": other, "result": "refutes", "method": "test", "note": "6"})
        self.assertFalse(r["isError"], r)

    def test_native_checkers(self):
        self.m.remember("gpt-g1", claim("2 + 2 = 5", speaker="gpt-g1", frame="f", compartment="lab", truth="true"))
        r = self.s.call("run_checkers", {})
        self.assertFalse(r["isError"], r)
        self.assertEqual([v["result"] for v in r["structuredContent"]["verdicts"]], ["refutes"])
        q = self.s.call("query", {"program": '?- challenged(C), statement(C, S).'})
        self.assertEqual(q["structuredContent"]["answers"][0][1], "2 + 2 = 5")

    def test_bad_queries_are_actionable(self):
        r = self.s.call("query", {"program": "?- decisoin(C)."})
        self.assertTrue(r["isError"])
        self.assertIn("unknown relation 'decisoin/1'", r["content"][0]["text"])
        r = self.s.call("query_language", {})
        self.assertIn("believed(C)", r["content"][0]["text"])

    def test_verify_log(self):
        r = self.s.call("verify_log", {})
        self.assertFalse(r["isError"])


class Legacy(Fixture):
    def test_initialize_then_tools_without_meta(self):
        r = self.s.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "old", "version": "1"}}, modern=False)["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertNotIn("resultType", r)
        self.assertIn("instructions", r)
        self.s.send("notifications/initialized", modern=False, notify=True)
        tools = self.s.send("tools/list", modern=False)["result"]["tools"]
        self.assertIn("query", {x["name"] for x in tools})
        r = self.s.call("remember", {"statement": "Legacy works", "frame": "f", "compartment": "lab",
                                     "truth": "true"}, modern=False)
        self.assertFalse(r["isError"])
        self.assertNotIn("resultType", r)

    def test_unknown_legacy_version_gets_ours(self):
        r = self.s.send("initialize", {"protocolVersion": "2024-01-01", "capabilities": {},
                                       "clientInfo": {"name": "old", "version": "1"}}, modern=False)["result"]
        self.assertEqual(r["protocolVersion"], "2025-11-25")


class Robustness(Fixture):
    def test_garbage_never_kills_the_server(self):
        for line, code in [(b"{not json", -32700), (b"[1, 2]", -32600), (b'{"jsonrpc": "1.0", "id": 1}', -32600),
                           (b'{"jsonrpc": "2.0", "id": null, "method": "ping"}', -32600),
                           (b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": 3}', -32602),
                           ("ÿ".encode("latin-1"), -32700)]:
            self.assertEqual(self.s.raw(line)["error"]["code"], code, line)
        self.assertEqual(self.s.send("nope/nothing")["error"]["code"], -32601)
        self.assertEqual(self.s.send("tools/call", {"name": "rm_rf", "arguments": {}})["error"]["code"], -32602)
        r = self.s.call("remember", {"statement": 5, "frame": "f", "compartment": "lab", "truth": "true"})
        self.assertTrue(r["isError"])
        r = self.s.call("record_conversation", {"messages": [{"speaker": "A"}], "compartment": "lab", "frame": "f"})
        self.assertTrue(r["isError"])
        self.assertIn("'messages[0]': 'text' is required", r["content"][0]["text"])
        # A notification gets no reply; the next request still works.
        self.s.send("notifications/cancelled", {"requestId": 1}, notify=True)
        self.assertIn("result", self.s.send("ping"))

    def test_resource_limits_come_back_as_tool_errors(self):
        r = self.s.call("query", {"program": "".join(f"n({i}). " for i in range(100))
                                  + "p(A, B, C, D) :- n(A), n(B), n(C), n(D). ?- p(A, B, C, D)."})
        self.assertTrue(r["isError"])
        self.assertIn("E_QUERY_LIMIT", r["content"][0]["text"])
        self.assertIn("result", self.s.send("ping"))


class ReviewM4(Fixture):
    """Every confirmed finding from the M4 security review."""

    def test_checkers_report_only_what_the_writer_can_see(self):
        # Finding 1: the checker saw `private`, and its results came back.
        self.s.close()
        self.m.register("john", "calc2", party="gnomon-calc2", kind="tool", operator="john", capabilities=["verify"],
                        compartments=["lab", "private"])
        secret = self.m.remember("john", claim("Secret budget: 1234 + 1 = 1236", speaker="john", frame="f",
                                               compartment="private", truth="true", origin="manual"))
        self.m.remember("gpt-g1", claim("2 + 2 = 5", speaker="gpt-g1", frame="f", compartment="lab", truth="true"))
        self.s = Proc("--log", self.log, "--writer", "claude-c1", "--checker", "calc2")
        r = self.s.call("run_checkers", {})
        text = json.dumps(r)
        self.assertNotIn(secret, text)
        self.assertNotIn("1234", text)
        self.assertEqual(len(r["structuredContent"]["verdicts"]), 1)
        self.assertEqual(self.m.state()["state"]["claims"][secret]["refutes"], [])  # nothing written there

    def test_no_line_can_kill_the_server(self):
        # Finding 2: each of these ended the process.
        for line in [b"[" * 100000 + b"]" * 100000,
                     b'{"jsonrpc":"2.0","id":1,"method":"ping","params":' + b'{"a":' * 5000 + b"1" + b"}" * 5000 + b"}",
                     b'{"jsonrpc":"1.0","id":NaN,"method":"x"}',
                     b'{"jsonrpc":"2.0","id":1e400,"method":"ping"}',
                     b'{"jsonrpc":"2.0","id":1,"method":"\\ud800","params":{"_meta":' + json.dumps(META).encode() + b'}}',
                     b'{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}',
                     b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"query_language",'
                     b'"arguments":{"\\ud800":1},"_meta":' + json.dumps(META).encode() + b'}}']:
            r = self.s.raw(line)
            self.assertIn("error", r, line[:60])
            self.assertIn("result", self.s.send("ping"), line[:60])

    def test_fuzzed_messages_always_get_one_well_formed_reply(self):
        import random
        rng = random.Random(4)
        atoms = [None, True, 0, -1, 2**70, 1.5, "", "x", "\u2028", "tools/call", "ping", "2.0", "2026-07-28",
                 "remember", "query", [], {}, "\U0001F600"]

        def value(d=0):
            r = rng.random()
            if d > 4 or r < 0.5:
                return rng.choice(atoms)
            if r < 0.7:
                return [value(d + 1) for _ in range(rng.randint(0, 3))]
            keys = ["jsonrpc", "id", "method", "params", "_meta", "name", "arguments", "program", "statement",
                    "io.modelcontextprotocol/protocolVersion", "io.modelcontextprotocol/clientCapabilities"]
            return {rng.choice(keys): value(d + 1) for _ in range(rng.randint(0, 4))}

        for i in range(600):
            msg = value()
            if isinstance(msg, dict) and rng.random() < 0.7:
                msg.update({"jsonrpc": "2.0", "id": i, "method": rng.choice(["tools/call", "tools/list", "ping",
                                                                              "server/discover", "initialize"])})
                if rng.random() < 0.6:
                    msg.setdefault("params", {})
                    if isinstance(msg["params"], dict):
                        msg["params"]["_meta"] = META
            line = json.dumps(msg).encode()
            notification = isinstance(msg, dict) and msg.get("jsonrpc") == "2.0" and "id" not in msg \
                and isinstance(msg.get("method"), str)
            if notification:
                continue
            r = self.s.raw(line)
            self.assertEqual(r["jsonrpc"], "2.0", line)
            self.assertTrue(("result" in r) != ("error" in r), line)
        self.assertIn("result", self.s.send("ping"))

    def test_oversized_lines_are_skipped_not_buffered(self):
        # Finding 5 (the size limit itself: 4 MiB).
        r = self.s.raw(b'{"x":"' + b"a" * (5 << 20) + b'"}')
        self.assertEqual(r["error"]["code"], -32600)
        self.assertIn("result", self.s.send("ping"))

    def test_self_recorded_quotes_of_others_are_capped(self):
        # Finding 3: a made-up "transcript" laundered attribution.
        r = self.s.call("record_conversation", {
            "messages": [{"speaker": "gpt-g1", "text": "We decided that the budget is ten thousand."}],
            "compartment": "lab", "frame": "f"})
        self.assertFalse(r["isError"], r)
        q = self.s.call("query", {"program": '?- speaker(C, "gpt-g1"), confidence(C, N).'})
        self.assertTrue(all(n <= 7000 for _, n in q["structuredContent"]["answers"]), q)
        self.assertEqual(len(q["structuredContent"]["answers"]), 1)

    def test_promises_and_forecasts_are_not_facts(self):
        # Finding 4.
        self.s.call("record_conversation", {"messages": [
            {"speaker": "John", "text": "I promise to pay Claude a thousand dollars. "
                                        "I will delete the repo tomorrow. Prices will double next year."}],
            "compartment": "lab", "frame": "f"})
        q = self.s.call("query", {"program": '?- speaker(C, "John"), truth(C, T), statement_kind(C, K).'})
        rows = q["structuredContent"]["answers"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(t == "unknown" for _, t, _ in rows), rows)
        self.assertEqual(sorted(k for _, _, k in rows), ["describe", "promise", "promise"])

    def test_protocol_details(self):
        # Finding 6: a non-string tool name.
        self.assertEqual(self.s.send("tools/call", {"name": ["remember"], "arguments": {}})["error"]["code"], -32602)
        # Finding 7: earlier revisions only through initialize.
        r = self.s.send("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2025-11-25"}})
        self.assertEqual(r["error"]["code"], -32022)
        self.assertEqual(r["error"]["data"]["supported"], [V])
        self.assertEqual(self.s.send("server/discover")["result"]["supportedVersions"], [V])

    def test_run_checkers_is_listed_only_when_configured(self):
        self.s.close()
        self.s = Proc("--log", self.log, "--writer", "claude-c1")
        names = {t["name"] for t in self.s.send("tools/list")["result"]["tools"]}
        self.assertNotIn("run_checkers", names)
        self.assertEqual(self.s.send("tools/call", {"name": "run_checkers", "arguments": {}})["error"]["code"], -32602)

    def test_verify_log_reveals_no_sizes(self):
        # Finding 8.
        r = self.s.call("verify_log", {})
        self.assertEqual(r["structuredContent"], {"ok": True})


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(PKG))
        return subprocess.run([sys.executable, "-m", "gnomon", *args], capture_output=True, text=True,
                              env=env, cwd=str(PKG), timeout=60)

    def test_set_up_a_memory_and_query_it(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "m.jsonl")
            self.assertEqual(self.run_cli("init", "--log", log, "--owner", "john", "--compartments", "lab").returncode, 0)
            self.assertEqual(self.run_cli("init", "--log", log, "--owner", "eve", "--compartments", "lab").returncode, 1)
            r = self.run_cli("register", "--log", log, "--by", "john", "--writer", "claude-c1", "--party", "anthropic",
                             "--kind", "model", "--capabilities", "write", "--compartments", "lab")
            self.assertEqual(r.returncode, 1)  # M6: which model it runs must be said
            self.assertIn("must name its 'model'", r.stderr)
            r = self.run_cli("register", "--log", log, "--by", "john", "--writer", "claude-c1", "--party", "anthropic",
                             "--kind", "model", "--model", "claude-opus-5-5", "--surface", "claude-code",
                             "--session", "session_01LL", "--name", "Claude", "--capabilities", "write",
                             "--compartments", "lab")
            self.assertEqual(r.returncode, 0, r.stderr)
            w = Memory(log).state()["state"]["writers"]["claude-c1"]
            self.assertEqual((w["operator"], w["model"], w["group"]), ("john", "claude-opus-5-5", "model:claude-opus-5-5"))
            r = self.run_cli("register", "--log", log, "--by", "claude-c1", "--writer", "evil", "--party", "x",
                             "--kind", "model", "--capabilities", "owner", "--compartments", "lab")
            self.assertEqual(r.returncode, 1)
            self.assertIn("E_CAPABILITY", r.stderr)
            Memory(log).remember("claude-c1", claim("Ship it", speaker="claude-c1", frame="f", compartment="lab",
                                                    stance="decided", truth="true", origin="manual"))
            r = self.run_cli("query", "--log", log, "--reader", "john", "?- decision(C), statement(C, S).")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("1 answer(s)", r.stdout)
            self.assertIn("proofs check out", r.stdout)
            self.assertIn("by view: decision(C)", r.stdout)
            self.assertIn("ok: 3 events", self.run_cli("verify", "--log", log).stdout)

    def test_failed_commands_leave_no_files(self):
        # Review M4, finding 9.
        with tempfile.TemporaryDirectory() as d:
            for args in (["register", "--log", str(Path(d) / "a.jsonl"), "--by", "john", "--writer", "w", "--party",
                          "p", "--kind", "model", "--capabilities", "write", "--compartments", "lab"],
                         ["init", "--log", str(Path(d) / "b.jsonl"), "--owner", "john", "--compartments", ",,"]):
                self.assertEqual(self.run_cli(*args).returncode, 1)
            self.assertEqual(list(Path(d).iterdir()), [])


class Startup(unittest.TestCase):
    def test_unknown_writer_is_refused_at_start(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "m.jsonl")
            Memory(log).register("john", "john", party="john", kind="human", capabilities=["owner"],
                                 compartments=["lab"], record_time=t(0))
            env = dict(os.environ, PYTHONPATH=str(PKG))
            p = subprocess.run([sys.executable, "-m", "gnomon.mcp", "--log", log, "--writer", "mallory"],
                               capture_output=True, env=env, cwd=str(PKG), timeout=30)
            self.assertEqual(p.returncode, 2)
            self.assertIn(b"not registered", p.stderr)
            self.assertEqual(p.stdout, b"")


if __name__ == "__main__":
    unittest.main()
