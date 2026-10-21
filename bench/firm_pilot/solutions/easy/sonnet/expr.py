import re
from fractions import Fraction

_TOK = re.compile(r'\s*(?:(\d+)|(\*\*|[-+*/()]))')


def _tokenize(s):
    toks = []
    pos = 0
    n = len(s)
    while True:
        m = re.compile(r'\s*').match(s, pos)
        pos = m.end()
        if pos >= n:
            break
        m = _TOK.match(s, pos)
        if not m:
            raise ValueError("syntax error")
        if m.group(1) is not None:
            toks.append(('n', int(m.group(1))))
        else:
            toks.append(('o', m.group(2)))
        pos = m.end()
    return toks


def evaluate(s):
    if not isinstance(s, str):
        raise ValueError("expected string")
    toks = _tokenize(s)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def expr():
        nonlocal i
        v = term()
        while peek() in (('o', '+'), ('o', '-')):
            op = toks[i][1]
            i += 1
            r = term()
            v = v + r if op == '+' else v - r
        return v

    def term():
        nonlocal i
        v = unary()
        while peek() in (('o', '*'), ('o', '/')):
            op = toks[i][1]
            i += 1
            r = unary()
            if op == '*':
                v = v * r
            else:
                if r == 0:
                    raise ValueError("division by zero")
                v = v / r
        return v

    def unary():
        nonlocal i
        if peek() in (('o', '+'), ('o', '-')):
            op = toks[i][1]
            i += 1
            v = unary()
            return -v if op == '-' else v
        return power()

    def power():
        nonlocal i
        b = atom()
        if peek() == ('o', '**'):
            i += 1
            e = unary()
            if e.denominator != 1:
                raise ValueError("non-integer exponent")
            e = e.numerator
            if b == 0 and e < 0:
                raise ValueError("division by zero")
            return Fraction(b) ** e
        return b

    def atom():
        nonlocal i
        t, v = peek()
        if t == 'n':
            i += 1
            return Fraction(v)
        if (t, v) == ('o', '('):
            i += 1
            r = expr()
            if peek() != ('o', ')'):
                raise ValueError("expected )")
            i += 1
            return r
        raise ValueError("syntax error")

    r = expr()
    if i != len(toks):
        raise ValueError("syntax error")
    return r
