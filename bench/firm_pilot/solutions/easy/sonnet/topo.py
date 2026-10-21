import heapq


def topo_sort(deps):
    nodes = set(deps)
    for ds in deps.values():
        nodes.update(ds)
    prereqs = {n: set(deps.get(n, ())) for n in nodes}
    dependents = {n: [] for n in nodes}
    for n, ps in prereqs.items():
        for p in ps:
            dependents[p].append(n)
    remaining = {n: len(ps) for n, ps in prereqs.items()}
    heap = [n for n, c in remaining.items() if c == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        n = heapq.heappop(heap)
        out.append(n)
        for m in dependents[n]:
            remaining[m] -= 1
            if remaining[m] == 0:
                heapq.heappush(heap, m)
    if len(out) != len(nodes):
        raise ValueError("cycle detected")
    return out
