def min_window(s: str, t: str) -> str:
    """Return the shortest substring of s containing every character of t
    with at least the multiplicity it has in t. Among shortest, the leftmost.
    Return "" if no such substring exists or t is empty. Runs in O(len(s) + len(t))."""
    if not t or not s:
        return ""

    need = {}
    for ch in t:
        need[ch] = need.get(ch, 0) + 1
    required = len(need)

    window = {}
    formed = 0
    left = 0
    best_len = len(s) + 1
    best_left = 0

    for right in range(len(s)):
        ch = s[right]
        if ch in need:
            count = window.get(ch, 0) + 1
            window[ch] = count
            if count == need[ch]:
                formed += 1

        while formed == required:
            length = right - left + 1
            if length < best_len:
                best_len = length
                best_left = left
            lch = s[left]
            if lch in need:
                lcount = window[lch]
                if lcount == need[lch]:
                    formed -= 1
                window[lch] = lcount - 1
            left += 1

    if best_len > len(s):
        return ""
    return s[best_left:best_left + best_len]
