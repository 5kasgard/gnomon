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
