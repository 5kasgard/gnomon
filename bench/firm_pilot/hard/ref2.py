import datetime, calendar, re
from fractions import Fraction
from collections import Counter, deque

# 1 regex: Thompson-style simulation over a parsed AST
def regex_match(pattern, s):
    pos = 0
    def peek():
        return pattern[pos] if pos < len(pattern) else None
    def eat():
        nonlocal pos; c = pattern[pos]; pos += 1; return c
    def alt():
        branches = [concat()]
        while peek() == "|":
            eat(); branches.append(concat())
        return ("alt", branches) if len(branches) > 1 else branches[0]
    def concat():
        items = []
        while peek() is not None and peek() not in "|)":
            items.append(repeat())
        return ("cat", items)
    def repeat():
        a = atom()
        while peek() in ("*", "+", "?"):
            a = (eat(), a)
        return a
    def atom():
        c = peek()
        if c is None or c in "*+?": raise ValueError("bad pattern")
        eat()
        if c == "(":
            r = alt()
            if peek() != ")": raise ValueError("unclosed")
            eat(); return r
        if c == ")": raise ValueError("unbalanced")
        if c == ".": return ("any",)
        if c == "\\":
            if peek() is None: raise ValueError("trailing escape")
            return ("lit", eat())
        if c == "[":
            neg = False
            if peek() == "^": eat(); neg = True
            items = []; first = True
            while True:
                if peek() is None: raise ValueError("unclosed class")
                if peek() == "]" and not first: eat(); break
                first = False
                a = eat()
                if a == "\\":
                    if peek() is None: raise ValueError("escape")
                    a = eat()
                if peek() == "-" and pos + 1 < len(pattern) and pattern[pos + 1] != "]":
                    eat(); b = eat()
                    if b == "\\":
                        if peek() is None: raise ValueError("escape")
                        b = eat()
                    if b < a: raise ValueError("bad range")
                    items.append((a, b))
                else:
                    items.append((a, a))
            return ("cls", neg, items)
        return ("lit", c)
    tree = alt()
    if pos != len(pattern): raise ValueError("unbalanced")
    # compile to NFA
    states = []  # each: (kind, data, out1, out2)
    def new(kind, data=None):
        states.append([kind, data, None, None]); return len(states) - 1
    def comp(node):
        """returns (start, list of (state, slot) dangling)"""
        k = node[0]
        if k in ("lit", "any", "cls"):
            s_ = new("char", node); return s_, [(s_, 2)]
        if k == "cat":
            if not node[1]:
                s_ = new("eps"); return s_, [(s_, 2)]
            start, outs = comp(node[1][0])
            for n in node[1][1:]:
                st, o2 = comp(n)
                for (x, slot) in outs: states[x][slot] = st
                outs = o2
            return start, outs
        if k == "alt":
            s_ = new("split"); outs = []
            a, oa = comp(node[1][0]); states[s_][2] = a
            if len(node[1]) == 2:
                b, ob = comp(node[1][1]); states[s_][3] = b; return s_, oa + ob
            b, ob = comp(("alt", node[1][1:])); states[s_][3] = b; return s_, oa + ob
        if k in ("*", "+", "?"):
            a, oa = comp(node[1]); sp = new("split"); states[sp][2] = a
            if k == "*":
                for (x, slot) in oa: states[x][slot] = sp
                return sp, [(sp, 3)]
            if k == "+":
                for (x, slot) in oa: states[x][slot] = sp
                return a, [(sp, 3)]
            return sp, oa + [(sp, 3)]
    start, outs = comp(tree)
    acc = new("match")
    for (x, slot) in outs: states[x][slot] = acc
    def closure(sset):
        stack = list(sset); seen = set(sset)
        while stack:
            x = stack.pop(); k = states[x][0]
            nxt = []
            if k == "split": nxt = [states[x][2], states[x][3]]
            elif k == "eps": nxt = [states[x][2]]
            for y in nxt:
                if y is not None and y not in seen: seen.add(y); stack.append(y)
        return seen
    def ok(node, ch):
        if node[0] == "any": return True
        if node[0] == "lit": return node[1] == ch
        hit = any(a <= ch <= b for a, b in node[2])
        return hit != node[1]
    cur = closure({start})
    for ch in s:
        nxt = {states[x][2] for x in cur if states[x][0] == "char" and ok(states[x][1], ch)}
        cur = closure(nxt)
        if not cur: return False
    return acc in cur

