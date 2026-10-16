"""Loads the Rust core (a C-ABI shared library) with ctypes.

No third-party packages: ctypes is part of the standard library.
Search order for the library:
  1. the GNOMON_LIB environment variable
  2. a copy next to this file (placed by scripts/build.sh)
  3. the workspace's target/release directory
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path


def _lib_name() -> str:
    if sys.platform.startswith("win"):
        return "gnomon_core.dll"
    if sys.platform == "darwin":
        return "libgnomon_core.dylib"
    return "libgnomon_core.so"


def _find() -> Path:
    env = os.environ.get("GNOMON_LIB")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve().parent
    candidates += [
        here / _lib_name(),
        here.parent.parent / "target" / "release" / _lib_name(),
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise ImportError(
        "gnomon core library not found; run scripts/build.sh or set GNOMON_LIB. "
        f"Looked in: {', '.join(str(c) for c in candidates)}"
    )


_lib = ctypes.CDLL(str(_find()))

_lib.gn_free.argtypes = [ctypes.c_void_p]
_lib.gn_free.restype = None

_SIGNATURES = {
    "gn_sha256_hex": [ctypes.c_char_p, ctypes.c_size_t],
    "gn_canonicalize": [ctypes.c_char_p],
    "gn_claim_id": [ctypes.c_char_p],
    "gn_append": [ctypes.c_char_p, ctypes.c_char_p],
    "gn_verify": [ctypes.c_char_p, ctypes.c_int64],
    "gn_state": [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p],
    "gn_checkpoint": [ctypes.c_char_p, ctypes.c_int64, ctypes.c_char_p],
    "gn_query": [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p],
    "gn_datalog": [ctypes.c_char_p],
    "gn_views": [],
}
for _name, _args in _SIGNATURES.items():
    _fn = getattr(_lib, _name)
    _fn.argtypes = _args
    _fn.restype = ctypes.c_void_p  # freed with gn_free


def call(name: str, *args) -> dict:
    """Call a core function and return its decoded JSON result."""
    encoded = [a.encode("utf-8") if isinstance(a, str) else a for a in args]
    ptr = getattr(_lib, name)(*encoded)
    if not ptr:
        raise RuntimeError(f"{name} returned null")
    try:
        text = ctypes.string_at(ptr).decode("utf-8")
    finally:
        _lib.gn_free(ptr)
    return json.loads(text)
