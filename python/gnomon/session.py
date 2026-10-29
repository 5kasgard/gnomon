"""Sessions: one writer per running session or agent (spec section 2a).

`instance_for` finds or starts the writer for a session, as an instance of
a template writer that has the `spawn` capability. A session that already
has its instance (resumed, or restarted) keeps it.

`issue_token` and `redeem_token` hand a session's writer from one process
to another on the same machine (from a host's session hook to the MCP
server the same session talks to) without trusting what a model says: the
token is a random secret, single use, tied to its session, expiring after a
day, and kept beside the log in a file only its owner can read.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Optional

from . import GateError, Memory


def instance_for(memory: Memory, parent: str, session: Optional[str] = None,
                 surface: Optional[str] = None, compartments: Optional[list] = None) -> str:
    """The writer for `session`, an instance of `parent`: the existing one
    if this session has one, otherwise a new one (`parent-<n>`). With
    `compartments`, a new instance holds only those (a subset of its
    parent's). An existing instance of the session is reused only if it holds
    exactly those; a session whose instance holds more (one made before
    compartments were pinned) gets a further, narrower instance, which the
    lookup then finds first."""
    writers = memory.writers()
    if parent not in writers:
        raise GateError("E_UNKNOWN_WRITER", f"writer '{parent}' is not registered")
    p = writers[parent]
    if "spawn" not in p["capabilities"]:
        raise GateError("E_CAPABILITY", f"'{parent}' needs the 'spawn' capability to start session instances")
    comps = p["compartments"] if compartments is None else list(compartments)
    if any(c not in p["compartments"] for c in comps):
        raise GateError("E_COMPARTMENT", f"'{parent}' does not hold {', '.join(c for c in comps if c not in p['compartments'])}")
    key = session  # the session id the new instance is registered under
    if session:
        mine = [(wid, w) for wid, w in writers.items() if w.get("instance_of") == parent and w.get("session") == session]
        if compartments is None:
            if mine:
                return mine[0][0]
        else:
            # The core allows one writer per session id, so a session whose
            # existing instance holds more than `comps` gets a further one under
            # a derived id, and the lookup finds it by that id.
            derived = f"{session}~{'+'.join(sorted(comps))}"
            for wid, w in writers.items():
                if w.get("instance_of") == parent and w.get("session") in (session, derived) \
                        and sorted(w["compartments"]) == sorted(comps):
                    return wid
            if mine:
                key = derived
    session = session or uuid.uuid4().hex[:16]
    key = key or session
    for n in range(1, 1_000_000):
        wid = f"{parent}-{n}"
        if wid not in writers:
            break
    if len(wid) > 32:
        raise GateError("E_BODY", f"'{parent}' is too long a writer id to start instances of (ids are at most "
                                  "32 characters, and an instance's is its parent's plus '-<n>')")
    caps = [c for c in p["capabilities"] if c not in ("owner", "govern", "spawn")]
    try:
        memory.register(parent, wid, party=p["party"], kind=p["kind"], capabilities=caps,
                        compartments=comps, name=p.get("name"), model=p.get("model"),
                        operator=p.get("operator"), surface=p.get("surface") or surface, session=key)
    except GateError as e:
        if e.code != "E_DUPLICATE":
            raise
        return instance_for(memory, parent, session, surface, compartments)  # another session took that id first
    return wid


TOKEN_TTL = 24 * 3600  # seconds


def _token_dir(log: str) -> Path:
    return Path(str(log) + ".d") / "attach"


def _read_token(path: Path) -> Optional[dict]:
    try:
        t = json.loads(path.read_text(encoding="utf-8"))
        return t if isinstance(t, dict) and isinstance(t.get("writer"), str) else None
    except (OSError, ValueError):
        return None


def outstanding_tokens(log: str) -> list:
    """Tokens issued and not yet redeemed or expired (to keep them out of
    anything recorded)."""
    d = _token_dir(log)
    if not d.is_dir():
        return []
    return [p.name for p in d.iterdir() if len(p.name) == 32]


def issue_token(log: str, writer: str, session: Optional[str] = None) -> str:
    """A single-use secret naming `writer` (for `session`), for redeem_token.
    A session has at most one outstanding token: asking again returns it.
    Tokens expire after a day; expired ones are swept here."""
    d = _token_dir(log)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for p in list(d.iterdir()):
        t = _read_token(p)
        if t is None or now - float(t.get("issued", 0)) > TOKEN_TTL:
            with contextlib.suppress(OSError):
                p.unlink()
        elif t["writer"] == writer and t.get("session") == session:
            return p.name
    token = secrets.token_hex(16)
    fd = os.open(d / token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"writer": writer, "session": session, "issued": now}))
    return token


def redeem_token(log: str, token: str, session: Optional[str] = None) -> Optional[str]:
    """The writer a token names, once; None for anything else: a malformed,
    unknown, used or expired token, or (when the redeemer knows its own
    session) one issued for another session."""
    if not (isinstance(token, str) and len(token) == 32 and all(c in "0123456789abcdef" for c in token)):
        return None
    path = _token_dir(log) / token
    t = _read_token(path)
    if t is None:
        return None
    if session is not None and t.get("session") not in (None, session):
        return None
    try:
        path.unlink()  # whoever unlinks it, redeems it
    except OSError:
        return None
    if time.time() - float(t.get("issued", 0)) > TOKEN_TTL:
        return None
    return t["writer"]
