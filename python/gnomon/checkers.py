"""Native checkers: claims the computer can settle exactly, it settles.

A model is never asked to do arithmetic or look up a weekday when the machine
can compute the answer. Each checker runs as its own registered writer and
party, so its verdicts go through the same gate as anyone else's: it cannot
check claims its own party made, and it gives one verdict per claim.

What a checker says is deliberately narrow:

* **supports** or **refutes** only if what it checked *is* the whole
  statement;
* **abstain** if the statement says more than it checked ("it is not true
  that ...", "... if 2 + 2 = 5"), since the other words could reverse it
  (the note says exactly what was checked);
* nothing at all if the notation reads two ways and another reading makes
  the statement true.

Only claims asserted true or false are checked; questions, suggestions and
hypotheticals are skipped.
"""

from __future__ import annotations

import ast
import calendar
import math
import datetime as dt
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional

__all__ = ["Finding", "RunReport", "check_arithmetic", "check_calendar", "judge", "run"]


@dataclass(frozen=True)
class Finding:
    span: tuple[int, int]  # where in the statement
    holds: bool
    note: str


# ------------------------------------------------------------- arithmetic

# ASCII digits only: \d would also match other scripts' digits, which the
# number parsers then reject or misread.
_NUM = r"[0-9]+(?:\.[0-9]+)?"
_NUM_AT = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
# Characters an arithmetic expression may contain. Letters may not, so
# "x = 5" or "v2 = 3" are not arithmetic claims.
_EXPR_CHARS = set("0123456789.+-*/×÷^() \t")
_TOKENS = re.compile(r"[0-9]+(?:\.[0-9]+)?|[-+*/×÷^()]|[ \t]+")
# "2026-10-14", "10-12", "3-1": a hyphen between digits with no spaces is a
# date, range or score as often as a minus. Ambiguous, so not checked.
_TIGHT_HYPHEN = re.compile(r"[0-9]-[0-9]")
_CHAINED_POW = re.compile(r"\^[^-+*/×÷=]*\^")
_NEG_POW = re.compile(r"(?:^|[-+*/×÷(])\s*-\s*[0-9.]+\s*\^")
# "1.000" is one thousand in much of Europe, one in English.
_THOUSANDS = re.compile(r"(?<![0-9.])[0-9]{1,3}\.[0-9]{3}(?![0-9])")
_MAX_EXP = 64
_MAX_DIGITS = 400
_MAX_EXPR = 500  # characters
_MAX_EQUATIONS = 50  # per statement


class _Unsafe(Exception):
    pass


