def edit_script(a: str, b: str) -> list:
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    # direction table: bytearray per row; 0 keep/sub(diag),1 del(up),2 ins(left)
    dirs = [bytearray(m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        dirs[0][j] = 2
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        di = dirs[i]
        di[0] = 1
        ca = a[i - 1]
        for j in range(1, m + 1):
            d = prev[j - 1] + (ca != b[j - 1])
            best, dr = d, 0
            u = prev[j] + 1
            if u < best:
                best, dr = u, 1
            l = cur[j - 1] + 1
            if l < best:
                best, dr = l, 2
            cur[j] = best
            di[j] = dr
        prev = cur
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        dr = dirs[i][j]
        if dr == 0:
            c, d = a[i - 1], b[j - 1]
            ops.append(("keep", c) if c == d else ("sub", c, d))
            i -= 1; j -= 1
        elif dr == 1:
            ops.append(("del", a[i - 1])); i -= 1
        else:
            ops.append(("ins", b[j - 1])); j -= 1
    ops.reverse()
    return ops
