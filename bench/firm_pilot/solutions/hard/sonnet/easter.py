import datetime


def easter(year: int) -> datetime.date:
    """Return the date of Gregorian Easter Sunday for 1583 <= year <= 9999."""
    if isinstance(year, bool) or not isinstance(year, int):
        raise TypeError("year must be an int")
    if not 1583 <= year <= 9999:
        raise ValueError("year must be between 1583 and 9999")
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)
