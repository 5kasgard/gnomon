"""M4 interoperability: the official MCP Python SDK, as a real host would
use it, talking to the Gnomon server over stdio.

Gnomon itself has no dependencies; this test needs the SDK (`pip install
mcp`, 1.x or 2.x) and skips without it. CI runs it in a separate job.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))

from gnomon import Memory  # noqa: E402

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - the SDK is optional
    ClientSession = None
    if os.environ.get("GNOMON_REQUIRE_MCP_SDK"):
        raise  # in the interop CI job, a skip would hide that nothing ran


def f(obj, name):
    """A result field under SDK 1.x (camelCase) or 2.x (snake_case) names."""
    snake = "".join("_" + c.lower() if c.isupper() else c for c in name)
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, name)


@unittest.skipIf(ClientSession is None, "official MCP SDK not installed")
class OfficialClient(unittest.TestCase):
    def test_a_real_client_can_use_the_memory(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "memory.jsonl")
            m = Memory(log)
            m.register("john", "john", party="john", kind="human", capabilities=["owner"],
                       compartments=["lab"], record_time="2026-10-14T10:00:00Z")
            m.register("john", "claude-c1", party="anthropic", kind="model", model="claude-c1", operator="john", capabilities=["write", "verify"],
                       compartments=["lab"], record_time="2026-10-14T10:01:00Z")
            asyncio.run(self.session(log))

    async def session(self, log: str) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gnomon.mcp", "--log", log, "--writer", "claude-c1"],
            env=dict(os.environ, PYTHONPATH=str(PKG)),
            cwd=str(PKG),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                self.assertEqual(f(init, "serverInfo").name, "gnomon")
                names = {t.name for t in (await s.list_tools()).tools}
                self.assertTrue({"remember", "record_conversation", "recall", "query", "query_language"} <= names)

                r = await s.call_tool("record_conversation", {
                    "messages": [{"speaker": "John", "text": "Let's use Rust for the core."},
                                 {"speaker": "Claude", "text": "I'd suggest Python for the bindings."}],
                    "compartment": "lab", "frame": "stack"})
                self.assertFalse(f(r, "isError"), r)
                self.assertEqual(len(f(r, "structuredContent")["claims"]), 2)

                r = await s.call_tool("query", {"program": "?- decision(C), statement(C, S), speaker(C, P)."})
                self.assertFalse(f(r, "isError"), r)
                self.assertEqual(f(r, "structuredContent")["answers"][0][1:], ["Let's use Rust for the core.", "John"])
                self.assertTrue(f(r, "structuredContent")["proofs_checked"])
                self.assertIn("proofs checked independently: OK", r.content[0].text)

                r = await s.call_tool("remember", {"statement": "x", "frame": "f", "compartment": "elsewhere",
                                                   "truth": "true"})
                self.assertTrue(f(r, "isError"))


if __name__ == "__main__":
    unittest.main()
