"""Maximum flow via Dinic's algorithm (standard library only).

max_flow(n, edges, s, t) returns the value of a maximum s-t flow in a directed
graph with nodes 0..n-1 and edges given as (u, v, capacity) triples.
"""

from collections import deque


def max_flow(n: int, edges: list[tuple[int, int, int]], s: int, t: int) -> int:
    if s == t:
        raise ValueError("source and sink must be different nodes")
    if not (0 <= s < n and 0 <= t < n):
        raise ValueError("source and sink must be valid node indices")

    # Residual graph in flat arrays: edge i and i ^ 1 are a forward/reverse pair.
    to: list[int] = []
    cap: list[int] = []
    adj: list[list[int]] = [[] for _ in range(n)]

    for edge in edges:
        u, v, c = edge[0], edge[1], edge[2]
        if u == v:
            # Self-loops never carry useful flow.
            continue
        adj[u].append(len(to))
        to.append(v)
        cap.append(int(c))
        adj[v].append(len(to))
        to.append(u)
        cap.append(0)

    total = 0
    while True:
        # BFS phase: build level graph from s over edges with positive residual capacity.
        level = [-1] * n
        level[s] = 0
        queue = deque([s])
        while queue:
            u = queue.popleft()
            for e in adj[u]:
                v = to[e]
                if cap[e] > 0 and level[v] < 0:
                    level[v] = level[u] + 1
                    queue.append(v)
        if level[t] < 0:
            break

        # DFS phase: send blocking flow along level-increasing paths (iterative).
        it = [0] * n
        path: list[int] = []
        u = s
        while True:
            if u == t:
                f = min(cap[e] for e in path)
                for e in path:
                    cap[e] -= f
                    cap[e ^ 1] += f
                total += f
                path = []
                u = s
                continue

            lst = adj[u]
            i = it[u]
            advanced = False
            while i < len(lst):
                e = lst[i]
                v = to[e]
                if cap[e] > 0 and level[v] == level[u] + 1:
                    it[u] = i
                    path.append(e)
                    u = v
                    advanced = True
                    break
                i += 1
            if advanced:
                continue

            it[u] = i
            # Dead end: retreat one edge and skip it.
            if u == s:
                break
            e = path.pop()
            u = to[e ^ 1]
            it[u] += 1

    return total
