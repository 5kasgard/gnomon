You are an independent reviewer of a small security fix and the experiment that chose it. Be critical; you are not asked to agree.

Context: Gnomon is a hash-chained memory log. Writers hold "compartments"; a reader sees only events in compartments it holds. "Auto mode" is a Claude Code hook that records every session's conversation into ONE compartment Y. Problem (issue #51): a session whose writer holds X and Y can read X content and the hook then files it into Y, where Y-only readers see it. Several fixes were tested; D1 was chosen. Below: the pre-registration, results, and D1's code diff.

Answer in under 400 words, as a numbered list:
1. Is there a way X content still reaches Y through Gnomon under D1 that the 8 tested roads miss? Be concrete.
2. Is anything in the experiment unfair to an arm, or does any reported result not follow from the data?
3. Bugs or risks in D1's diff (races, upgrades, error handling, the marker file).
4. Verdict: ship D1 / ship with changes (which) / do not ship.

===== PREREG.md =====
# Issue #51: which fix stops auto mode carrying content between compartments?

Pre-registered 2026-10-29, before any arm is built or run. Amend only before data exists.

## Question

In auto mode the hook files a whole session into one compartment (Y), though the session may
have taken in content from another compartment (X) the writer also holds. Which fix closes the
roads Gnomon itself opens, at the least cost to normal use?

## Prior (frozen before our data)

- Coarse, whole-context tracking (a session's label rises with everything it reads, the
  "high-water mark") suffers label creep: one sensitive read taints everything after it. The
  standard remedy is to split the work into compartmentalised processes (LIO tutorial,
  arXiv 2208.13560). FIDES (Microsoft, arXiv 2505.23643) reports the same whole-conversation
  taint in LLM agents and works around it by keeping restricted content out of the context.
- So the prior favours arms A and D over C; B and C also depend on seeing every read in the
  transcript, which the hook's own injected context may not show.

## Arms

- **0, main:** as today (baseline; should leak).
- **A, one-compartment writers:** auto mode refuses a template writer holding more than one
  compartment (no recording, a clear message on stderr; the session itself is never broken).
- **B, refuse mixed turns:** record a chunk only if nothing in it came from a compartment other
  than Y; otherwise record a note instead, as the hook does for refusals today.
- **C, file where it came from:** record each chunk into Y plus every compartment it drew on.
- **D, one-compartment sessions:** the session's instance writer is spawned holding only Y (the
  gate already allows a subset); everything the session reads through Gnomon is then from Y.

B and C detect "drew on" from what the transcript shows (Gnomon MCP tool results), the
strongest detection the hook can do without a change to the core.

## Channels (each a scenario on a fresh log; canary text planted only in X)

1. Gnomon MCP recall of X content during the session.
2. Start context: resume packet of a frame in X.
3. Start context: the person's situation lines recorded in X.
4. Per-message context (`--per-message`) drawn from X.
5. After compaction: the model restates X content; the tool call that fetched it was in an
   earlier, already-recorded part of the transcript.
6. A sub-agent (SubagentStop) quotes X content.
7. The session uses the Gnomon MCP server without calling `attach`, so its calls run as the
   writer the server was configured with (the template, holding X and Y). This channel can
   defeat D, so it is listed before D is built.

8. Write-back: the session reads X content and writes it into Y itself through a Gnomon write
   tool (`handoff`, which the start context asks for, or another write tool).

## Amendment 1 (2026-10-29, before any arm is built or run)

From a review by a separate Claude Haiku agent (verbatim notes in the commit that adds the
result). Changes:

- Every arm runs the hook with `--compartment Y` pinned (the default, the first compartment,
  could be X).
- Channel 8 (write-back) added above, tested in every arm.
- D includes what channel 7 needs: with `--per-session`, the MCP server today acts as the
  template (X and Y) until `attach`; D refuses every tool but `attach` until then. Its lines count.
- The hook today keeps only text blocks of a transcript, so B and C must add code that reads
  Gnomon tool results and where they came from; that code counts in their size. If it cannot be
  built, the arm is reported as not buildable, not dropped silently.
- u2 is now: share of the turns of a two-compartment template's session, drawing only on Y,
  that are recorded word for word in Y. u4 is now: the owner, outside auto mode (the CLI or an
  MCP server run as the template), can write a sentence quoting X content into Y. Same check
  in every arm.
- The harness calls no model: restatements are scripted, the same script for every arm, and
  canaries are matched exactly. So paraphrase is not tested; that weakens B and C's detection
  in real use more than A and D, which do not detect anything. Recorded as a limit.
- Size counts all non-test changes, including `settings()` and the MCP server.

## Measures (all exact, by script)

