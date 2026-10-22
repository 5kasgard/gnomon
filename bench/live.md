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
