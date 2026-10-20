import heapq


def topo_sort(deps: dict[str, list[str]]) -> list[str]:
    """Return a dependency-respecting order of all nodes.

    deps[n] lists the nodes that n depends on (which must come before n).
    Nodes that appear only as dependencies are included as well. When several
    nodes are ready at once, the alphabetically smallest is emitted first.
    Raises ValueError if the dependencies contain a cycle (including a
    self-dependency).
    """
    parents = {}
    for n, ds in deps.items():
        parents.setdefault(n, set()).update(ds or ())
        for d in parents[n]:
            parents.setdefault(d, set())

    children = {n: set() for n in parents}
    indegree = {}
    for n, ps in parents.items():
        indegree[n] = len(ps)
        for p in ps:
            children[p].add(n)

    heap = [n for n, k in indegree.items() if k == 0]
    heapq.heapify(heap)

    order = []
    while heap:
        node = heapq.heappop(heap)
        order.append(node)
        for c in children[node]:
            indegree[c] -= 1
            if indegree[c] == 0:
                heapq.heappush(heap, c)

    if len(order) != len(parents):
        raise ValueError("dependency cycle detected")
    return order
