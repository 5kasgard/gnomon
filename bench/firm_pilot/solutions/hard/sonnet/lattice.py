MOD = 1_000_000_007


def lattice_paths(h, w, blocked):
    if h < 0 or w < 0:
        return 0
    pts = set()
    for p in blocked:
        r, c = p[0], p[1]
        if 0 <= r <= h and 0 <= c <= w:
            pts.add((r, c))
    if (0, 0) in pts or (h, w) in pts:
        return 0
    n = h + w + 1
    fact = [1] * (n + 1)
    for i in range(1, n + 1):
        fact[i] = fact[i - 1] * i % MOD
    inv = [1] * (n + 1)
    inv[n] = pow(fact[n], MOD - 2, MOD)
    for i in range(n, 0, -1):
        inv[i - 1] = inv[i] * i % MOD

    def comb(a, b):
        return fact[a] * inv[b] % MOD * inv[a - b] % MOD

    pl = sorted(pts)
    pl.append((h, w))
    m = len(pl)
    dp = [0] * m
    for i in range(m):
        r, c = pl[i]
        v = comb(r + c, r)
        for j in range(i):
            r2, c2 = pl[j]
            if r2 <= r and c2 <= c and dp[j]:
                v -= dp[j] * comb(r - r2 + c - c2, r - r2)
        dp[i] = v % MOD
    return dp[-1]