def _eval(node: ast.AST) -> Fraction:
    """Exact evaluation of + - * / ** on literals, nothing else."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        raise _Unsafe("unparsed number")  # numbers arrive as strings, see below
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "F" and len(node.args) == 1:
        return Fraction(node.args[0].value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Add):
            r = a + b
        elif isinstance(node.op, ast.Sub):
            r = a - b
        elif isinstance(node.op, ast.Mult):
            r = a * b
        elif isinstance(node.op, ast.Div):
            if b == 0:
                raise _Unsafe("division by zero")
            r = a / b
        elif isinstance(node.op, ast.Pow):
            if b.denominator != 1 or abs(b) > _MAX_EXP:
                raise _Unsafe("exponent too large or fractional")
            r = a ** int(b)
        else:
            raise _Unsafe("operator")
        if len(str(r.numerator)) > _MAX_DIGITS or len(str(r.denominator)) > _MAX_DIGITS:
            raise _Unsafe("result too large")
        return r
    raise _Unsafe(type(node).__name__)


def _exact(expr: str) -> Fraction:
    # Wrap every literal as F("...") so decimals stay exact (0.1 + 0.2 == 0.3).
    py = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    py = re.sub(_NUM, lambda m: f'F("{m.group(0)}")', py)
    return _eval(ast.parse(py, mode="eval"))


def _show(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f} (= {float(f):g})"


def _equations(statement: str):
    """Yield (start, lhs, op, rhs, end) for each "<expression> = <number>".

    A linear scan, not a backtracking pattern: each "=" looks left over at
    most _MAX_EXPR expression characters and right for one number, so no
    input can make this slow.
    """
    seen = 0
    for i, ch in enumerate(statement):
        if ch not in "=≠":
            continue
        seen += 1
        if seen > _MAX_EQUATIONS:
            return
        j = i
        while j > 0 and statement[j - 1] in _EXPR_CHARS and i - j <= _MAX_EXPR:
            j -= 1
        if i - j > _MAX_EXPR:
            continue  # too long to be a claim anyone checks by hand
        lhs = statement[j:i]
        lead = len(lhs) - len(lhs.lstrip())
        start = j + lead
        lhs = lhs.strip()
        if not lhs or (start > 0 and (statement[start - 1].isalnum() or statement[start - 1] in "._")):
            continue
        k = i + 1
        while k < len(statement) and statement[k] in " \t":
            k += 1
        m = _NUM_AT.match(statement, k)
        if not m:
            continue
        end = m.end()
        if end < len(statement) and (statement[end].isalnum() or statement[end] in "_-"
                                     or (statement[end] == "." and end + 1 < len(statement)
                                         and statement[end + 1].isdigit())):
            continue
        if _TIGHT_HYPHEN.search(lhs):
            continue
        # Conventions differ on "-2^2" (-4 or 4) and "2^3^2" (512 or 64).
        if _CHAINED_POW.search(lhs) or _NEG_POW.search(lhs):
            continue
        tokens = _TOKENS.findall(lhs)
        if "".join(tokens) != lhs or not any(t in "+-*/×÷^" for t in tokens[1:]):
            continue  # not purely arithmetic, or just a number on its own
        yield start, lhs, ch, m.group(0), end


def _european_reading_holds(lhs: str, rhs: str) -> bool:
    """Does reading "1.000" as one thousand make the equation true?"""
    if not _THOUSANDS.search(lhs + " " + rhs):
        return False
    swap = lambda text: _THOUSANDS.sub(lambda m: m.group(0).replace(".", ""), text)
    try:
        return _exact(swap(lhs)) == Fraction(swap(rhs))
    except (_Unsafe, SyntaxError, ValueError, ZeroDivisionError, RecursionError, MemoryError):
        return False


def _rounded_or_truncated(left: Fraction, rhs: str) -> bool:
    """Is rhs `left` rounded (half up or half even) or cut to rhs's decimals?"""
    if "." not in rhs:
        return False
    q = 10 ** len(rhs.split(".")[1])
    right = Fraction(rhs)
    sign = -1 if left < 0 else 1
    a = abs(left) * q
    readings = {
        sign * Fraction(math.floor(a + Fraction(1, 2)), q),  # half up
        Fraction(round(left * q), q),  # half even
    }
    if q >= 1000:  # "0.666" is a cut-off expansion; "0.6" is just wrong
        readings.add(sign * Fraction(math.floor(a), q))
    return right in readings


def _factorial_reading_holds(statement: str, end: int, left: Fraction, rhs: str) -> bool:
    """"= 4!": is it 4 factorial (and then true), or an exclamation?"""
    if end >= len(statement) or statement[end] != "!" or "." in rhs or rhs.startswith("-"):
        return False
    n = int(rhs)
    return n <= 20 and left == math.factorial(n)


def check_arithmetic(statement: str) -> list[Finding]:
    out = []
    for start, lhs, op, rhs, end in _equations(statement):
        try:
            left = _exact(lhs)
            right = Fraction(rhs)
        except (_Unsafe, SyntaxError, ValueError, ZeroDivisionError, RecursionError, MemoryError):
            continue  # not something we can settle exactly: say nothing
        equal = left == right
        if not equal and op == "=":
            # Ambiguous rather than wrong: say nothing, but only when another
            # reading actually makes the statement true.
            if _european_reading_holds(lhs, rhs):
                continue  # "1.000 + 1 = 1.001"
            if _rounded_or_truncated(left, rhs):
                continue  # "1/3 = 0.333", "1/8 = 0.13", "2/3 = 0.666"
            if _factorial_reading_holds(statement, end, left, rhs):
                continue  # "20 + 4 = 4!"
        holds = equal if op == "=" else not equal
        out.append(Finding((start, end), holds, f"{lhs} = {_show(left)}, claimed {op} {rhs}"))
    return out


# --------------------------------------------------------------- calendar

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_DAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}
_M = "|".join(sorted(_MONTHS, key=len, reverse=True))
_D = "|".join(_DAYS)
_DATE_IS_DAY = re.compile(
    rf"(?<![\w-])(?:(?P<iso>[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})"
    rf"|(?<![0-9])(?P<d1>[0-9]{{1,2}})(?:st|nd|rd|th)?\s+(?P<m1>{_M})\.?,?\s+(?P<y1>[0-9]{{4}})"
    rf"|(?P<m2>{_M})\.?\s+(?P<d2>[0-9]{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y2>[0-9]{{4}}))"
    rf"\s+(?:is|was|will be|falls on)\s+(?:a\s+)?(?P<day>{_D})\b",
    re.IGNORECASE | re.ASCII,
)
# Before the Gregorian calendar was adopted everywhere, a date's weekday
# depends on which calendar the speaker meant. Not checked.
_FIRST_SAFE_YEAR = 1923


