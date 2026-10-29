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

## Measures (all exact, by script)

- **Leak:** for each channel, can a reader holding only Y find the canary? (yes or no)
- **Widening:** text that existed only in Y, now readable by an X-only reader (C's risk).
- **Use:** (u1) a single-compartment template records as before; (u2) a two-compartment
  template can run auto mode in some supported way; (u3) the session can still recall Y content;
  (u4) a person can still move X content into Y on purpose by a route someone chose.
- **Size:** net lines changed in `python/gnomon`, tests excluded.

## Decision rule (fixed now)

1. Keep the arms that close all seven channels with no widening.
2. Among those, prefer the fewest use failures, then the fewest lines.
3. If no arm closes all seven, report every arm and the owner decides.

Out of scope, recorded as a limit: content reaching the session by roads Gnomon does not open
(the person pastes it, files on disk, issue #50). No arm here can see those.

## Routing, cost, review

- Plan and this file: Claude Opus. Arms and harness: a Claude Sonnet sub-agent, from a brief.
  Numbers: the harness script. Review of this file and the result: one non-Claude reviewer
  (a prompt file the owner can paste into Gemini) and a separate Claude Haiku agent.
- Paid API cost: $0. No model is called by the harness.
- Author bias: the author (Opus) proposed arm D and leans to it; the channels were listed
  before building, and the reviewers are asked whether a channel that favours D is missing.
