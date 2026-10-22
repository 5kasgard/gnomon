# Live: a simulated person, an assistant with Gnomon, many sessions

Offline tests score what retrieval puts in view. This one runs the loop:
whether a model calls Gnomon's tools, what it expands, what each reply
costs, and whether it applies what comes back.

- **A, the person,** plays a private card. The card holds facts revealed
  in early sessions: standing circumstances such as a diet, a budget
  freeze or a move. A is told only to work them in naturally, and talks
  about everyday topics otherwise. Two personas, 8 weekly sessions,
  6 facts each (`live_cards.json`).
- **B, the assistant,** starts every session with no memory except
  Gnomon's tools (`situation`, `context`, `expand`), called with a
  text protocol any model can follow. Every session is recorded into
  Gnomon at its end.
- **Probes.** From session 6 on, sessions open with a scripted request
  where a planted fact should change a good answer ("suggest dinner
  ideas" for a vegetarian). B answers it twice: with its memory, and
  without (the control), on the same message.
- **Measured,** judge-free:
  - whether each answer applies the fact, by the fact's check words;
  - with against without, paired per probe;
  - every tool call, expand, model call and token.
- **Limits.**
  - It is model-written conversation, so it tests the loop, not realism.
  - Check words are a coarse test.
  - 12 probes is a pilot, not a statistic.
- **Providers:** Workers AI or the Anthropic API, chosen at run time
  and recorded with the results.
  - Workers AI runs on the free daily allocation, through a small proxy
    Worker with an AI binding. It sits behind a key and a dedicated
    Cloudflare Access service token, so no API token is needed.
  - The model is Gemma 4 26B, with thinking turned off.
  - The proxy, its Access app and the token are deleted after the run.

## Pilot result (Workers AI, Gemma 4 26B, thinking off)

The run took 120 model calls for 16 sessions, about 69K tokens and 894
neurons. The three-condition probes took 36 more calls and 460 neurons.
In all, about 14% of one day's free allocation. Results are in
`live_results/`.

**1. Pull fails: the model never used its memory.** Across 16 sessions
and 12 probes, B made 0 tool calls. That was every chance it had, even
with the instruction to use memory whenever earlier conversations could
matter. This model, under a text protocol, does not reach for memory on
its own. That is one model and one protocol, but it is a warning: a
memory that waits to be asked may never be asked.

**2. The simulated person did not always plant its facts.** Priya never
mentioned 3 of her 6 facts (the nut allergy, the sold car, Tuesdays).
Those probes test nothing and are set aside below. The harness must
check that each fact was actually said, and prompt again if not.

**3. Push helps on the probes that were planted** (9; judge-free check
words). The pull condition is left out: it made no tool calls, so it
answered without memory and only measures noise.

| | Applied the fact |
|---|---|
| No memory | 3 / 9 |
| Memory pushed into the prompt (situation + context, about 1,100–2,900 tokens) | **6 / 9** |
| Push better / worse / same, paired | 5 / 2 / 2 |

The two "worse" cases look like check-word noise. A generic answer
without memory happened to mention running, or Scotland. With 9 probes
this is a pilot, not a result.

**What changes in the design:** the standing parts of memory should be
pushed, not left to be pulled. The `situation` model and the standing
instructions change only when the person says something new, so they
can sit in a cached prefix of every conversation. Gnomon's Claude Code
hook (`gnomon.hook`) is the place for that. Pull stays for the rest.

**Next run:**
- verify that each fact is planted;
- use native function calling where the model has it;
- run Claude through the Anthropic API, since whether Claude pulls is
  the question that matters for Claude users;
- use a blinded judge as a second measure alongside the check words.

## Run 2, fixed before running: making memory unmistakable

The pilot's model never pulled memory under a text protocol. The owner's
point: a memory has to bind, or be so apparent that it is the obvious
move. Each session starts fresh and short, so nothing in the model's
context says there is a history to check. A one-call check with native
tools and `tool_choice: auto` already shows the reflex: "Since I don't
know your preferences…", and no call.

- **History:** the same cards and sessions. Planting is now verified:
  after A's turns, if a fact's key word is missing from what A said, A
  gets up to two extra turns with an explicit reminder. B in the
  sessions uses native tools with the salience note (below).
- **Probe conditions,** each on the same message, with memory as
  recorded up to that session:
  - **none:** no memory.
  - **native:** the model's own function calling, `tool_choice: auto`.
  - **salient:** native, plus one line in the system prompt saying how
    many earlier conversations there were, when the last one was, and
    how many of the person's statements memory holds.
  - **forced:** native, with `tool_choice: required` on the first step,
    so the model must consult memory before answering.
  - **push:** situation and context placed into the system prompt by
    the harness.
- **Measured:**
  - how often each condition calls a tool;
  - whether the answer applies the planted fact (check words), on
    planted facts only;
  - tokens of memory read.
- **Same model:** Gemma 4 26B on Workers AI, thinking off.

## Run 2 result

The run took 176 model calls, about 125K tokens and 1,464 neurons
(`live_results/run2_*`). Every fact was planted this time: the reminder
fired for Priya. Applied means the planted fact shows up in the answer
(check words, 12 probes).

| Condition | Called memory | Applied the fact | Memory read |
|---|---|---|---|
| None | 0 / 12 | 5 / 12 | — |
| Native tools, `auto` | 1 / 12 | 5 / 12 | 1.4K tokens |
| Native + salience note | 1 / 12 | 4 / 12 | 1.1K |
| Forced first call | 11 / 12 | 6 / 12 | 10.8K |
| **Push** | (none needed) | **10 / 12** | about 2–3K per probe, in the prompt |

What it shows:

- **Offering tools, even native ones, does not get them used.** One call
  in 12, with or without a line saying there is a history. The text cue
  changed nothing. That is the owner's point in numbers: words ask,
  structure binds.
- **Forcing a call gets a call, not the right one.**
  - Where the model chose `situation`, which holds the facts, it
    applied them about half the time.
  - Three times it called `expand` with no pointer.
  - Five times it called `context` with a query aimed off target. For
    the walking route it asked for "location, preferred walking routes",
    which misses the knee. For half-term it asked about the kids'
    interests, which misses Glasgow.

  The unknown unknown moves from "should I look" to "what should I look
  for". The model cannot ask for what it does not know is there.
- **Push wins: 10 of 12, against 5 with no memory.** The harness hands
  over the person's situation and what bears on the message, and the
  model applies it. The two misses were the 10am meetings and Glasgow,
  where the answers did not use the words checked.

The check words are generous to "none": generic answers often mention
running, budgets or weekdays. So the gap between push and the rest is
likely larger than shown.

**Design consequence.** For the person's standing situation, memory
must be pushed by structure: a hook or harness that puts it in a
cached prefix. Tools stay for depth: expanding a pointer, or a search
the model knows it needs. The next condition to test is the middle
path: push a short table of contents, and leave the details to pull.

## Run 3, fixed before running: what should the hook push?

Push won run 2. A hook can push two things: what never changes between
messages (the situation, cacheable as a prefix), or what depends on the
message (`context` for it). The cheapest option would be a table of
contents. The probes re-use run 2's recorded histories, each limited to
what was recorded before its session. Each condition runs twice, to
see run-to-run noise:

- **none:** no memory (the noise floor);
- **push, full:** situation + context for the message (run 2's push,
  replicated);
- **push, situation only:** the cacheable prefix alone;
- **table of contents + tools:** a line listing the distinctive words
  of the person's situation sentences, plus native tools (`auto`). Up
  to 30 words: each sentence's two rarest content words, in the order
  they were said.

Measured: applied the fact (check words), tool calls, tokens pushed.
