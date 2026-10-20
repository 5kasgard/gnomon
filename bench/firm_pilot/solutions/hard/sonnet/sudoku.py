def _build():
    units = []
    for r in range(9):
        units.append([r * 9 + c for c in range(9)])
    for c in range(9):
        units.append([r * 9 + c for r in range(9)])
    for br in range(3):
        for bc in range(3):
            units.append([(br * 3 + i) * 9 + bc * 3 + j for i in range(3) for j in range(3)])
    cell_units = [[] for _ in range(81)]
    for u in units:
        for c in u:
            cell_units[c].append(u)
    peers = []
    for c in range(81):
        s = set()
        for u in cell_units[c]:
            s.update(u)
        s.discard(c)
        peers.append(tuple(s))
    return units, cell_units, peers


_UNITS, _CU, _PEERS = _build()
_ALL = 0x1FF
_BITS = [1 << i for i in range(9)]


def _assign(cand, c, b):
    """Set cell c to bit b, propagate. Return False on contradiction."""
    stack = [(c, b)]
    while stack:
        c, b = stack.pop()
        cur = cand[c]
        if not cur & b:
            return False
        if cur == b:
            # already fixed; still ensure peers cleared (done at first fix)
            if getattr(_assign, "_x", None) is None:
                pass
        cand[c] = b
        for p in _PEERS[c]:
            pc = cand[p]
            if pc & b:
                n = pc & ~b
                if not n:
                    return False
                cand[p] = n
                if n & (n - 1) == 0:
                    stack.append((p, n))
        # hidden singles in the units of c
        for u in _CU[c]:
            for d in _BITS:
                pass
    return True


def _propagate(cand):
    # Full constraint propagation: naked + hidden singles to fixpoint.
    changed = True
    while changed:
        changed = False
        for u in _UNITS:
            seen = 0
            twice = 0
            for c in u:
                v = cand[c]
                twice |= seen & v
                seen |= v
            if seen != _ALL:
                return False
            once = seen & ~twice
            if once:
                for c in u:
                    v = cand[c] & once
                    if v:
                        if v & (v - 1):
                            return False
                        if cand[c] != v:
                            cand[c] = v
                            changed = True
        # naked singles elimination
        for c in range(81):
            v = cand[c]
            if v & (v - 1) == 0:
                if not v:
                    return False
                for p in _PEERS[c]:
                    if cand[p] & v:
                        n = cand[p] & ~v
                        if not n:
                            return False
                        cand[p] = n
                        changed = True
    return True


def _search(cand):
    if not _propagate(cand):
        return None
    best = -1
    bn = 10
    for c in range(81):
        v = cand[c]
        if v & (v - 1):
            n = bin(v).count("1")
            if n < bn:
                bn = n
                best = c
                if n == 2:
                    break
    if best < 0:
        return cand
    v = cand[best]
    while v:
        b = v & -v
        v ^= b
        nxt = cand[:]
        nxt[best] = b
        r = _search(nxt)
        if r is not None:
            return r
    return None


def sudoku(grid):
    if not isinstance(grid, str) or len(grid) != 81:
        return None
    cand = [_ALL] * 81
    for i, ch in enumerate(grid):
        if ch in "123456789":
            cand[i] = 1 << (int(ch) - 1)
        elif ch != "." and ch != "0":
            return None
    # check given conflicts
    for u in _UNITS:
        seen = 0
        for c in u:
            v = cand[c]
            if v != _ALL and v & (v - 1) == 0:
                if seen & v:
                    return None
                seen |= v
    res = _search(cand)
    if res is None:
        return None
    return "".join(str(r.bit_length()) for r in res)
