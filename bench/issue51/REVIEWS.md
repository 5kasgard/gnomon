# Reviews of the #51 test

## Pre-registration, Claude Haiku (separate agent), 2026-10-29, verbatim

1. **Missed channels.** Three.
- **Write-back.** The session reads X through MCP, then writes that text into Y with a Gnomon write call. The seven channels test reads and auto-recording only. The hook's start context tells the model to call `handoff` before stopping, so a handoff summarising X content into the default compartment is a concrete route. B and C gate only auto-recorded turns, so they miss it.
- **Tool results are invisible to the recorder.** `_text_of` keeps only text blocks, so Gnomon tool results are dropped. With `--with-tools` only `[tool call: name]` survives. The PREREG says B and C detect "drew on" from MCP results, but the hook cannot do that as it stands. They need new code to read `tool_result` blocks and provenance, and that code is not in the size measure.
- **Default compartment.** `--compartment` defaults to the instance's first compartment, which for a two-compartment template may be X. Every session could then be filed into X. Every arm must pass `--compartment Y` explicitly.
- **Channel 7 and D.** D as defined changes only the instance. The template still holds X and Y, so unattached MCP calls still reach X. D closes channel 7 only if the MCP default also changes, and that change is not in the arm.

2. **Unfair measures.**
- u4 is undefined, so D can pass it by assertion.
- u2 ("some supported way") lets D pass with a Y-only instance that never records X. Measure the share of turns recorded verbatim instead.
- The harness calls no model, so channels 4-6 depend on a scripted restatement. Use the same script for every arm. If B and C are fed `tool_result` blocks the real reader ignores, they get credit for a detector that does not exist.
- The tie-break favours D. A fails u2 by design. If D's use measures are lenient, D wins on use and the line count never matters. Count the MCP change D needs.
- Size covers only `python/gnomon`, so the `settings()` output and bench changes are excluded.

3. **Before data.**
- Pin `--compartment Y` in all arms.
- Add write-back and `handoff` as channel 8, tested for every arm.
- Add an MCP-default check for D, or drop D's claim to close channel 7.
- Define u2 and u4 operationally.
- Either implement `tool_result` provenance reading for B and C, or drop them as unimplementable.
- State that exact-match canaries miss paraphrase.

Handling: all six "before data" points applied in amendment 1 of `PREREG.md`.

## D1 and the experiment, Gemini (non-Claude, via the owner's chat), 2026-10-29, verbatim

* Untested Leak Roads under D1:
   * CLI Execution via Bash: Claude Code sessions have a shell tool. A session can execute the gnomon CLI directly using the parent template writer (--writer <parent>) to read compartment X into the session transcript, which the hook then records into Y.
   * MCP Without --per-session: In mcp.py, the runs_auto check sits inside if self.parent is not None and self.writer == self.parent:. If the MCP server is configured without --per-session (self.parent is None), it acts directly as the multi-compartment template writer, bypassing the check entirely.
   * Cross-Session Token Redemption: t_attach is unchanged. If outstanding_tokens exposes tokens from concurrent or prior sessions (up to the 24h TOKEN_TTL) where the same template ran with --compartment X, the current Y session can call attach with an X token, read X, and let the hook record the transcript into Y.
 * Experimental Fairness & Data Validity:
   * Post-hoc Rule Changing (HARKing): The pre-registered decision rule ignored existing test suite failures, and u1–u4 omitted standalone --per-session MCP servers. Adding D1 and u5 after seeing D fail 7 tests rescues the author's preferred arm (D) while accepting u5 = false as a "known cost" rather than a disqualifying use failure.
   * Unfairness to Arm A on Road 8: Arm A only checked the template inside hook.py, leaving the MCP server open to Road 8 (write-back). Applying the same single-compartment restriction in mcp.py would have closed Road 8 for Arm A as well.
   * Misleading u2 on Arm C: Reporting u2 = 6/6 for Arm C is misleading because any multi-compartment session widens Y content into X, violating compartment isolation during normal use.
 * Bugs and Risks in D1's Diff:
   * Sticky Marker File: mark_auto creates a permanent file in <log>.d/auto/ with no cleanup or TTL. Once a template runs auto mode once, standalone --per-session MCP use is permanently broken for that template (u5 = false forever).
   * First-Run Race: If an MCP tool call fires before the first hook invocation creates the marker file, runs_auto returns False and self._start_instance() spawns an unrestricted writer holding both X and Y.
   * Upgrade Failure: Existing active sessions upgrading to D1 already have an instance writer holding [X, Y]. On the next hook call, sorted(w["compartments"]) != sorted(comps) raises GateError("E_COMPARTMENT"), breaking the session.
   * Path Sanitization & Validation: template.replace("/", "_") allows path traversal if template is ".." and causes collisions (a/b vs a_b). Also, mark_auto runs before Memory(a.log) validates that a.writer exists, and [None] in held crashes sorted() if a writer has zero compartments.
 * Verdict: Ship with changes.
   * Drop the filesystem marker (mark_auto/runs_auto). Instead, pass an explicit --require-attach flag to the MCP server in auto-mode's configuration, and handle existing multi-compartment session writers gracefully during upgrades rather than crashing on E_COMPARTMENT.

Handling (each claim checked against the code before acting):

- CLI via Bash, and reading another session's token: both need the session's own file access
  (the token directory and the log are files). That is #50's class, out of scope in PREREG;
  recorded as a limit, not tested here.
- MCP without `--per-session`: correct, a real road D1 leaves open. D2 (below) makes auto
  mode's documented MCP configuration require attach; a server started without it remains a
  misconfiguration, now named in the README. Not closed by code.
- HARKing: fair. D1 and u5 were registered openly after D's data, and D1 was designed to
  rescue D. The first test's verdict stands as reported (only D closed all eight roads; D broke
  seven tests). Follow-up arms are shown as follow-ups, not folded into the first test.
- Arm A on road 8: correct that A plus the same MCP restriction would close road 8. It would
  still record no turns of a two-compartment session (u2 0/6), so it would lose on use; that is
  inferred, not measured.
- u2 on C: agreed; C's widening is reported beside it, and C fails the decision rule on it.
- Sticky marker, first-run race: correct; both removed by D2's flag.
- Upgrade: partly. The hook catches the error and exits 0, so the session does not break, but
  a resumed pre-upgrade session silently stops being recorded. D2 handles it.
- Path traversal: does not reproduce. The gate refuses writer ids such as `..`, `a/b`, `a_b`
  (tested on main, E_BODY). Moot under D2, which writes no marker.
- `[None]` in `held`: the error is caught (exit 0); moot under D2's rewrite.
