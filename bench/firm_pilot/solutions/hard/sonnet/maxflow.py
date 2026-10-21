from collections import deque


def max_flow(n, edges, s, t):
    if s == t:
        raise ValueError("source and sink must differ")
    if not (0 <= s < n and 0 <= t < n):
        raise ValueError("s or t out of range")
    head = [[] for _ in range(n)]
    to = []
    cap = []
    for u, v, c in edges:
        if u == v or c <= 0:
            continue
        head[u].append(len(to)); to.append(v); cap.append(c)
        head[v].append(len(to)); to.append(u); cap.append(0)
    total = 0
    while True:
        level = [-1] * n
        level[s] = 0
        dq = deque([s])
        while dq:
            u = dq.popleft()
            for e in head[u]:
                if cap[e] > 0 and level[to[e]] < 0:
                    level[to[e]] = level[u] + 1
                    dq.append(to[e])
        if level[t] < 0:
            return total
        it = [0] * n
        path = []
        u = s
        while True:
            if u == t:
                f = min(cap[e] for e in path)
                for e in path:
                    cap[e] -= f
                    cap[e ^ 1] += f
                total += f
                # restart from first saturated edge
                for i, e in enumerate(path):
                    if cap[e] == 0:
                        u = to[e ^ 1]
                        del path[i:]
                        break
                continue
            adv = False
            hu = head[u]
            while it[u] < len(hu):
                e = hu[it[u]]
                v = to[e]
                if cap[e] > 0 and level[v] == level[u] + 1:
                    path.append(e)
                    u = v
                    adv = True
                    break
                it[u] += 1
            if not adv:
                if u == s:
                    break
                level[u] = -1
                e = path.pop()
                u = to[e ^ 1]
                it[u] += 1
