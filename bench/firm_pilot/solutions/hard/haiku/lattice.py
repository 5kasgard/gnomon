MOD = 1_000_000_007


def lattice_paths(h: int, w: int, blocked: list[tuple[int, int]]) -> int:
    """Count monotone lattice paths from (0, 0) to (h, w) avoiding blocked points.

    Steps are (row+1, col) or (row, col+1). Result is modulo 1_000_000_007.
    Uses inclusion-exclusion over the blocked points (sorted by row, then col),
    so the cost is O(k^2 + h + w) for k blocked points.
    """
    h = int(h)
    w = int(w)
    if h < 0 or w < 0:
        return 0

    pts = set()
    for r, c in blocked:
        r = int(r)
        c = int(c)
        if 0 <= r <= h and 0 <= c <= w:
            pts.add((r, c))

    if (0, 0) in pts or (h, w) in pts:
        return 0

    n = h + w
    fact = [1] * (n + 1)
    for i in range(1, n + 1):
        fact[i] = fact[i - 1] * i % MOD
    inv = [1] * (n + 1)
    inv[n] = pow(fact[n], MOD - 2, MOD)
    for i in range(n, 0, -1):
        inv[i - 1] = inv[i] * i % MOD

    def paths(dr: int, dc: int) -> int:
        if dr < 0 or dc < 0:
            return 0
        return fact[dr + dc] * inv[dr] % MOD * inv[dc] % MOD

    order = sorted(pts)
    f = []
    for i, (r, c) in enumerate(order):
        v = paths(r, c)
        for j in range(i):
            rj, cj = order[j]
            if cj <= c:
                v -= f[j] * paths(r - rj, c - cj)
        f.append(v % MOD)

    total = paths(h, w)
    for i, (r, c) in enumerate(order):
        total -= f[i] * paths(h - r, w - c)
    return total % MOD
