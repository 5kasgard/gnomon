from array import array


def edit_script(a: str, b: str) -> list[tuple]:
    """Return a shortest edit script turning string a into string b.

    Operations are read left to right through a:
      ("keep", c)       keep the next character c of a
      ("sub", c, d)     replace the next character c of a with d (c != d)
      ("del", c)        delete the next character c of a
      ("ins", d)        insert d
    """
    n = len(a)
    m = len(b)

    # rows[i][j] = Levenshtein distance between a[:i] and b[:j]
    prev = array('i', range(m + 1))
    rows = [prev]
    for i in range(1, n + 1):
        ai = a[i - 1]
        cur = array('i', [0]) * (m + 1)
        cur[0] = i
        for j in range(1, m + 1):
            best = prev[j - 1] if ai == b[j - 1] else prev[j - 1] + 1
            d = prev[j] + 1
            if d < best:
                best = d
            d = cur[j - 1] + 1
            if d < best:
                best = d
            cur[j] = best
        rows.append(cur)
        prev = cur

    # Trace back from (n, m) to (0, 0), collecting operations in reverse.
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        cur_d = rows[i][j]
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and rows[i - 1][j - 1] == cur_d:
            ops.append(("keep", a[i - 1]))
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and rows[i - 1][j - 1] + 1 == cur_d:
            ops.append(("sub", a[i - 1], b[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and rows[i - 1][j] + 1 == cur_d:
            ops.append(("del", a[i - 1]))
            i -= 1
        else:
            ops.append(("ins", b[j - 1]))
            j -= 1

    ops.reverse()
    return ops
