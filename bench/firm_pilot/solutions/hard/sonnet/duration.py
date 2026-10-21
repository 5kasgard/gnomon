import calendar
import datetime
import re

_RE = re.compile(
    r"(-)?P(?:([0-9]+)Y)?(?:([0-9]+)M)?(?:([0-9]+)W)?(?:([0-9]+)D)?"
    r"(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+)S)?)?",
    re.ASCII,
)


def add_duration(dt: datetime.datetime, iso: str) -> datetime.datetime:
    if not isinstance(iso, str) or not isinstance(dt, datetime.datetime):
        raise ValueError("invalid argument")
    m = _RE.fullmatch(iso)
    if not m:
        raise ValueError("invalid duration")
    neg = m.group(1) is not None
    groups = m.groups()[1:]
    if all(g is None for g in groups):
        raise ValueError("no components")
    if "T" in iso:
        if all(g is None for g in groups[4:]):
            raise ValueError("T without time component")
    y, mo, w, d, h, mi, s = (int(g) if g is not None else 0 for g in groups)
    sign = -1 if neg else 1
    try:
        total = dt.year * 12 + (dt.month - 1) + sign * (y * 12 + mo)
        ny, nm = divmod(total, 12)
        nm += 1
        if not (1 <= ny <= 9999):
            raise ValueError("year out of range")
        day = min(dt.day, calendar.monthrange(ny, nm)[1])
        res = dt.replace(year=ny, month=nm, day=day)
        delta = datetime.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
        res = res - delta if neg else res + delta
    except OverflowError:
        raise ValueError("result out of range")
    return res
