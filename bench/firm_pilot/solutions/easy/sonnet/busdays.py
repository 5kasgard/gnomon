import datetime


def _weekdays_before(o):
    # Weekdays among ordinals 1..o-1 (ordinal 1 is a Monday).
    q, r = divmod(o - 1, 7)
    return q * 5 + min(r, 5)


def _count(a, b, holidays):
    ao, bo = a.toordinal(), b.toordinal()
    total = _weekdays_before(bo) - _weekdays_before(ao)
    for h in holidays:
        if a <= h < b and h.weekday() < 5:
            total -= 1
    return total


def business_days(start: datetime.date, end: datetime.date, holidays: set) -> int:
    if end < start:
        return -_count(end, start, holidays)
    return _count(start, end, holidays)