# 2 sudoku
def sudoku(grid):
    g = [0 if c == "." else int(c) for c in grid]
    rows = [set() for _ in range(9)]; cols = [set() for _ in range(9)]; boxes = [set() for _ in range(9)]
    for i, v in enumerate(g):
        if v:
            r, c, b = i // 9, i % 9, (i // 27) * 3 + (i % 9) // 3
            if v in rows[r] or v in cols[c] or v in boxes[b]: return None
            rows[r].add(v); cols[c].add(v); boxes[b].add(v)
    def solve():
        best = None; bc = None
        for i in range(81):
            if not g[i]:
                r, c, b = i // 9, i % 9, (i // 27) * 3 + (i % 9) // 3
                cand = [v for v in range(1, 10) if v not in rows[r] and v not in cols[c] and v not in boxes[b]]
                if best is None or len(cand) < len(bc):
                    best, bc = i, cand
                    if not cand: return False
        if best is None: return True
        i = best; r, c, b = i // 9, i % 9, (i // 27) * 3 + (i % 9) // 3
        for v in bc:
            g[i] = v; rows[r].add(v); cols[c].add(v); boxes[b].add(v)
            if solve(): return True
            g[i] = 0; rows[r].discard(v); cols[c].discard(v); boxes[b].discard(v)
        return False
    return "".join(map(str, g)) if solve() else None

# 3 linear systems
def linsolve(A, b):
    m = len(A); n = len(A[0]) if m else 0
    M = [[Fraction(x) for x in row] + [Fraction(bb)] for row, bb in zip(A, b)]
    piv = []; r = 0
    for c in range(n):
        p = next((i for i in range(r, m) if M[i][c] != 0), None)
        if p is None: continue
        M[r], M[p] = M[p], M[r]
        inv = M[r][c]; M[r] = [x / inv for x in M[r]]
        for i in range(m):
            if i != r and M[i][c] != 0:
                f = M[i][c]; M[i] = [x - f * y for x, y in zip(M[i], M[r])]
        piv.append(c); r += 1
    for i in range(r, m):
        if M[i][n] != 0: return ("none", None)
    x = [Fraction(0)] * n
    for i, c in enumerate(piv): x[c] = M[i][n]
    return ("unique" if len(piv) == n else "infinite", x)

# 4 utf-8
def utf8_decode(b):
    return bytes(b).decode("utf-8", "replace")

# 5 csv
def parse_csv(text):
    rows = []; row = []; field = []; i = 0; n = len(text)
    if text == "": return []
    state = "start"  # start of field
    while i < n:
        c = text[i]
        if state == "start":
            if c == '"': state = "quoted"; i += 1; continue
            state = "plain"
            continue
        if state == "plain":
            if c == ",": row.append("".join(field)); field = []; state = "start"; i += 1; continue
            if c == "\n" or (c == "\r" and i + 1 < n and text[i + 1] == "\n"):
                row.append("".join(field)); rows.append(row); row = []; field = []; state = "start"
                i += 2 if c == "\r" else 1
                if i == n: return rows
                continue
            field.append(c); i += 1; continue
        if state == "quoted":
            if c == '"':
                if i + 1 < n and text[i + 1] == '"': field.append('"'); i += 2; continue
                state = "after"; i += 1; continue
            field.append(c); i += 1; continue
        if state == "after":
            if c == ",": row.append("".join(field)); field = []; state = "start"; i += 1; continue
            if c == "\n" or (c == "\r" and i + 1 < n and text[i + 1] == "\n"):
                row.append("".join(field)); rows.append(row); row = []; field = []; state = "start"
                i += 2 if c == "\r" else 1
                if i == n: return rows
                continue
            raise ValueError("text after closing quote")
    if state == "quoted": raise ValueError("unterminated quote")
    row.append("".join(field)); rows.append(row)
    return rows

# 6 lattice paths
MOD = 1_000_000_007
def lattice_paths(h, w, blocked):
    N = h + w + 1
    f = [1] * (N + 1)
    for i in range(1, N + 1): f[i] = f[i - 1] * i % MOD
    inv = [1] * (N + 1); inv[N] = pow(f[N], MOD - 2, MOD)
    for i in range(N, 0, -1): inv[i - 1] = inv[i] * i % MOD
    def C(a, b): return f[a + b] * inv[a] % MOD * inv[b] % MOD
    bl = set(p for p in blocked if 0 <= p[0] <= h and 0 <= p[1] <= w)
    if (0, 0) in bl or (h, w) in bl: return 0
    pts = sorted(bl) + [(h, w)]
    dp = []
    for i, (r, c) in enumerate(pts):
        v = C(r, c)
        for j in range(i):
            pr, pc = pts[j]
            if pr <= r and pc <= c: v -= dp[j] * C(r - pr, c - pc)
        dp.append(v % MOD)
    return dp[-1]

