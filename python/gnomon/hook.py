"""Auto mode for Claude Code: record every conversation, bind every session.

Install it as Claude Code hooks (python -m gnomon hooks prints the
settings). With it:

* At the start of every session (SessionStart), the session gets its own
  writer, an instance of the template writer, keyed by the host's session
  id, so a resumed session keeps its writer. The model is told who it is
  on record and given a single-use token to `attach` the Gnomon MCP tools to
  that same writer. It also gets the resume packet of the work it is most
  likely continuing, and what the person has said about their own
  situation (constraints, plans, preferences), in their words: placed in
  front of the model rather than offered as a tool, because a model that
  must choose to look rarely does (bench/live.md: tools offered were
  used on 1 of 12 probes; pushed, the fact was applied 17-19 of 24 times
  against 10 without memory).
* Optionally (--per-message), before each prompt (UserPromptSubmit), what
  the record holds that bears on that prompt.
* After every reply (Stop), before the context is compacted (PreCompact)
  and when the session ends (SessionEnd), the new turns of the conversation
  are recorded from the host's transcript as an episode. The model's turns
  are bound to the session's writer and the user's to its operator. This
  happens whether or not the model remembers to do it.

The transcript is the host's own record of what was said, so nothing is
paraphrased. The text of each turn is kept whole. Tool calls and their
output are left out, unless --with-tools is given.

A hook must never break the session it serves: any failure is reported on
stderr and the hook exits 0.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

from . import GateError, Memory, title
from .ingest import record_messages
from .session import instance_for, issue_token, outstanding_tokens

MAX_EPISODE = 90_000  # characters per recorded episode (the core allows 100,000)


def _state_path(log: str, session: str) -> Path:
    # A hash: distinct session ids never share a watermark.
    return Path(str(log) + ".d") / "hooks" / (hashlib.sha256(session.encode("utf-8", "replace")).hexdigest() + ".json")


def _load_mark(log: str, session: str) -> dict:
    try:
        m = json.loads(_state_path(log, session).read_text(encoding="utf-8"))
        return {"offset": int(m["offset"]), "file": str(m.get("file", ""))}
    except (OSError, ValueError, KeyError, TypeError):
        return {"offset": 0, "file": ""}


def _save_mark(log: str, session: str, offset: int, file_id: str) -> None:
    p = _state_path(log, session)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"offset": offset, "file": file_id}), encoding="utf-8")
    os.replace(tmp, p)


def compaction_mark(log: str, session: str) -> Path:
    """A file whose time says when this session's context was last
    compacted: the MCP server then stops referring to what it showed
    before, which the model may no longer have."""
    return _state_path(log, session).with_suffix(".compacted")


def _mark_compaction(log: str, session: str) -> None:
    p = compaction_mark(log, session)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    os.utime(p)


def _pushed(log: str, session: str) -> set:
    """Spans already pushed into this session's context since it was last
    compacted: pushing them again adds tokens and nothing else (on real
    histories, about half of what would be pushed; bench/session_economy.md)."""
    p = _state_path(log, session).with_suffix(".pushed")
    try:
        held = json.loads(p.read_text(encoding="utf-8"))
        c = compaction_mark(log, session)
        if c.exists() and c.stat().st_mtime >= held["since"]:
            return set()
        return set(held["spans"])
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _save_pushed(log: str, session: str, spans: set) -> None:
    import time
    p = _state_path(log, session).with_suffix(".pushed")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        since = json.loads(p.read_text(encoding="utf-8"))["since"]
        c = compaction_mark(log, session)
        if c.exists() and c.stat().st_mtime >= since:
            since = time.time()
    except (OSError, ValueError, KeyError, TypeError):
        since = time.time()
    tmp = p.with_suffix(".pushed.tmp")
    tmp.write_text(json.dumps({"since": since, "spans": sorted(spans)}), encoding="utf-8")
    os.replace(tmp, p)


@contextlib.contextmanager
def _locked(log: str, session: str):
    """One recorder per session at a time: overlapping hooks wait."""
    p = _state_path(log, session).with_suffix(".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+b") as f:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except ImportError:  # Windows
            import msvcrt
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
        yield


def _text_of(content: Any, with_tools: bool) -> str:
    """The words of one transcript message: text blocks (and, with tools,
    a one-line note of each tool call)."""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif with_tools and kind == "tool_use":
                parts.append(f"[tool call: {block.get('name', '?')}]")
    return "\n".join(p for p in parts if p.strip())


# Entries the host writes itself: not words anyone said.
_HOST_FLAGS = ("isSidechain", "isMeta", "isCompactSummary", "isVisibleInTranscriptOnly", "isApiErrorMessage")
_HOST_PREFIXES = ("<command-name>", "<command-message>", "<command-args>", "<local-command-stdout>",
                  "<local-command-stderr>", "[Request interrupted", "Caveat: The messages below were generated")


def _said(entry: dict, with_tools: bool, model_label: str) -> Optional[tuple]:
    if any(entry.get(k) for k in _HOST_FLAGS):
        return None
    kind = entry.get("type")
    if kind not in ("user", "assistant"):
        return None
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    if kind == "assistant" and msg.get("model") == "<synthetic>":
        return None
    text = _text_of(msg.get("content"), with_tools)
    # Text that cannot be stored as written (a broken surrogate pair) is
    # kept with a replacement character rather than lost.
    text = "".join("\ufffd" if 0xD800 <= ord(c) <= 0xDFFF else c for c in text).strip()
    if not text or text.startswith(_HOST_PREFIXES):
        return None
    return ("User" if kind == "user" else model_label, text)


def read_turns(transcript: str, start: int = 0, with_tools: bool = False,
               model_label: str = "Claude") -> tuple[list, int]:
    """New turns of a Claude Code transcript from byte `start`: a list of
    (speaker, text, end offset), and the offset read up to. Speakers are
    "User" and `model_label`. Side conversations, host notes, compaction
    summaries, slash commands and API errors are left out. A last line
    still being written is left for next time."""
    turns: list = []
    try:
        with open(transcript, "rb") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return [], start
    pos = start
    for raw in data.split(b"\n")[:-1]:  # the piece after the last newline is unfinished
        pos += len(raw) + 1
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(entry, dict):
            t = _said(entry, with_tools, model_label)
            if t:
                turns.append((t[0], t[1], pos))
    return turns, pos


def _split(text: str, size: int) -> list:
    """Pieces of at most about `size` characters that join back to `text`
    exactly (they are recorded untrimmed); cuts fall inside words where
    possible, so each piece reads naturally."""
    pieces, start = [], 0
    while len(text) - start > size:
        cut = start + size
        back = cut
        while back > start + size // 2 and (text[back - 1].isspace() or text[back].isspace()):
            back -= 1
        if back > start + size // 2:
            cut = back
        pieces.append(text[start:cut])
        start = cut
    pieces.append(text[start:])
    return pieces


def _chunks(turns: list, start: int = 0) -> list:
    """Turns grouped into episodes the core accepts, each with the
    transcript offset it ends at; a very long turn becomes consecutive
    turns by the same speaker, never shortened."""
    out, cur, size, end = [], [], 0, start
    for speaker, text, stop in turns:
        for p in _split(text, MAX_EPISODE // 2):
            cost = len(p) + len(speaker) + 3
            if cur and size + cost > MAX_EPISODE:
                out.append((cur, end))
                cur, size = [], 0
            cur.append((speaker, p))
            size += cost
        end = stop
    if cur:
        out.append((cur, end))
    return out


def _redact(text: str, secrets_: list) -> str:
    for tok in secrets_:
        text = text.replace(tok, "[attach token]")
    return text


def record(memory: Memory, log: str, writer: str, session: str, transcript: str, compartment: str,
           with_tools: bool = False) -> list:
    """Record the turns added since the last call; returns episode ids.
    The watermark advances after every recorded episode, so a failure
    never duplicates turns; a part the gate refuses is replaced by a
    note saying so, so it never stops recording for good."""
    with _locked(log, session):
        mark = _load_mark(log, session)
        try:
            st_ = os.stat(transcript)
            file_id = f"{st_.st_dev}:{st_.st_ino}"
        except OSError:
            return []
        start = mark["offset"]
        if mark["file"] != file_id or st_.st_size < start:
            start = 0  # a new or rewritten transcript: read it from the beginning
        w = memory.writers().get(writer, {})
        # The model's turns go under its registered name (or its id): only
        # those can be bound to it, and never a name that reads as "User".
        label = w.get("name") or writer
        if label.casefold() == "user":
            label = writer
        operator = w.get("operator")
        turns, end = read_turns(transcript, start, with_tools, label)
        outstanding = outstanding_tokens(log)
        episodes = []
        for chunk, stop in _chunks(turns, start):
            chunk = [(sp, _redact(tx, outstanding)) for sp, tx in chunk]
            names = {sp for sp, _ in chunk}
            bindings = {k: v for k, v in ((label, writer), ("User", operator)) if v and k in names}
            try:
                episodes.append(record_messages(memory, writer, chunk, compartment=compartment, medium="claude-code",
                                                bindings=bindings, exact=True))
            except GateError as e:
                print(f"gnomon hook: part of the conversation was refused: {e.code}: {e.message}", file=sys.stderr)
                try:
                    episodes.append(record_messages(
                        memory, writer, [(label, f"[{len(chunk)} turn(s) could not be recorded: {e.code}]")],
                        compartment=compartment, medium="claude-code", bindings={label: writer}))
                except GateError:
                    pass
            _save_mark(log, session, stop, file_id)
        _save_mark(log, session, end, file_id)
    return episodes


def situation_context(memory: Memory, writer: str, budget_tokens: int = 1500) -> str:
    """The person's own sentences about their situation, most recent within
    the budget; '' when there are none. Their turns are recorded as "User"."""
    from .situation import Situation
    if budget_tokens <= 0:
        return ""
    r = Situation(memory, writer, own=lambda speaker: speaker == "User").render(budget=budget_tokens)
    return r["text"] if r["lines"] else ""


def start_context(memory: Memory, log: str, writer: str, frame: Optional[str] = None,
                  budget_tokens: int = 2000, situation_tokens: int = 1500,
                  session: Optional[str] = None) -> str:
    """What the model is told at the start of a session."""
    from .mcp import render_resume
    writers = memory.writers()
    session = session or memory.writers().get(writer, {}).get("session")  # the host's id, not a derived one
    token = issue_token(log, writer, session)
    lines = [
        "Gnomon (verifiable memory) is attached to this session.",
        f"You are on record as {title(writers, writer)}. This conversation is recorded automatically.",
        f"Before using any other Gnomon tool, call the Gnomon `attach` tool with token {token}, so that your "
        "tool calls are made as this session's writer.",
        "Before you stop, or when the work reaches a milestone, call `handoff` so the next session or model "
        "can continue exactly where you left off.",
        "",
    ]
    sit = situation_context(memory, writer, situation_tokens)
    if sit:
        lines += [sit, ""]
    try:
        lines.append(render_resume(memory.resume(writer, frame), budget_tokens))
    except GateError as e:
        lines.append(f"(Could not resume: {e.code})")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m gnomon.hook", description="Claude Code hook: auto mode.")
    p.add_argument("--log", required=True)
    p.add_argument("--writer", required=True, help="the template writer (needs 'spawn'); each session is an instance")
    p.add_argument("--compartment", help="where conversations are recorded (default: the writer's first)")
    p.add_argument("--frame", help="the piece of work to resume at session start (default: the most recent)")
    p.add_argument("--with-tools", action="store_true", help="note tool calls in the recorded conversation")
    p.add_argument("--budget", type=int, default=2000,
                   help="about how many tokens of context to give a new session (default 2000)")
    p.add_argument("--situation-budget", type=int, default=1500,
                   help="tokens of the person's own situation to give a new session (default 1500; 0: none)")
    p.add_argument("--per-message", type=int, default=0, metavar="TOKENS",
                   help="before each prompt, add up to this many tokens of what the record holds on it (default 0: off)")
    a = p.parse_args(argv)
    try:
        event = json.loads(sys.stdin.read() or "{}")
        name = event.get("hook_event_name", "")
        session = str(event.get("session_id") or "")
        if not session:
            return 0
        memory = Memory(a.log)
        held = memory.writers().get(a.writer, {}).get("compartments") or [None]
        compartment = a.compartment or held[0]
        # The session's writer holds only the compartment it is recorded in,
        # so nothing it reads through Gnomon can come from another.
        known = memory.writers()
        writer = instance_for(memory, a.writer, session, surface="claude-code", compartments=[compartment])
        if writer not in known and any(w.get("instance_of") == a.writer and w.get("session") == session
                                       for w in known.values()):
            print(f"gnomon hook: this session's earlier writer held more than '{compartment}'; "
                  f"recording as {writer}, which holds only '{compartment}'", file=sys.stderr)
        transcript = event.get("transcript_path")
        if name == "PreCompact":
            _mark_compaction(a.log, session)
        if name in ("Stop", "SubagentStop", "PreCompact", "SessionEnd") and transcript:
            record(memory, a.log, writer, session, transcript, compartment, a.with_tools)
        elif name == "SessionStart":
            if transcript:  # a resumed session: catch up on anything unrecorded
                record(memory, a.log, writer, session, transcript, compartment, a.with_tools)
            ctx = start_context(memory, a.log, writer, a.frame, max(200, a.budget), a.situation_budget, session)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
        elif name == "UserPromptSubmit" and a.per_message > 0 and (event.get("prompt") or "").strip():
            from .consolidate import Tiers, span_key
            held = _pushed(a.log, session)
            c = Tiers(memory, writer).context(event["prompt"], budget=a.per_message, directive_budget=0,
                                              exclude=held)
            if c["view"]:
                _save_pushed(a.log, session, held | {span_key(v) for v in c["view"]})
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                         "additionalContext": c["text"]}}))
    except Exception as e:  # a hook must never break the session it serves
        print(f"gnomon hook: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def settings(log: str, writer: str, python: str = "python3", pythonpath: Optional[str] = None,
             per_message: int = 0) -> dict:
    """The Claude Code settings that turn auto mode on."""
    env = f"PYTHONPATH={shlex.quote(pythonpath)} " if pythonpath else ""
    cmd = f"{env}{shlex.quote(python)} -m gnomon.hook --log {shlex.quote(str(log))} --writer {shlex.quote(writer)}"
    if per_message:
        cmd += f" --per-message {int(per_message)}"
    hook = [{"hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]
    events = ("SessionStart", "Stop", "PreCompact", "SessionEnd") + (("UserPromptSubmit",) if per_message else ())
    return {"hooks": {k: hook for k in events}}


if __name__ == "__main__":
    sys.exit(main())
