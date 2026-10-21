"""Full-match regular expression matcher using only the standard library.

Supported syntax: alternation '|', concatenation, postfix '*', '+', '?',
groups '(...)', '.', character classes '[...]' (ranges, '^' negation,
literal ']' first, literal '-' first or last) and backslash escapes.
Matching is done by simulating a Thompson NFA, so it runs in time
polynomial in len(pattern) * len(s).
"""

import sys

_CHR, _ANY, _CLS = 'chr', 'any', 'cls'

_CHAR, _SPLIT, _MATCH = 0, 1, 2


class _Parser:
    def __init__(self, pattern):
        self.p = pattern
        self.n = len(pattern)
        self.i = 0

    def peek(self):
        return self.p[self.i] if self.i < self.n else None

    def parse(self):
        node = self.alt()
        if self.i != self.n:
            raise ValueError("unbalanced parenthesis at position %d" % self.i)
        return node

    def alt(self):
        branches = [self.concat()]
        while self.peek() == '|':
            self.i += 1
            branches.append(self.concat())
        if len(branches) == 1:
            return branches[0]
        return ('alt', branches)

    def concat(self):
        items = []
        while True:
            c = self.peek()
            if c is None or c == '|' or c == ')':
                break
            items.append(self.quant())
        return ('cat', items)

    def quant(self):
        node = self.atom()
        while self.peek() in ('*', '+', '?'):
            op = self.p[self.i]
            self.i += 1
            node = ('rep', node, op)
        return node

    def atom(self):
        c = self.peek()
        if c in ('*', '+', '?'):
            raise ValueError("quantifier with nothing to repeat at position %d" % self.i)
        if c == '(':
            self.i += 1
            node = self.alt()
            if self.peek() != ')':
                raise ValueError("unbalanced parenthesis: missing ')'")
            self.i += 1
            return node
        if c == '[':
            return self.cls()
        if c == '.':
            self.i += 1
            return (_ANY,)
        if c == '\\':
            if self.i + 1 >= self.n:
                raise ValueError("trailing backslash")
            ch = self.p[self.i + 1]
            self.i += 2
            return (_CHR, ch)
        self.i += 1
        return (_CHR, c)

    def _class_char(self):
        c = self.p[self.i]
        if c == '\\':
            if self.i + 1 >= self.n:
                raise ValueError("trailing backslash")
            ch = self.p[self.i + 1]
            self.i += 2
            return ch
        self.i += 1
        return c

    def cls(self):
        self.i += 1  # skip '['
        neg = False
        if self.peek() == '^':
            neg = True
            self.i += 1
        ranges = []
        first = True
        while True:
            if self.i >= self.n:
                raise ValueError("unclosed character class")
            c = self.p[self.i]
            if c == ']' and not first:
                self.i += 1
                break
            first = False
            lo = self._class_char()
            if (self.i + 1 < self.n and self.p[self.i] == '-'
                    and self.p[self.i + 1] != ']'):
                self.i += 1  # skip '-'
                hi = self._class_char()
                if hi < lo:
                    raise ValueError("bad character range %r-%r" % (lo, hi))
                ranges.append((lo, hi))
            else:
                ranges.append((lo, lo))
        return (_CLS, neg, ranges)


class _Prog:
    def __init__(self):
        self.kind = []
        self.pred = []
        self.out1 = []
        self.out2 = []

    def add(self, kind, pred, o1, o2):
        self.kind.append(kind)
        self.pred.append(pred)
        self.out1.append(o1)
        self.out2.append(o2)
        return len(self.kind) - 1


def _lit(ch):
    return lambda c: c == ch


def _any(c):
    return True


def _class_pred(neg, ranges):
    if neg:
        def pred(c):
            for lo, hi in ranges:
                if lo <= c <= hi:
                    return False
            return True
    else:
        def pred(c):
            for lo, hi in ranges:
                if lo <= c <= hi:
                    return True
            return False
    return pred


def _emit(prog, node, nxt):
    """Compile node so that it continues to state nxt; return entry state."""
    tag = node[0]
    if tag == _CHR:
        return prog.add(_CHAR, _lit(node[1]), nxt, None)
    if tag == _ANY:
        return prog.add(_CHAR, _any, nxt, None)
    if tag == _CLS:
        return prog.add(_CHAR, _class_pred(node[1], node[2]), nxt, None)
    if tag == 'cat':
        s = nxt
        for item in reversed(node[1]):
            s = _emit(prog, item, s)
        return s
    if tag == 'alt':
        entries = [_emit(prog, b, nxt) for b in node[1]]
        entry = entries[-1]
        for e in reversed(entries[:-1]):
            entry = prog.add(_SPLIT, None, e, entry)
        return entry
    if tag == 'rep':
        child, op = node[1], node[2]
        if op == '?':
            body = _emit(prog, child, nxt)
            return prog.add(_SPLIT, None, body, nxt)
        split = prog.add(_SPLIT, None, None, nxt)
        body = _emit(prog, child, split)
        prog.out1[split] = body
        return split if op == '*' else body
    raise ValueError("internal error: unknown node %r" % (tag,))


def _run(prog, start, s):
    count = len(prog.kind)
    mark = [0] * count
    kind, pred, out1, out2 = prog.kind, prog.pred, prog.out1, prog.out2
    gen = [0]

    def closure(seeds):
        gen[0] += 1
        g = gen[0]
        stack = list(seeds)
        out = []
        while stack:
            st = stack.pop()
            if mark[st] == g:
                continue
            mark[st] = g
            out.append(st)
            if kind[st] == _SPLIT:
                stack.append(out2[st])
                stack.append(out1[st])
        return out

    cur = closure([start])
    for ch in s:
        seeds = []
        for st in cur:
            if kind[st] == _CHAR and pred[st](ch):
                seeds.append(out1[st])
        if not seeds:
            return False
        cur = closure(seeds)
    return any(kind[st] == _MATCH for st in cur)


def regex_match(pattern: str, s: str) -> bool:
    """Return True if the whole of s matches pattern. Raises ValueError on a bad pattern."""
    old_limit = sys.getrecursionlimit()
    if old_limit < 10000:
        sys.setrecursionlimit(10000)
    try:
        tree = _Parser(pattern).parse()
        prog = _Prog()
        accept = prog.add(_MATCH, None, None, None)
        start = _emit(prog, tree, accept)
        return _run(prog, start, s)
    finally:
        sys.setrecursionlimit(old_limit)