# 7 max flow (Dinic)
def max_flow(n, edges, s, t):
    if s == t: raise ValueError("s == t")
    g = [[] for _ in range(n)]
    for u, v, c in edges:
        g[u].append([v, c, len(g[v])]); g[v].append([u, 0, len(g[u]) - 1])
    flow = 0
    while True:
        lvl = [-1] * n; lvl[s] = 0; q = deque([s])
        while q:
            u = q.popleft()
            for v, c, _ in g[u]:
                if c > 0 and lvl[v] < 0: lvl[v] = lvl[u] + 1; q.append(v)
        if lvl[t] < 0: return flow
        it = [0] * n
        def dfs(u, f):
            if u == t: return f
            while it[u] < len(g[u]):
                e = g[u][it[u]]; v, c, r = e
                if c > 0 and lvl[v] == lvl[u] + 1:
                    d = dfs(v, min(f, c))
                    if d: e[1] -= d; g[v][r][1] += d; return d
                it[u] += 1
            return 0
        import sys; sys.setrecursionlimit(10000)
        while True:
            f = dfs(s, float("inf"))
            if not f: break
            flow += f

# 8 best rational approximation
def best_approx(x, N):
    x = Fraction(x)
    best = None
    def consider(p, q):
        nonlocal best
        if q < 1 or q > N: return
        fr = Fraction(p, q); d = abs(fr - x)
        key = (d, fr.denominator, fr.numerator)
        if best is None or key < best[0]: best = (key, fr)
    # Stern-Brocot / continued fraction convergents and semiconvergents
    a = x.numerator; b = x.denominator
    p0, q0, p1, q1 = 0, 1, 1, 0
    num, den = a, b
    while True:
        if den == 0: break
        t = num // den
        p2, q2 = p0 + t * p1, q0 + t * q1
        if q2 > N:
            k = (N - q0) // q1 if q1 else 0
            for kk in (k, k - 1):
                if kk >= 0: consider(p0 + kk * p1, q0 + kk * q1)
            consider(p1, q1)
            break
        consider(p2, q2)
        p0, q0, p1, q1 = p1, q1, p2, q2
        num, den = den, num - t * den
    # neighbours by brute force around floor for safety on small N
    return best[1]

def best_approx_brute(x, N):
    x = Fraction(x); best = None
    for q in range(1, N + 1):
        p = (x * q).numerator // (x * q).denominator
        for pp in (p - 1, p, p + 1, p + 2):
            fr = Fraction(pp, q); key = (abs(fr - x), fr.denominator, fr.numerator)
            if best is None or key < best[0]: best = (key, fr)
    return best[1]

# 9 edit distance
def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[-1]

# 10 easter
def easter(y):
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31; day = (h + l - 7 * m + 114) % 31 + 1
    return datetime.date(y, month, day)

# 11 durations
_DUR = re.compile(r"(-)?P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
def add_duration(dt, s):
    m = _DUR.fullmatch(s)
    if not m or all(g is None for g in m.groups()[1:]) or s.endswith("T"): raise ValueError(s)
    sign = -1 if m.group(1) else 1
    Y, Mo, W, D, H, Mi, S = (int(g) * sign if g else 0 for g in m.groups()[1:])
    total = dt.year * 12 + (dt.month - 1) + Y * 12 + Mo
    y, mo = divmod(total, 12); mo += 1
    if not 1 <= y <= 9999: raise ValueError("out of range")
    day = min(dt.day, calendar.monthrange(y, mo)[1])
    out = dt.replace(year=y, month=mo, day=day)
    try:
        return out + datetime.timedelta(weeks=W, days=D, hours=H, minutes=Mi, seconds=S)
    except OverflowError:
        raise ValueError("out of range")

# 12 min window
def min_window(s, t):
    if not t: return ""
    need = Counter(t); missing = len(t); best = None; left = 0
    for right, ch in enumerate(s):
        if need[ch] > 0: missing -= 1
        need[ch] -= 1
        if missing == 0:
            while need[s[left]] < 0: need[s[left]] += 1; left += 1
            if best is None or right - left + 1 < best[1] - best[0]: best = (left, right + 1)
            need[s[left]] += 1; missing += 1; left += 1
    return s[best[0]:best[1]] if best else ""
