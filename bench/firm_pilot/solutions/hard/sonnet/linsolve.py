from fractions import Fraction


def linsolve(A, b):
    m = len(A)
    n = len(A[0])
    M = [[Fraction(v) for v in A[i]] + [Fraction(b[i])] for i in range(m)]
    pivots = []
    r = 0
    for c in range(n):
        if r >= m:
            break
        p = None
        for i in range(r, m):
            if M[i][c] != 0:
                p = i
                break
        if p is None:
            continue
        M[r], M[p] = M[p], M[r]
        pv = M[r][c]
        M[r] = [v / pv for v in M[r]]
        for i in range(m):
            if i != r and M[i][c] != 0:
                f = M[i][c]
                M[i] = [a - f * bb for a, bb in zip(M[i], M[r])]
        pivots.append(c)
        r += 1
    for i in range(r, m):
        if M[i][n] != 0:
            return ("none", None)
    x = [Fraction(0)] * n
    for i, c in enumerate(pivots):
        x[c] = M[i][n]
    if len(pivots) == n:
        return ("unique", x)
    return ("infinite", x)
