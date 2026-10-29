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
