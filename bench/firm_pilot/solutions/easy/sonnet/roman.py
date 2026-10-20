_VALUES = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
           (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
           (5, "V"), (4, "IV"), (1, "I")]
_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _to_roman(n):
    out = []
    for v, sym in _VALUES:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def roman_to_int(s: str) -> int:
    if not isinstance(s, str) or not s:
        raise ValueError("invalid Roman numeral")
    if any(c not in _MAP for c in s):
        raise ValueError("invalid Roman numeral")
    total = 0
    for i, c in enumerate(s):
        v = _MAP[c]
        if i + 1 < len(s) and _MAP[s[i + 1]] > v:
            total -= v
        else:
            total += v
    if not 1 <= total <= 3999 or _to_roman(total) != s:
        raise ValueError("invalid Roman numeral")
    return total
