import heapq


def shortest_path(graph, src, dst):
    """Return (cost, path) for the cheapest simple path src -> dst.

    graph: dict mapping node -> list of (neighbor, non-negative int weight).
    Nodes may appear only as edge targets. Ties are broken by choosing the
    lexicographically smallest node list. Returns None if dst is unreachable.
    """
    if src == dst:
        return (0, [src])

    # Reverse adjacency so that Dijkstra from dst gives distance-to-dst.
    rev = {}
    for u, edges in graph.items():
        for v, w in edges:
            rev.setdefault(v, []).append((u, w))

    dist = {dst: 0}
    heap = [(0, dst)]
    while heap:
        d, v = heapq.heappop(heap)
        if d > dist[v]:
            continue
        for u, w in rev.get(v, ()):
            nd = d + w
            if u not in dist or nd < dist[u]:
                dist[u] = nd
                heapq.heappush(heap, (nd, u))

    if src not in dist:
        return None
    total = dist[src]

    def next_nodes(u):
        # Edges that lie on some shortest path to dst, in lexicographic order.
        du = dist[u]
        return sorted({
            v for v, w in graph.get(u, ())
            if v in dist and w + dist[v] == du
        })

    # DFS over tight edges in sorted order; the first simple path that
    # reaches dst is the lexicographically smallest optimal path.
    path = [src]
    onpath = {src}
    stack = [iter(next_nodes(src))]
    while stack:
        v = next(stack[-1], None)
        if v is None:
            stack.pop()
            onpath.discard(path.pop())
            continue
        if v == dst:
            return (total, path + [dst])
        if v in onpath:
            continue
        path.append(v)
        onpath.add(v)
        stack.append(iter(next_nodes(v)))
    return None
