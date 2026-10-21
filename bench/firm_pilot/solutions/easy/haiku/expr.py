"""Exact rational arithmetic evaluator for integer expressions.

Grammar (lowest to highest precedence):
    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/') unary)*
    unary  := ('+' | '-') unary | power
    power  := atom ('**' unary)?          # right-associative, exponent may be signed
    atom   := NUMBER | '(' expr ')'
"""

from fractions import Fraction


class _DivisionByZero(ValueError, ZeroDivisionError):
    """Raised on division by zero; is both a ValueError and a ZeroDivisionError."""


def _tokenize(s):
    tokens = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
        elif '0' <= ch <= '9':
            j = i
            while j < n and '0' <= s[j] <= '9':
                j += 1
            tokens.append(('num', int(s[i:j])))
            i = j
        elif s.startswith('**', i):
            tokens.append(('**', None))
            i += 2
        elif ch in '+-*/()':
            tokens.append((ch, None))
            i += 1
        else:
            raise ValueError("invalid character %r at position %d" % (ch, i))
    return tokens


def _pow(base, exp):
    if exp.denominator != 1:
        raise ValueError("exponent must be an integer, got %s" % exp)
    e = exp.numerator
    if base == 0 and e < 0:
        raise _DivisionByZero("0 cannot be raised to a negative power")
    return base ** e


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos][0]
        return None

    def advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.expr()
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token at position %d" % self.pos)
        return value

    def expr(self):
        value = self.term()
        while self.peek() in ('+', '-'):
            op = self.advance()[0]
            right = self.term()
            value = value + right if op == '+' else value - right
        return value

    def term(self):
        value = self.unary()
        while self.peek() in ('*', '/'):
            op = self.advance()[0]
            right = self.unary()
            if op == '*':
                value = value * right
            else:
                if right == 0:
                    raise _DivisionByZero("division by zero")
                value = value / right
        return value

    def unary(self):
        negate = False
        while self.peek() in ('+', '-'):
            if self.advance()[0] == '-':
                negate = not negate
        value = self.power()
        return -value if negate else value

    def power(self):
        base = self.atom()
        if self.peek() == '**':
            self.advance()
            exp = self.unary()
            return _pow(base, exp)
        return base

    def atom(self):
        kind = self.peek()
        if kind == 'num':
            return Fraction(self.advance()[1])
        if kind == '(':
            self.advance()
            value = self.expr()
            if self.peek() != ')':
                raise ValueError("expected ')' at position %d" % self.pos)
            self.advance()
            return value
        if kind is None:
            raise ValueError("unexpected end of expression")
        raise ValueError("unexpected token at position %d" % self.pos)


def evaluate(s: str) -> Fraction:
    """Evaluate an integer arithmetic expression exactly, returning a Fraction."""
    try:
        tokens = _tokenize(s)
        result = _Parser(tokens).parse()
    except RecursionError:
        raise ValueError("expression nested too deeply") from None
    return Fraction(result)
