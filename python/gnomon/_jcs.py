"""RFC 8785 canonical JSON for the subset Gnomon uses (null, booleans, safe
integers, strings, arrays, objects), in plain Python.

Used by `proofs` to recompute hashes without the Rust core. A faster
rewrite of python/tests/jcs_reference.py (the plain reference the tests
check against the core); the tests check the two agree.

Written separately from the Rust core so the two can check each other:
agreement between independent implementations is evidence; one
implementation agreeing with itself is not.
"""

from __future__ import annotations

# Escapes as one translation table: the work happens in C.
_TABLE = {i: "\\u%04x" % i for i in range(0x20)}
_TABLE.update({ord('"'): '\\"', ord("\\"): "\\\\", 0x08: "\\b", 0x0C: "\\f", 0x0A: "\\n", 0x0D: "\\r",
               0x09: "\\t"})

MAX_SAFE = 2**53 - 1


def _string(s: str) -> str:
    if not s.isascii():
        s.encode("utf-8")  # lone surrogates are not text: raise, as RFC 8785 requires
    return '"' + s.translate(_TABLE) + '"'


def _sorted_keys(d: dict) -> list:
    # RFC 8785 sorts by UTF-16 code units. For ASCII keys that is plain
    # string order; otherwise big-endian UTF-16 bytes compare the same way.
    if all(k.isascii() for k in d):
        return sorted(d)
    return sorted(d, key=lambda k: k.encode("utf-16-be"))


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
        keys = _sorted_keys(v)
        return "{" + ",".join(_string(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise TypeError(type(v))
