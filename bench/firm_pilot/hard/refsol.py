from ref2 import *
def edit_script(a, b):
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): D[i][0] = i
    for j in range(m + 1): D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i-1][j] + 1, D[i][j-1] + 1, D[i-1][j-1] + (a[i-1] != b[j-1]))
    ops = []; i, j = n, m
    while i or j:
        if i and j and D[i][j] == D[i-1][j-1] + (a[i-1] != b[j-1]):
            ops.append(("keep", a[i-1]) if a[i-1] == b[j-1] else ("sub", a[i-1], b[j-1])); i -= 1; j -= 1
        elif i and D[i][j] == D[i-1][j] + 1: ops.append(("del", a[i-1])); i -= 1
        else: ops.append(("ins", b[j-1])); j -= 1
    return ops[::-1]