def check_calendar(statement: str) -> list[Finding]:
    out = []
    for m in _DATE_IS_DAY.finditer(statement):
        try:
            if m.group("iso"):
                date = dt.date.fromisoformat(m.group("iso"))
            elif m.group("d1"):
                date = dt.date(int(m.group("y1")), _MONTHS[m.group("m1").lower()], int(m.group("d1")))
            else:
                date = dt.date(int(m.group("y2")), _MONTHS[m.group("m2").lower()], int(m.group("d2")))
        except (ValueError, KeyError):
            year = m.group("y1") or m.group("y2") or (m.group("iso") or "0")[:4]
            if int(year) >= _FIRST_SAFE_YEAR:
                out.append(Finding(m.span(), False, f"'{m.group(0)}': no such date"))
            continue
        if date.year < _FIRST_SAFE_YEAR:
            continue
        actual = calendar.day_name[date.weekday()]
        claimed = m.group("day")
        out.append(Finding(m.span(), actual.lower() == claimed.lower(),
                           f"{date.isoformat()} is a {actual}, claimed {claimed}"))
    return out


CHECKERS = (check_arithmetic, check_calendar)


# ------------------------------------------------------------------ verdicts

def _covers_whole(statement: str, spans: Iterable[tuple[int, int]]) -> bool:
    """Is the statement nothing but the checked parts (plus filler)?"""
    rest = list(statement)
    for a, b in spans:
        for i in range(a, b):
            rest[i] = " "
    leftover = re.sub(r"[\s.,;:!]+|\b(?:and|so|also|then|which|means|that|indeed|yes)\b", "", "".join(rest), flags=re.IGNORECASE)
    return leftover == ""


def judge(statement: str) -> Optional[tuple[str, str]]:
    """(result, note) for a statement, or None if nothing here is checkable."""
    findings = [f for check in CHECKERS for f in check(statement)]
    if not findings:
        return None
    notes = "; ".join(f.note for f in findings)
    if not _covers_whole(statement, [f.span for f in findings]):
        # The statement says more than was checked ("it is not true that
        # ...", "... if 2 + 2 = 5"): the words around it can reverse it.
        return "abstain", "checked only part of the statement: " + notes
    if any(not f.holds for f in findings):
        return "refutes", notes
    return "supports", notes


@dataclass
class RunReport:
    verdicts: list[tuple[str, str, str]]  # (claim, result, note)
    failures: list[tuple[str, str]]  # (claim, error) for claims that could not be checked


def run(memory, writer: str, *, record_time: Optional[str] = None,
        compartments: Optional[Iterable[str]] = None) -> RunReport:
    """Check every claim the checker's writer can see (and, if given, only
    in these compartments: a caller that sees less must not learn more).

    Only claims asserted true or false are checked, and the result is judged
    against what the claim asserts: a claim that "2 + 2 = 5" is false is
    supported. One claim that fails to check never stops the others.
    """
    from . import GateError

    me = memory.writers().get(writer)
    if me is None:
        raise GateError("E_UNKNOWN_WRITER", f"writer '{writer}' is not registered")
    scope = [c for c in me["compartments"] if compartments is None or c in set(compartments)]
    state = memory.state(compartments=scope)["state"]
    report = RunReport([], [])
    for cid, c in sorted(state["claims"].items()):
        body = c["body"]
        if body["stance"] in ("asked", "hypothetical", "suggested") or body["truth"] not in ("true", "false"):
            continue
        try:
            verdict = judge(body["statement"])
            if verdict is None:
                continue
            result, note = verdict
            if body["truth"] == "false" and result != "abstain":
                result = "refutes" if result == "supports" else "supports"
                note += " (the claim asserts this is false)"
            memory.verdict(writer, cid, result, "computed", note=note, record_time=record_time)
        except GateError as e:
            # Already judged by this party, or a claim this party asserted:
            # the gate's answer stands, and the checker moves on.
            if e.code not in ("E_DUPLICATE", "E_SELF_VERIFY", "E_SAME_PARTY_VERIFY"):
                report.failures.append((cid, f"{e.code}: {e.message}"))
            continue
        except Exception as e:  # a checker bug must not hide every later claim
            report.failures.append((cid, f"{type(e).__name__}: {e}"))
            continue
        report.verdicts.append((cid, result, note))
    return report
