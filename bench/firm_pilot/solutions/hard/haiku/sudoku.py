"""Sudoku solver: bitmask candidates, constraint propagation, MRV backtracking."""


def _build_units():
    units = []
    for r in range(9):
        units.append([r * 9 + c for c in range(9)])
    for c in range(9):
        units.append([r * 9 + c for r in range(9)])
    for br in range(3):
        for bc in range(3):
            units.append([(br * 3 + r) * 9 + bc * 3 + c for r in range(3) for c in range(3)])
    unit_of = [[] for _ in range(81)]
    for u in units:
        for i in u:
            unit_of[i].append(u)
    peers = [set() for _ in range(81)]
    for u in units:
        for i in u:
            for j in u:
                if i != j:
                    peers[i].add(j)
    return units, unit_of, [sorted(p) for p in peers]


_UNITS, _UNIT_OF, _PEERS = _build_units()
_ALL = 0b1111111110  # bits 1..9 set


def _eliminate(cand, i, d):
    """Remove digit d from cell i and propagate. Returns False on contradiction."""
    bit = 1 << d
    if not cand[i] & bit:
        return True
    cand[i] &= ~bit
    rem = cand[i]
    if rem == 0:
        return False
    if rem & (rem - 1) == 0:  # naked single: propagate to peers
        d2 = rem.bit_length() - 1
        for p in _PEERS[i]:
            if not _eliminate(cand, p, d2):
                return False
    for u in _UNIT_OF[i]:  # hidden single check per unit
        places = [j for j in u if cand[j] & bit]
        if not places:
            return False
        if len(places) == 1:
            j = places[0]
            if cand[j] != bit and not _assign(cand, j, d):
                return False
    return True


def _assign(cand, i, d):
    """Fix cell i to digit d by eliminating all other candidates."""
    others = cand[i] & ~(1 << d)
    while others:
        b = others & -others
        if not _eliminate(cand, i, b.bit_length() - 1):
            return False
        others ^= b
    return True


def _search(cand):
    best = -1
    bestn = 10
    for i in range(81):
        c = cand[i]
        if c & (c - 1):
            n = bin(c).count("1")
            if n < bestn:
                bestn = n
                best = i
                if n == 2:
                    break
    if best < 0:
        return cand  # every cell fixed
    c = cand[best]
    while c:
        b = c & -c
        c ^= b
        nc = cand[:]
        if _assign(nc, best, b.bit_length() - 1):
            r = _search(nc)
            if r is not None:
                return r
    return None


def sudoku(grid):
    if not isinstance(grid, str) or len(grid) != 81:
        return None
    cand = [_ALL] * 81
    for i, ch in enumerate(grid):
        if ch == "." or ch == "0":
            continue
        if ch not in "123456789":
            return None
        if not _assign(cand, i, int(ch)):
            return None
    res = _search(cand)
    if res is None:
        return None
    return "".join(str(c.bit_length() - 1) for c in res)
