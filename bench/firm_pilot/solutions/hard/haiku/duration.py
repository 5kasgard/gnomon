"""Add ISO 8601 durations to datetime.datetime objects (standard library only).

Supported form: [-]P[nY][nM][nW][nD][T[nH][nM][nS]]

Years and months are applied first as a calendar step (same day in the target
month, clamped to that month's last day). Weeks, days, hours, minutes and
seconds are then applied as exact elapsed time. A leading '-' negates every
component. Anything else raises ValueError, as does a result outside the
years 1 to 9999.
"""

import calendar
import datetime
import re

_DURATION_RE = re.compile(
    r"(-)?P"
    r"(?:([0-9]+)Y)?"
    r"(?:([0-9]+)M)?"
    r"(?:([0-9]+)W)?"
    r"(?:([0-9]+)D)?"
    r"(?:T(?=[0-9])"
    r"(?:([0-9]+)H)?"
    r"(?:([0-9]+)M)?"
    r"(?:([0-9]+)S)?"
    r")?"
)

_MIN_YEAR = 1
_MAX_YEAR = 9999


def _parse(iso):
    if not isinstance(iso, str):
        raise ValueError(f"duration must be a string, not {type(iso).__name__}")
    match = _DURATION_RE.fullmatch(iso)
    if match is None:
        raise ValueError(f"invalid ISO 8601 duration: {iso!r}")
    groups = match.groups()
    if all(group is None for group in groups[1:]):
        raise ValueError(f"ISO 8601 duration has no components: {iso!r}")
    sign = -1 if groups[0] is not None else 1
    years, months, weeks, days, hours, minutes, seconds = (
        int(group) if group is not None else 0 for group in groups[1:]
    )
    return sign, years, months, weeks, days, hours, minutes, seconds


def add_duration(dt: datetime.datetime, iso: str) -> datetime.datetime:
    """Return dt plus the ISO 8601 duration ``iso``.

    Raises ValueError if ``iso`` is not a valid duration or the result falls
    outside the years 1 to 9999.
    """
    sign, years, months, weeks, days, hours, minutes, seconds = _parse(iso)

    try:
        # Calendar step: years and months, clamping the day to the month end.
        total_months = sign * (years * 12 + months)
        if total_months:
            index = dt.year * 12 + (dt.month - 1) + total_months
            year, month_index = divmod(index, 12)
            if not _MIN_YEAR <= year <= _MAX_YEAR:
                raise ValueError(f"result year {year} is outside {_MIN_YEAR}-{_MAX_YEAR}")
            month = month_index + 1
            day = min(dt.day, calendar.monthrange(year, month)[1])
            dt = dt.replace(year=year, month=month, day=day)

        # Exact step: weeks through seconds as elapsed time.
        delta = datetime.timedelta(
            weeks=weeks,
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )
        if sign < 0:
            delta = -delta
        if delta:
            if dt.tzinfo is not None and dt.utcoffset() is not None:
                tz = dt.tzinfo
                dt = (dt.astimezone(datetime.timezone.utc) + delta).astimezone(tz)
            else:
                dt = dt + delta
    except OverflowError as exc:
        raise ValueError(f"result is outside years {_MIN_YEAR}-{_MAX_YEAR}") from exc

    if not _MIN_YEAR <= dt.year <= _MAX_YEAR:
        raise ValueError(f"result year {dt.year} is outside {_MIN_YEAR}-{_MAX_YEAR}")
    return dt