- **Leak:** for each channel, can a reader holding only Y find the canary? (yes or no)
- **Widening:** text that existed only in Y, now readable by an X-only reader (C's risk).
- **Use:** (u1) a single-compartment template records as before; (u2) a two-compartment
  template can run auto mode in some supported way; (u3) the session can still recall Y content;
  (u4) a person can still move X content into Y on purpose by a route someone chose.
- **Size:** net lines changed in `python/gnomon`, tests excluded.

## Decision rule (fixed now)

1. Keep the arms that close all eight channels with no widening.
2. Among those, prefer the fewest use failures, then the fewest lines.
3. If no arm closes all eight, report every arm and the owner decides.

Out of scope, recorded as a limit: content reaching the session by roads Gnomon does not open
(the person pastes it, files on disk, issue #50). No arm here can see those.

## Routing, cost, review

- Plan and this file: Claude Opus. Arms and harness: a Claude Sonnet sub-agent, from a brief.
  Numbers: the harness script. Review of this file and the result: one non-Claude reviewer
  (a prompt file the owner can paste into Gemini) and a separate Claude Haiku agent.
- Paid API cost: $0. No model is called by the harness.
- Author bias: the author (Opus) proposed arm D and leans to it; the channels were listed
  before building, and the reviewers are asked whether a channel that favours D is missing.

===== RESULTS.md =====
# Issue #51: results

Test: `PREREG.md` (with amendment 1). Arms and harness built by a Claude Sonnet sub-agent;
numbers from `run.py` via `collect.py`, saved in `results.json`. Rerun independently by the
planner (Claude Opus) on every branch: identical on every measure (only the recorded commit
id differs, as the results were committed after the run). No model was called; $0.

## First test (pre-registered)

| Arm | Roads 1-8 leaking | Widening | u1 | u2 | u3 | u4 | Lines +/- | Existing suite (290) |
|---|---|---|---|---|---|---|---|---|
| 0 (main) | all 8 | no | yes | 6/6 | yes | yes | 0 | pass |
| A one-compartment writers | 8 | no | yes | **0/6** | yes | yes | +8/-0 | pass |
| B refuse mixed turns | 8 | no | yes | 4/6 | yes | yes | +144/-0 | pass |
| C file where it came from | **all 8** | **yes** | yes | 6/6 | yes | yes | +168/-16 | pass |
| C2 (extra, not registered) | 8 | **yes** | yes | 2/6 | yes | yes | +168/-16 | pass |
| D one-compartment sessions | **none** | no | yes | 6/6 | yes | yes | +21/-14 | **7 fail** |

Arm 0 leaks on every road, so every scenario exercises the road it names.

By the decision rule, only D closes all eight roads with no widening. But D breaks seven
existing tests, which the pre-registered use measures (u1-u4) did not cover: they test a
documented feature, a `--per-session` MCP server used **without** the hook, which today starts
its own writer on the first tool call. D makes that server refuse every tool until `attach`,
and without the hook there is no token to attach with. That gap in the measures is the
author's; it was found by running the existing suite, not by u1-u4.

What the data says about each arm:

- **A** stops recording (u2 0/6) and still leaks by write-back (road 8): the model still reads X
  through the MCP server and writes it into Y itself.
- **B** leaks by write-back, costs 144 lines, and drops a third of the clean turns once a
  session has read X (taint carries forward).
- **C** does what the prior predicted: it files X content into Y as well, so it closes nothing,
  and widens (Y content becomes visible to X-only readers). C2 widens too.
- **B and C** detect reads by matching text word for word, so a paraphrase would pass them;
  that was not tested (no model), and A and D do not depend on detection.

Matches the outside prior (whole-session taint tracking suffers label creep; splitting into
single-compartment processes is the standard remedy), but the decisive numbers are these.

## Follow-up test D1 (registered after the first results, before D1 is built)

Hypothesis: D's break is only in its MCP change, which can be narrowed. **D1** = D, except the
per-session MCP server refuses unattached use only for a template that auto mode runs (the hook
records that it runs it, e.g. a marker under `<log>.d/`); for any other template, unattached use
starts an instance as today.

Measures: the same harness (roads 1-8, widening, u1-u4, size), plus the full existing suite,
plus **u5**: a template used both by auto mode and by a standalone per-session server: does the
standalone server still work? (Expected: no; reported either way, as D1's known cost.)

Decision: D1 replaces D if it closes all eight roads with no widening and passes the existing
suite unchanged; otherwise both D and D1 go to the owner with their costs.

D1 result (follow-up test, reproduced independently): roads 1-8 leaking: none; widening: no; u1 yes, u2 6/6, u3 yes, u4 yes, u5 false; +39/-8 lines; existing suite 290/290 pass. u5 true for arms 0, A, B, C, C2; false for D, D1.

===== D1 diff (python/gnomon) =====
diff --git a/python/gnomon/hook.py b/python/gnomon/hook.py
index ca321ce..927eb48 100644
--- a/python/gnomon/hook.py
+++ b/python/gnomon/hook.py
@@ -44,7 +44,7 @@ from typing import Any, Optional
 
 from . import GateError, Memory, title
 from .ingest import record_messages
-from .session import instance_for, issue_token, outstanding_tokens
+from .session import instance_for, issue_token, mark_auto, outstanding_tokens
 
 MAX_EPISODE = 90_000  # characters per recorded episode (the core allows 100,000)
 
@@ -350,9 +350,13 @@ def main(argv: Optional[list] = None) -> int:
         session = str(event.get("session_id") or "")
         if not session:
             return 0
+        mark_auto(a.log, a.writer)
         memory = Memory(a.log)
-        writer = instance_for(memory, a.writer, session, surface="claude-code")
-        compartment = a.compartment or memory.writers()[writer]["compartments"][0]
+        held = memory.writers().get(a.writer, {}).get("compartments") or [None]
+        compartment = a.compartment or held[0]
+        # The session's writer holds only the compartment it is recorded in,
+        # so nothing it reads through Gnomon can come from another.
+        writer = instance_for(memory, a.writer, session, surface="claude-code", compartments=[compartment])
         transcript = event.get("transcript_path")
         if name == "PreCompact":
             _mark_compaction(a.log, session)
diff --git a/python/gnomon/mcp.py b/python/gnomon/mcp.py
index e19e779..1366c1b 100644
--- a/python/gnomon/mcp.py
+++ b/python/gnomon/mcp.py
@@ -30,7 +30,7 @@ import uuid
 
 from . import GateError, Memory, VIEWS, __version__, _native, claim, checkers, proofs, title
 from .ingest import ingest, record_messages
-from .session import instance_for, redeem_token
+from .session import instance_for, redeem_token, runs_auto
 
 MODERN = ["2026-07-28"]
 LEGACY = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
@@ -514,6 +514,10 @@ class Server:
         if name == "attach":
             return self.t_attach(args)
         if self.parent is not None and self.writer == self.parent:
+            if runs_auto(self.log, self.parent):
+                # Auto mode runs this template: it may hold compartments the session's writer does not.
+                raise ToolError("this session has no writer yet: call `attach` with the token in your session "
+                                "context first")
             self._start_instance()
         fn: Callable[[dict], tuple[str, Any]] = getattr(self, "t_" + name)
         self.results += 1
diff --git a/python/gnomon/session.py b/python/gnomon/session.py
index 16874a3..fce8aa1 100644
--- a/python/gnomon/session.py
+++ b/python/gnomon/session.py
@@ -26,18 +26,26 @@ from . import GateError, Memory
 
 
 def instance_for(memory: Memory, parent: str, session: Optional[str] = None,
-                 surface: Optional[str] = None) -> str:
+                 surface: Optional[str] = None, compartments: Optional[list] = None) -> str:
     """The writer for `session`, an instance of `parent`: the existing one
-    if this session has one, otherwise a new one (`parent-<n>`)."""
+    if this session has one, otherwise a new one (`parent-<n>`). With
+    `compartments`, a new instance holds only those (a subset of its
+    parent's), and an existing one must hold exactly those."""
     writers = memory.writers()
     if parent not in writers:
         raise GateError("E_UNKNOWN_WRITER", f"writer '{parent}' is not registered")
     p = writers[parent]
     if "spawn" not in p["capabilities"]:
         raise GateError("E_CAPABILITY", f"'{parent}' needs the 'spawn' capability to start session instances")
+    comps = p["compartments"] if compartments is None else list(compartments)
+    if any(c not in p["compartments"] for c in comps):
+        raise GateError("E_COMPARTMENT", f"'{parent}' does not hold {', '.join(c for c in comps if c not in p['compartments'])}")
     if session:
         for wid, w in writers.items():
             if w.get("instance_of") == parent and w.get("session") == session:
+                if compartments is not None and sorted(w["compartments"]) != sorted(comps):
+                    raise GateError("E_COMPARTMENT", f"this session's writer {wid} holds {', '.join(w['compartments'])}, "
+                                                     f"not only {', '.join(comps)}: start a new session")
                 return wid
     session = session or uuid.uuid4().hex[:16]
     for n in range(1, 1_000_000):
@@ -50,15 +58,30 @@ def instance_for(memory: Memory, parent: str, session: Optional[str] = None,
     caps = [c for c in p["capabilities"] if c not in ("owner", "govern", "spawn")]
     try:
         memory.register(parent, wid, party=p["party"], kind=p["kind"], capabilities=caps,
-                        compartments=p["compartments"], name=p.get("name"), model=p.get("model"),
+                        compartments=comps, name=p.get("name"), model=p.get("model"),
                         operator=p.get("operator"), surface=p.get("surface") or surface, session=session)
     except GateError as e:
         if e.code != "E_DUPLICATE":
             raise
-        return instance_for(memory, parent, session, surface)  # another session took that id first
+        return instance_for(memory, parent, session, surface, compartments)  # another session took that id first
     return wid
 
 
+def _auto_marker(log: str, template: str) -> Path:
+    return Path(str(log) + ".d") / "auto" / template.replace("/", "_")
+
+
+def mark_auto(log: str, template: str) -> None:
+    """Record that auto mode (the hook) runs this template."""
+    m = _auto_marker(log, template)
+    m.parent.mkdir(parents=True, exist_ok=True)
+    m.touch()
+
+
+def runs_auto(log: str, template: str) -> bool:
+    return _auto_marker(log, template).exists()
+
+
 TOKEN_TTL = 24 * 3600  # seconds
 
 
