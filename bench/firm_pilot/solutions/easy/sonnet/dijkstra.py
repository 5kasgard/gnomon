import heapq


def shortest_path(graph, src, dst):
    if src == dst:
        return (0, [src])
    rev = {}
    for u, edges in graph.items():
        for v, w in edges:
            rev.setdefault(v, []).append((u, w))
    dist = {dst: 0}
    heap = [(0, dst)]
    while heap:
        d, v = heapq.heappop(heap)
        if d > dist.get(v, float("inf")):
            continue
        for u, w in rev.get(v, ()):
            nd = d + w
            if nd < dist.get(u, float("inf")):
                dist[u] = nd
                heapq.heappush(heap, (nd, u))
    if src not in dist:
        return None

    def cands(u):
        du = dist[u]
        return iter(sorted({v for v, w in graph.get(u, ())
                            if v in dist and w + dist[v] == du}))

    path = [src]
    on = {src}
    stack = [cands(src)]
    while stack:
        nxt = next(stack[-1], None)
        if nxt is None:
            stack.pop()
            on.discard(path.pop())
            continue
        if nxt in on:
            continue
        path.append(nxt)
        if nxt == dst:
            return (dist[src], path)
        on.add(nxt)
        stack.append(cands(nxt))
    return None
