"""Glob matching for "/"-separated paths.

Rules:
  "?"  matches exactly one character other than "/".
  "*"  matches any run (possibly empty) of characters other than "/".
  A whole path segment "**" matches zero or more entire segments.
  Everything else matches itself.
"""


def _split(s):
    # The empty string has no segments; otherwise split on "/" keeping empties.
    if s == "":
        return []
    return s.split("/")


def _segment_match(pat, seg):
    """Match one pattern segment against one path segment ("*" and "?" only)."""
    pi = si = 0
    star_pi = -1
    star_si = 0
    np, ns = len(pat), len(seg)
    while si < ns:
        if pi < np and pat[pi] == "*":
            star_pi = pi
            star_si = si
            pi += 1
        elif pi < np and (pat[pi] == "?" or pat[pi] == seg[si]):
            if seg[si] == "/":
                return False
            pi += 1
            si += 1
        elif star_pi != -1:
            pi = star_pi + 1
            star_si += 1
            si = star_si
        else:
            return False
    while pi < np and pat[pi] == "*":
        pi += 1
    return pi == np


def glob_match(pattern: str, path: str) -> bool:
    pats = _split(pattern)
    segs = _split(path)
    n = len(pats)
    m = len(segs)

    # nxt[j]: pats[i+1:] matches segs[j:]; built from the end backwards.
    nxt = [False] * (m + 1)
    nxt[m] = True
    for i in range(n - 1, -1, -1):
        cur = [False] * (m + 1)
        p = pats[i]
        if p == "**":
            # zero segments: cur[j] = nxt[j]; one or more: consume segs[j].
            cur[m] = nxt[m]
            for j in range(m - 1, -1, -1):
                cur[j] = nxt[j] or cur[j + 1]
        else:
            for j in range(m):
                cur[j] = _segment_match(p, segs[j]) and nxt[j + 1]
        nxt = cur
    return nxt[0]
