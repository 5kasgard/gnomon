import re

# Canonical Roman numerals for 1..3999: thousands, hundreds, tens, units,
# each group in its unique standard form (subtractive pairs only as listed).
_ROMAN_RE = re.compile(
    r"M{0,3}"
    r"(?:CM|CD|D?C{0,3})"
    r"(?:XC|XL|L?X{0,3})"
    r"(?:IX|IV|V?I{0,3})"
)

_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int:
    """Convert a canonical Roman numeral (1..3999) to an int.

    Raises ValueError for any string that is not the unique canonical form
    of a number in that range (including empty strings, lowercase letters,
    non-canonical forms such as 'IIII' or 'IC', and non-str input).
    """
    if not isinstance(s, str):
        raise ValueError(f"not a Roman numeral: {s!r}")
    if not s or _ROMAN_RE.fullmatch(s) is None:
        raise ValueError(f"not a canonical Roman numeral: {s!r}")

    total = 0
    n = len(s)
    for i, ch in enumerate(s):
        value = _VALUES[ch]
        if i + 1 < n and value < _VALUES[s[i + 1]]:
            total -= value
        else:
            total += value
    return total
