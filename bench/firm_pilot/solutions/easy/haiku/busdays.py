import datetime


def _weekdays_in_range(a, b):
    """Count Monday-to-Friday dates d with a <= d < b, where a <= b."""
    n = (b - a).days
    weeks, rem = divmod(n, 7)
    total = weeks * 5
    first = a.weekday()  # Monday == 0 ... Sunday == 6
    for i in range(rem):
        if (first + i) % 7 < 5:
            total += 1
    return total


def business_days(start: datetime.date, end: datetime.date, holidays: set[datetime.date]) -> int:
    """Number of Monday-to-Friday dates d with start <= d < end that are not in holidays.

    If end < start, returns the negated count for the range [end, start).
    """
    if end < start:
        return -business_days(end, start, holidays)
    total = _weekdays_in_range(start, end)
    if holidays:
        for h in holidays:
            if start <= h < end and h.weekday() < 5:
                total -= 1
    return total
