from functools import lru_cache


def _seg(p, s):
    # match single segment: ? one char, * any run, no "/" involved
    m, n = len(p), len(s)
    cur = [False] * (n + 1)
    cur[0] = True
    for i in range(m):
        c = p[i]
        nxt = [False] * (n + 1)
        if c == '*':
            nxt[0] = cur[0]
            for j in range(1, n + 1):
                nxt[j] = nxt[j - 1] or cur[j]
        else:
            for j in range(1, n + 1):
                if cur[j - 1] and (c == '?' or c == s[j - 1]):
                    nxt[j] = True
        cur = nxt
    return cur[n]


def glob_match(pattern: str, path: str) -> bool:
    ps = pattern.split('/') if pattern != '' else []
    ss = path.split('/') if path != '' else []

    @lru_cache(maxsize=None)
    def go(i, j):
        if i == len(ps):
            return j == len(ss)
        if ps[i] == '**':
            for k in range(j, len(ss) + 1):
                if go(i + 1, k):
                    return True
            return False
        if j == len(ss):
            return False
        return _seg(ps[i], ss[j]) and go(i + 1, j + 1)

    return go(0, 0)
