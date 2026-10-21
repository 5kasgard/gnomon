import sys, random, datetime, importlib.util, time, signal, json, os, re
from fractions import Fraction
import ref2
HERE = os.path.dirname(os.path.abspath(__file__))
sys.setrecursionlimit(100000)

def load(path):
    spec = importlib.util.spec_from_file_location("sol", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def raises(f, *a):
    try: f(*a)
    except ValueError: return True
    except Exception: return False
    return False

def timed(limit, f, *a):
    t = time.time(); r = f(*a); assert time.time() - t < limit, f"too slow ({time.time() - t:.1f}s)"; return r

def t_regex(m, R):
    ex = [("a(b|c)*d", "abccbd", True), ("a|", "", True), ("a|", "a", True), ("()", "", True), ("[]a]", "]", True),
          ("[^]a]", "b", True), ("[a-]", "-", True), ("[-a]", "-", True), ("a**", "aaa", True), ("a+?", "", True),
          ("\\.", "a", False), ("\\.", ".", True), ("[\\]]", "]", True), ("\\*", "*", True), ("[^a-c]x", "dx", True),
          ("a.c", "a\nc", True), ("x*", "", True), ("(ab)+", "abab", True), ("(ab)+", "aba", False)]
    for p, s, want in ex:
        assert m.regex_match(p, s) == want, (p, s)
    for p in ["*a", "(", "a)", "[a", "a\\", "[b-a]", "a|*", "(*)", "+", "a(?)"]:
        assert raises(m.regex_match, p, "a"), p
    for p, s in [("(a|aa)*b", "a" * 3000), ("(a*)*b", "a" * 3000), ("(x+x+)+y", "x" * 3000), ("(a?){30}a{30}".replace("{30}", ""), "a" * 30)]:
        assert timed(2.0, m.regex_match, p, s) == ref2.regex_match(p, s), p
    def gen(d):
        r = R.random()
        if d == 0 or r < 0.3: return R.choice(["a", "b", ".", "[ab]", "[^a]", "\\.", "[a-b]", "[]a]", "\\(", ""])
        if r < 0.5: return gen(d - 1) + gen(d - 1)
        if r < 0.65: return gen(d - 1) + "|" + gen(d - 1)
        if r < 0.8: return "(" + gen(d - 1) + ")"
        return "(" + gen(d - 1) + ")" + R.choice(["*", "+", "?", "*?", "+*"])
    for _ in range(3000):
        p = gen(4); s = "".join(R.choice("ab.c(]") for _ in range(R.randint(0, 7)))
        assert m.regex_match(p, s) == ref2.regex_match(p, s), (p, s)

def t_sudoku(m, R):
    puzzles = json.load(open(os.path.join(HERE, "puzzles.json")))
    puzzles.append("1....7.9..3..2...8..96..5....53..9...1..8...26....4...3......1..4......7..7...3..")
    for p in puzzles:
        got = timed(3.0, m.sudoku, p); want = ref2.sudoku(p)
        assert got == want, p
    assert m.sudoku("11" + "." * 79) is None
    # No solution, though no two givens conflict: a wrong digit in an empty cell.
    p = puzzles[0]; sol = ref2.sudoku(p)
    for i, ch in enumerate(p):
        if ch != ".": continue
        for d in "123456789":
            if d == sol[i]: continue
            q = p[:i] + d + p[i + 1:]
            r, c = i // 9, i % 9
            peers = [q[r * 9 + k] for k in range(9)] + [q[k * 9 + c] for k in range(9)]
            if peers.count(d) == 2:  # only itself, in its row and its column
                assert ref2.sudoku(q) is None
                assert timed(3.0, m.sudoku, q) is None, q
                return

def t_linsolve(m, R):
    def norm(r):
        k, x = r
        return (k, None if x is None else [Fraction(v) for v in x])
    for _ in range(600):
        mr, nc = R.randint(1, 4), R.randint(1, 4)
        A = [[R.randint(-3, 3) for _ in range(nc)] for _ in range(mr)]
        if R.random() < 0.3 and mr > 1: A[-1] = [2 * v for v in A[0]]
        b = [R.randint(-5, 5) for _ in range(mr)]
        if R.random() < 0.3 and mr > 1: b[-1] = 2 * b[0]
        assert norm(m.linsolve(A, b)) == ref2.linsolve(A, b), (A, b)

def t_utf8(m, R):
    cases = [b"", b"abc", "héllo €𝄞".encode(), b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2\x82", b"\xf0\x9f\x98",
             b"\x80\xbf", b"\xff\xfe", b"\xe0\x80\xaf", b"\xf0\x80\x80\x80", b"a\xc3", b"\xc3\x28", b"\xe1\x80\xe2\x82\xac"]
    for b in cases:
        assert m.utf8_decode(b) == ref2.utf8_decode(b), b
    pool = [bytes([x]) for x in range(256)] + [c.encode() for c in "é€𝄞ü中"]
    for _ in range(20000):
        b = b"".join(R.choice(pool) if R.random() < 0.5 else bytes([R.choice([0x80, 0xbf, 0xc2, 0xdf, 0xe0, 0xed, 0xef, 0xf0, 0xf4, 0xf5, 0xa0, 0x9f, 0x8f, 0x90])]) for _ in range(R.randint(0, 8)))
        assert m.utf8_decode(b) == ref2.utf8_decode(b), b

def t_csv(m, R):
    ex = {"": [], "a": [["a"]], "a\n": [["a"]], "\n": [[""]], "a,b\r\nc": [["a", "b"], ["c"]], '"a,b",c': [["a,b", "c"]],
          '"a""b"': [['a"b']], 'a"b': [['a"b']], "a\rb": [["a\rb"]], '"x\ny"\n': [["x\ny"]], ",": [["", ""]],
          "a\n\nb": [["a"], [""], ["b"]], '""': [[""]], '"a"\r\n': [["a"]]}
    for t, want in ex.items():
        assert m.parse_csv(t) == want, repr(t)
    for bad in ['"a', '"a"b', '"a" ,b', 'x,"y']:
        assert raises(m.parse_csv, bad), repr(bad)
    for _ in range(4000):
        t = "".join(R.choice(['a', 'b', ',', '"', '""', '\n', '\r\n', '\r', ' ']) for _ in range(R.randint(0, 10)))
        try: want = ref2.parse_csv(t)
        except ValueError: assert raises(m.parse_csv, t), repr(t); continue
        assert m.parse_csv(t) == want, repr(t)

def t_lattice(m, R):
    def brute(h, w, bl):
        bl = set(bl); dp = [[0] * (w + 1) for _ in range(h + 1)]
        for r in range(h + 1):
            for c in range(w + 1):
                if (r, c) in bl: continue
                dp[r][c] = 1 if r == c == 0 else ((dp[r - 1][c] if r else 0) + (dp[r][c - 1] if c else 0)) % ref2.MOD
        return dp[h][w]
    for _ in range(300):
        h, w = R.randint(0, 8), R.randint(0, 8)
        bl = [(R.randint(-1, 9), R.randint(-1, 9)) for _ in range(R.randint(0, 6))]
        assert m.lattice_paths(h, w, list(bl)) == brute(h, w, bl), (h, w, bl)
    h, w = 200000, 199999
    bl = [(R.randint(0, h), R.randint(0, w)) for _ in range(2000)]
    bl = [p for p in bl if p != (0, 0) and p != (h, w)]
    assert timed(10.0, m.lattice_paths, h, w, list(bl)) == ref2.lattice_paths(h, w, bl)

def t_maxflow(m, R):
    assert raises(m.max_flow, 2, [(0, 1, 1)], 0, 0)
    for _ in range(300):
        n = R.randint(2, 7); edges = [(R.randrange(n), R.randrange(n), R.randint(0, 9)) for _ in range(R.randint(0, 15))]
        s, t = R.sample(range(n), 2)
        assert m.max_flow(n, list(edges), s, t) == ref2.max_flow(n, edges, s, t), (n, edges, s, t)
    n = 500; edges = [(R.randrange(n), R.randrange(n), R.randint(1, 10**6)) for _ in range(5000)]
    assert timed(10.0, m.max_flow, n, list(edges), 0, n - 1) == ref2.max_flow(n, edges, 0, n - 1)

def t_approx(m, R):
    for _ in range(1500):
        x = Fraction(R.randint(-200, 200), R.randint(1, 60)); N = R.randint(1, 40)
        assert Fraction(m.best_approx(x, N)) == ref2.best_approx_brute(x, N), (x, N)
    for _ in range(300):
        x = Fraction(R.randint(-10**30, 10**30), R.randint(1, 10**30)); N = R.randint(1, 10**18)
        got = timed(1.0, m.best_approx, x, N)
        assert Fraction(got) == x.limit_denominator(N), (x, N)

def t_edit(m, R):
    def apply(a, ops):
        out = []; i = 0
        for op in ops:
            if op[0] == "keep": assert a[i] == op[1]; out.append(op[1]); i += 1
            elif op[0] == "sub": assert a[i] == op[1] and op[1] != op[2]; out.append(op[2]); i += 1
            elif op[0] == "del": assert a[i] == op[1]; i += 1
            elif op[0] == "ins": out.append(op[1])
            else: raise AssertionError(op)
        assert i == len(a); return "".join(out)
    for _ in range(800):
        a = "".join(R.choice("abc") for _ in range(R.randint(0, 9))); b = "".join(R.choice("abc") for _ in range(R.randint(0, 9)))
        ops = [tuple(o) for o in m.edit_script(a, b)]
        assert apply(a, ops) == b, (a, b)
        assert sum(o[0] != "keep" for o in ops) == ref2.edit_distance(a, b), (a, b)
    a = "".join(R.choice("acgt") for _ in range(2000)); b = "".join(R.choice("acgt") for _ in range(2000))
    ops = [tuple(o) for o in timed(20.0, m.edit_script, a, b)]
    assert apply(a, ops) == b and sum(o[0] != "keep" for o in ops) == ref2.edit_distance(a, b)

def t_easter(m, R):
    for y in range(1583, 10000):
        assert m.easter(y) == ref2.easter(y), y

def t_duration(m, R):
    D = datetime.datetime
    ex = [(D(2024, 1, 31), "P1M", D(2024, 2, 29)), (D(2024, 3, 31), "-P1M", D(2024, 2, 29)), (D(2023, 1, 31), "P1M", D(2023, 2, 28)),
          (D(2024, 2, 29), "P1Y", D(2025, 2, 28)), (D(2024, 1, 1), "PT36H", D(2024, 1, 2, 12)), (D(2024, 1, 31), "P1M1D", D(2024, 3, 1)),
          (D(2024, 1, 1), "P2W", D(2024, 1, 15)), (D(2024, 1, 1), "-PT1S", D(2023, 12, 31, 23, 59, 59))]
    for dt, s, want in ex:
        assert m.add_duration(dt, s) == want, (dt, s)
    for bad in ["P", "PT", "P1DT", "1D", "P1H", "P1D1Y", "P-1D", "PT1D", "+P1D", "P1.5D", "P1MT", "p1d", " P1D", "P1D "]:
        assert raises(m.add_duration, D(2024, 1, 1), bad), bad
    assert raises(m.add_duration, D(9999, 12, 1), "P1M")
    assert raises(m.add_duration, D(1, 1, 1), "-PT1S")
    for _ in range(3000):
        dt = D(2000, 1, 1) + datetime.timedelta(days=R.randint(0, 20000), seconds=R.randint(0, 86399))
        parts = [(R.random() < 0.4, k) for k in "YMWD"]
        s = ("-" if R.random() < 0.3 else "") + "P" + "".join(f"{R.randint(0, 40)}{k}" for use, k in parts if use)
        tp = "".join(f"{R.randint(0, 100)}{k}" for k in "HMS" if R.random() < 0.4)
        if tp: s += "T" + tp
        try: want = ref2.add_duration(dt, s)
        except ValueError: assert raises(m.add_duration, dt, s), (dt, s); continue
        assert m.add_duration(dt, s) == want, (dt, s)

def t_window(m, R):
    for s, t, want in [("ADOBECODEBANC", "ABC", "BANC"), ("a", "aa", ""), ("aa", "aa", "aa"), ("abc", "", ""), ("", "a", "")]:
        assert m.min_window(s, t) == want, (s, t)
    for _ in range(2000):
        s = "".join(R.choice("abc") for _ in range(R.randint(0, 15))); t = "".join(R.choice("abc") for _ in range(R.randint(0, 4)))
        assert m.min_window(s, t) == ref2.min_window(s, t), (s, t)
    s = "".join(R.choice("abcdefghij") for _ in range(10**6)) + "XYZ"
    assert timed(5.0, m.min_window, s, "ZYXjj") == ref2.min_window(s, "ZYXjj")

TESTS = {"regex": t_regex, "sudoku": t_sudoku, "linsolve": t_linsolve, "utf8": t_utf8, "csv": t_csv, "lattice": t_lattice,
         "maxflow": t_maxflow, "approx": t_approx, "edit": t_edit, "easter": t_easter, "duration": t_duration, "window": t_window}

def grade(task, path):
    def boom(*a): raise TimeoutError("timeout")
    signal.signal(signal.SIGALRM, boom); signal.alarm(120)
    try:
        TESTS[task](load(path), random.Random(99)); return "PASS"
    except AssertionError as e: return f"FAIL: wrong on {str(e)[:200]}"
    except Exception as e: return f"FAIL: {type(e).__name__}: {str(e)[:200]}"
    finally: signal.alarm(0)

if __name__ == "__main__":
    print(grade(sys.argv[1], sys.argv[2]))
