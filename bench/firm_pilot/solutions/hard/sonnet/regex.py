import sys


def _parse(p):
    n = len(p)
    pos = 0

    def parse_alt(depth):
        nonlocal pos
        alts = [parse_cat(depth)]
        while pos < n and p[pos] == '|':
            pos += 1
            alts.append(parse_cat(depth))
        return ('alt', alts)

    def parse_class():
        nonlocal pos
        # pos is after '['
        neg = False
        if pos < n and p[pos] == '^':
            neg = True
            pos += 1
        singles = set()
        ranges = []
        first = True

        def read():
            nonlocal pos
            if pos >= n:
                raise ValueError("unclosed class")
            c = p[pos]
            pos += 1
            if c == '\\':
                if pos >= n:
                    raise ValueError("trailing backslash")
                c = p[pos]
                pos += 1
            return c

        while True:
            if pos >= n:
                raise ValueError("unclosed class")
            if p[pos] == ']' and not first:
                pos += 1
                break
            first = False
            lo = read()
            if pos + 1 < n and p[pos] == '-' and p[pos + 1] != ']':
                pos += 1
                hi = read()
                if hi < lo:
                    raise ValueError("bad range")
                ranges.append((lo, hi))
            else:
                singles.add(lo)
        return ('set', frozenset(singles), tuple(ranges), neg)

    def parse_cat(depth):
        nonlocal pos
        items = []
        while pos < n:
            c = p[pos]
            if c == '|':
                break
            if c == ')':
                if depth == 0:
                    raise ValueError("unbalanced )")
                break
            if c in '*+?':
                raise ValueError("nothing to repeat")
            pos += 1
            if c == '(':
                node = parse_alt(depth + 1)
                if pos >= n or p[pos] != ')':
                    raise ValueError("unbalanced (")
                pos += 1
            elif c == '[':
                node = parse_class()
            elif c == '.':
                node = ('any',)
            elif c == '\\':
                if pos >= n:
                    raise ValueError("trailing backslash")
                node = ('chr', p[pos])
                pos += 1
            else:
                node = ('chr', c)
            while pos < n and p[pos] in '*+?':
                node = ({'*': 'star', '+': 'plus', '?': 'opt'}[p[pos]], node)
                pos += 1
            items.append(node)
        return ('cat', items)

    tree = parse_alt(0)
    if pos != n:
        raise ValueError("unbalanced )")
    return tree


def _build(tree):
    eps = []
    trans = []

    def new():
        eps.append([])
        trans.append(None)
        return len(eps) - 1

    def b(node):
        t = node[0]
        if t == 'cat':
            s = new()
            cur = s
            for it in node[1]:
                a, z = b(it)
                eps[cur].append(a)
                cur = z
            return s, cur
        if t == 'alt':
            s = new()
            e = new()
            for it in node[1]:
                a, z = b(it)
                eps[s].append(a)
                eps[z].append(e)
            return s, e
        if t in ('chr', 'any', 'set'):
            s = new()
            e = new()
            trans[s] = (node, e)
            return s, e
        a, z = b(node[1])
        s = new()
        e = new()
        eps[s].append(a)
        eps[z].append(e)
        if t == 'star':
            eps[s].append(e)
            eps[z].append(a)
        elif t == 'plus':
            eps[z].append(a)
        else:
            eps[s].append(e)
        return s, e

    s, e = b(tree)
    return eps, trans, s, e


def _test(pred, ch):
    t = pred[0]
    if t == 'chr':
        return ch == pred[1]
    if t == 'any':
        return True
    r = ch in pred[1]
    if not r:
        for lo, hi in pred[2]:
            if lo <= ch <= hi:
                r = True
                break
    return r != pred[3]


def regex_match(pattern, s):
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    tree = _parse(pattern)
    eps, trans, start, end = _build(tree)

    def closure(states):
        seen = set(states)
        stack = list(states)
        while stack:
            x = stack.pop()
            for y in eps[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return seen

    cur = closure([start])
    for ch in s:
        nxt = []
        for x in cur:
            tr = trans[x]
            if tr is not None and _test(tr[0], ch):
                nxt.append(tr[1])
        if not nxt:
            return False
        cur = closure(nxt)
    return end in cur
