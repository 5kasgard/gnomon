def merge_intervals(xs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge closed integer intervals that overlap or touch; return sorted by start.

    Intervals sharing an endpoint (e.g. (1,3) and (3,5)) are merged.
    Intervals that are merely adjacent (e.g. (1,2) and (3,4)) are not merged.
    """
    if not xs:
        return []
    ordered = sorted((min(a, b), max(a, b)) for a, b in xs)
    result = []
    cur_start, cur_end = ordered[0]
    for a, b in ordered[1:]:
        if a <= cur_end:
            if b > cur_end:
                cur_end = b
        else:
            result.append((cur_start, cur_end))
            cur_start, cur_end = a, b
    result.append((cur_start, cur_end))
    return result
