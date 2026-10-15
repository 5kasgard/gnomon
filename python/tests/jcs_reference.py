"""An independent RFC 8785 implementation for the subset Gnomon uses
(null, booleans, safe integers, strings, arrays, objects).

Written separately from the Rust core so the two can check each other:
agreement between independent implementations is evidence; one
implementation agreeing with itself is not.
"""

from __future__ import annotations

_ESC = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

MAX_SAFE = 2**53 - 1


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _utf16_units(s: str) -> list[int]:
    data = s.encode("utf-16-be")
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def canonical(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        if abs(v) > MAX_SAFE:
            raise ValueError("integer outside safe range")
        return str(v)
    if isinstance(v, float):
        raise ValueError("floats are not allowed")
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        return "[" + ",".join(canonical(x) for x in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v.keys(), key=_utf16_units)
        return "{" + ",".join(_string(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise TypeError(type(v))
