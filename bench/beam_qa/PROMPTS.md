# Prompts used for the BEAM 1M answer runs (2026-10-21)

Reader: Claude Haiku (claude-haiku-5-5) as a sub-agent, one agent per
conversation and condition, 13 agents in all. The gold answers were moved
out of the readers' folder before any agent started. Files were built by
`python bench/beam_qa/make.py --data BEAM --out DIR` (BEAM commit b2da22e;
conversations 35, 25, 1, 34, 13, 11, 21, 29, 8, 16; window on 35, 25, 1).

**memory (Gnomon top 10):** "Open memory_<N>.jsonl. Each line is a JSON
object with an id, a question, and "memory": the only snippets you may use
for that question (each starts with [message id] and the speaker). For each
question, answer from its own memory snippets only, in one short sentence
that states the specific facts asked for (names, numbers, dates, versions).
If the snippets do not contain the answer, answer exactly "Not
mentioned"." Told not to open any other file, not to search the web, and
not to write or run code to find answers.

**window (last 128K tokens):** "Read the whole transcript window_<N>.txt
(in consecutive pieces until you reach the end; it is about 128,000
tokens). Each line starts with [message id] and the speaker. Then open
window_<N>_questions.jsonl. Answer each question from the transcript only,
in one short sentence that states the specific facts asked for (names,
numbers, dates, versions). If the transcript does not contain the answer,
answer exactly "Not mentioned"." Same restrictions, and no grep or
scripts.

Score: `python bench/beam_qa/score.py bench/beam_qa gold.json` (gold.json
comes from make.py; BEAM's text is not copied into this repository).
One run per condition.
