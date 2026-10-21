"""Error correction for the log: find damage and repair it.

The hash chain proves a log is the one that was written, and says where it
stops being so, but it cannot put back what was lost. This adds two files
beside the log (`<log>.parity`, the parity blocks, and `<log>.parity.json`,
the block hashes): the log is cut into blocks of 4 KiB,
grouped in stripes of 16, and each stripe gets 2 Reed-Solomon parity blocks
over GF(256) (a systematic Cauchy code). A SHA-256 hash of every block says
which blocks are damaged, so damage is repaired as erasures: any 2 blocks
of a stripe (data or parity) can be lost and rebuilt exactly. The cost is
2/16 = 12.5% more storage, and protecting a log that has grown only redoes
its last stripes.

    from gnomon.parity import protect, check, repair
    protect("memory.jsonl")      # write or update memory.jsonl.parity
    check("memory.jsonl")        # {"damaged": [...], "repairable": bool}
    repair("memory.jsonl")       # rebuild damaged blocks in place

No dependencies beyond numpy. A repaired log is then checked by its own
hash chain (`Memory.verify`), so a repair can never pass off wrong bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

import numpy as np

BLOCK = 4096
DATA = 16
PARITY = 2
FORMAT = 2

# ------------------------------------------------------------- GF(256) maths
_EXP = np.zeros(512, dtype=np.uint8)
_LOG = np.zeros(256, dtype=np.int32)
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D  # x^8 + x^4 + x^3 + x^2 + 1
_EXP[255:510] = _EXP[:255]
# MUL[a] is a lookup table: MUL[a][b] = a*b in GF(256).
MUL = np.zeros((256, 256), dtype=np.uint8)
for _a in range(1, 256):
    MUL[_a, 1:] = _EXP[(_LOG[_a] + _LOG[np.arange(1, 256)]) % 255]


def _inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("0 has no inverse in GF(256)")
    return int(_EXP[255 - _LOG[a]])


def _cauchy(rows: int, cols: int) -> np.ndarray:
    """A Cauchy matrix 1/(x_i + y_j) with x_i = cols + i, y_j = j: every
    square submatrix is invertible, so any `rows` erasures are solvable."""
    m = np.zeros((rows, cols), dtype=np.uint8)
    for i in range(rows):
        for j in range(cols):
            m[i, j] = _inv((cols + i) ^ j)
    return m


_C = _cauchy(PARITY, DATA)
_GEN = np.vstack([np.eye(DATA, dtype=np.uint8), _C])  # systematic generator


def _matmul(mat: np.ndarray, blocks: np.ndarray) -> np.ndarray:
    """mat (r x k) times blocks (k x B) over GF(256)."""
    out = np.zeros((mat.shape[0], blocks.shape[1]), dtype=np.uint8)
    for i in range(mat.shape[0]):
        acc = out[i]
        for j in range(mat.shape[1]):
            c = mat[i, j]
            if c:
                acc ^= MUL[c][blocks[j]]
    return out


def _invert(m: np.ndarray) -> np.ndarray:
    """Gauss-Jordan inverse of a square matrix over GF(256)."""
    n = m.shape[0]
    a = np.concatenate([m.astype(np.uint8), np.eye(n, dtype=np.uint8)], axis=1)
    for col in range(n):
        piv = next(r for r in range(col, n) if a[r, col])
        a[[col, piv]] = a[[piv, col]]
        a[col] = MUL[_inv(int(a[col, col]))][a[col]]
        for r in range(n):
            if r != col and a[r, col]:
                a[r] ^= MUL[int(a[r, col])][a[col]]
    return a[:, n:]


# ------------------------------------------------------------------- stripes
def _stripes(data: bytes):
    """Yield (index, k x BLOCK array) for each stripe, zero-padded."""
    per = BLOCK * DATA
    for s in range(0, max(len(data), 1), per):
        chunk = data[s:s + per]
        buf = np.zeros(per, dtype=np.uint8)
        buf[:len(chunk)] = np.frombuffer(chunk, dtype=np.uint8)
        yield s // per, buf.reshape(DATA, BLOCK)


def _h(b) -> str:
    return hashlib.sha256(bytes(b)).hexdigest()


def _side(path) -> Path:
    return Path(str(path) + ".parity")


def protect(path, *, previous: Optional[dict] = None) -> dict:
    """Write (or bring up to date) the parity file for the log at `path`.
    Stripes already protected and unchanged are kept as they are."""
    path = Path(path)
    data = path.read_bytes()
    side = _side(path)
    old = previous
    if old is None and _index(side).exists():
        try:
            old = _read_side(side)
        except ValueError:
            old = None
    old_bin = side.read_bytes() if old and old.get("stripes") and side.exists() else b""
    stripes, parity = [], []
    for idx, blocks in _stripes(data):
        hashes = [_h(b) for b in blocks]
        if old and idx < len(old["stripes"]) and old["stripes"][idx]["data"] == hashes:
            stripes.append(old["stripes"][idx])
            parity.append(old_bin[idx * PARITY * BLOCK:(idx + 1) * PARITY * BLOCK])
            continue
        par = _matmul(_C, blocks)
        stripes.append({"data": hashes, "parity_hash": [_h(p) for p in par]})
        parity.append(par.tobytes())
    body = {"format": FORMAT, "block": BLOCK, "data": DATA, "parity": PARITY, "length": len(data),
            "stripes": stripes}
    _write_side(side, body, b"".join(parity))
    return {"length": len(data), "stripes": len(stripes), "overhead": PARITY / DATA}


def _index(side: Path) -> Path:
    return Path(str(side) + ".json")


def _write_side(side: Path, body: dict, parity: bytes) -> None:
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
    for target, payload in ((side, parity), (_index(side), json.dumps(
            {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "body": raw}).encode())):
        tmp = Path(str(target) + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, target)


def _parity_blocks(side: Path, idx: int) -> list:
    with open(side, "rb") as f:
        f.seek(idx * PARITY * BLOCK)
        raw = f.read(PARITY * BLOCK).ljust(PARITY * BLOCK, b"\0")
    return [raw[i * BLOCK:(i + 1) * BLOCK] for i in range(PARITY)]


def _read_side(side: Path) -> dict:
    outer = json.loads(_index(side).read_text(encoding="utf-8"))
    if hashlib.sha256(outer["body"].encode()).hexdigest() != outer["sha256"]:
        raise ValueError("the parity file itself is damaged; rebuild it from a log that verifies")
    body = json.loads(outer["body"])
    if body.get("format") != FORMAT or body["block"] != BLOCK or body["data"] != DATA or body["parity"] != PARITY:
        raise ValueError("parity file in an unknown format")
    return body


def check(path) -> dict:
    """Which blocks differ from what was protected, and whether every
    damaged stripe can be repaired. Bytes appended since the last `protect`
    are reported as unprotected, not as damage."""
    path = Path(path)
    body = _read_side(_side(path))
    data = path.read_bytes()
    damaged, repairable = [], True
    covered = data[:body["length"]]
    if len(covered) < body["length"]:
        covered = covered + b"\0" * (body["length"] - len(covered))  # truncation: missing bytes are damage
    for idx, blocks in _stripes(covered):
        st = body["stripes"][idx]
        bad = [j for j, b in enumerate(blocks) if _h(b) != st["data"][j]]
        par = _parity_blocks(_side(path), idx)
        bad += [DATA + i for i, p in enumerate(par) if _h(p) != st["parity_hash"][i]]
        if bad:
            damaged.append({"stripe": idx, "blocks": bad})
            repairable &= len(bad) <= PARITY
    return {"damaged": damaged, "repairable": repairable, "protected_bytes": body["length"],
            "unprotected_bytes": max(0, len(data) - body["length"])}


def repair(path) -> dict:
    """Rebuild damaged blocks in place (the original is kept as
    `<log>.damaged`). Raises if a stripe lost more than it can rebuild."""
    path = Path(path)
    body = _read_side(_side(path))
    rep = check(path)
    if not rep["damaged"]:
        return {"repaired": 0}
    if not rep["repairable"]:
        raise ValueError(f"too much damage to repair: {rep['damaged']}")
    data = bytearray(path.read_bytes())
    tail = bytes(data[body["length"]:])
    data = data[:body["length"]] + bytearray(max(0, body["length"] - len(data)))
    per = BLOCK * DATA
    fixed = 0
    for d in rep["damaged"]:
        idx, bad = d["stripe"], set(d["blocks"])
        st = body["stripes"][idx]
        buf = np.zeros(per, dtype=np.uint8)
        seg = data[idx * per:(idx + 1) * per]
        buf[:len(seg)] = np.frombuffer(bytes(seg), dtype=np.uint8)
        rows = list(buf.reshape(DATA, BLOCK)) + [np.frombuffer(p, dtype=np.uint8) for p in _parity_blocks(_side(path), idx)]
        good = [i for i in range(DATA + PARITY) if i not in bad][:DATA]
        dec = _invert(_GEN[good])
        blocks = _matmul(dec, np.stack([rows[i] for i in good]))
        for j in sorted(b for b in bad if b < DATA):
            if _h(blocks[j]) != st["data"][j]:
                raise ValueError(f"stripe {idx} block {j}: rebuilt bytes do not match their hash")
        flat = blocks.reshape(-1).tobytes()
        end = min(len(data), (idx + 1) * per)
        data[idx * per:end] = flat[:end - idx * per]
        fixed += len(bad)
    os.replace(path, Path(str(path) + ".damaged"))
    path.write_bytes(bytes(data) + tail)
    if any(b >= DATA for d in rep["damaged"] for b in d["blocks"]):
        protect(path, previous={"stripes": []})  # damaged parity blocks are simply recomputed
    return {"repaired": fixed, "stripes": [d["stripe"] for d in rep["damaged"]]}
