"""Grade a solution file against hidden tests: python3 grade.py <task> <file>.
Prints PASS or FAIL: <reason>. Randomised tests compare with ref.py."""
import sys, random, datetime, importlib.util, time, signal
from fractions import Fraction
import ref
sys.set_int_max_str_digits(0)

def load(path):
    spec = importlib.util.spec_from_file_location("sol", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def raises(f, *a):
    try: f(*a)
    except (ValueError, ZeroDivisionError): return True
    except Exception: return False
    return False

def t_slugify(m, R):
    for s in ["Hello, World!", "  --A__b--  ", "Café 2026", "!!!", "", "a", "ABC def-GHI", "x--y", "ÜBER über", "1.2.3"]:
        assert m.slugify(s) == ref.slugify(s), s
    for _ in range(300):
        s = "".join(R.choice("aZ09 -_!é.") for _ in range(R.randint(0, 20)))
        assert m.slugify(s) == ref.slugify(s), s

def t_roman(m, R):
    for n in list(range(1, 4000)):
        assert m.roman_to_int(ref._to_roman(n)) == n, n
    for bad in ["IIII", "VX", "IC", "MMMM", "", "iv", "XM", "VV", "IIV", "CMC", "IIX", "LL", "DD", "XXXX", "IXI", "XCX", "A", " I"]:
        assert raises(m.roman_to_int, bad), bad
    for _ in range(3000):
        s = "".join(R.choice("IVXLCDM") for _ in range(R.randint(1, 6)))
        if s in ref._R: assert m.roman_to_int(s) == ref._R[s], s
        else: assert raises(m.roman_to_int, s), s

def t_intervals(m, R):
    assert m.merge_intervals([]) == []
    assert m.merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]
    assert m.merge_intervals([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
    for _ in range(500):
        xs = []
        for _ in range(R.randint(0, 8)):
            a = R.randint(-10, 10); xs.append((a, a + R.randint(0, 5)))
        assert [tuple(x) for x in m.merge_intervals(list(xs))] == ref.merge_intervals(xs), xs

def t_lru(m, R):
    for _ in range(300):
        cap = R.randint(0, 4); a, b = m.LRUCache(cap), ref.LRUCache(cap)
        for _ in range(40):
            k = R.randint(0, 6)
            if R.random() < 0.5: assert a.get(k) == b.get(k)
            else:
                v = R.randint(0, 100); a.put(k, v); b.put(k, v)

def t_expr(m, R):
    cases = {"1+2*3": 7, "2**3**2": 512, "-2**2": -4, "2**-1": Fraction(1, 2), "(1+2)*3": 9, "7/2": Fraction(7, 2),
             " - ( 3 ) ": -3, "+4": 4, "2*-3": -6, "--2": 2, "(2**2)**3": 64, "1-2-3": -4, "8/2/2": 2, "0**0": 1,
             "4**(1/1)": 4, "2**(3-5)": Fraction(1, 4), "-(2)**2": -4}
    for s, v in cases.items():
        assert m.evaluate(s) == v, s
    for bad in ["", "1+", "(1", "1)", "2**(1/2)", "1/0", "1/(2-2)", "a", "1 2", "**2", "3//2", "1+*2", "()"]:
        assert raises(m.evaluate, bad), bad
    for _ in range(400):
        def gen(d):
            if d == 0 or R.random() < 0.3: return str(R.randint(0, 9))
            op = R.choice(["+", "-", "*", "/", "**", "u"])
            if op == "u": return R.choice(["-", "+"]) + gen(d - 1)
            if op == "**": return "(" + gen(d - 1) + ")**" + R.choice(["2", "3", "-1", "0", "(1+1)"])
            return "(" + gen(d - 1) + op + gen(d - 1) + ")" if R.random() < 0.5 else gen(d - 1) + op + gen(d - 1)
        s = gen(4)
        try: want = ref.evaluate(s)
        except ValueError: assert raises(m.evaluate, s), s; continue
        assert m.evaluate(s) == want, s

def t_glob(m, R):
    ex = [("src/**/*.py", "src/a.py", True), ("src/**/*.py", "src/x/y/a.py", True), ("src/*.py", "src/x/a.py", False),
          ("**", "", True), ("a/**", "a", True), ("a?c", "a/c", False), ("**/b", "b", True), ("a/**/b", "a/b", True),
          ("*", "a/b", False), ("a/*/c", "a//c", True), ("**/**", "x/y", True), ("a*b*c", "abxbc", True)]
    for p, x, want in ex:
        assert m.glob_match(p, x) == want, (p, x)
    segs = ["a", "b", "ab", "", "*", "?", "**", "a*", "*b", "?b"]
    for _ in range(3000):
        p = "/".join(R.choice(segs) for _ in range(R.randint(1, 4)))
        x = "/".join(R.choice(["a", "b", "ab", "ba", "", "abb"]) for _ in range(R.randint(1, 4)))
        assert m.glob_match(p, x) == ref.glob_match(p, x), (p, x)

def t_topo(m, R):
    assert m.topo_sort({}) == []
    assert m.topo_sort({"b": ["a"], "c": ["a"]}) == ["a", "b", "c"]
    assert raises(m.topo_sort, {"a": ["b"], "b": ["a"]})
    assert raises(m.topo_sort, {"a": ["a"]})
    for _ in range(500):
        names = [chr(97 + i) for i in range(R.randint(1, 8))]
        deps = {n: [x for x in names if R.random() < 0.25] for n in names if R.random() < 0.8}
        try: want = ref.topo_sort(deps)
        except ValueError: assert raises(m.topo_sort, deps), deps; continue
        assert m.topo_sort(deps) == want, deps

def t_busdays(m, R):
    D = datetime.date
    for _ in range(2000):
        a = D(2020, 1, 1) + datetime.timedelta(R.randint(0, 800)); b = a + datetime.timedelta(R.randint(-60, 60))
        hol = {D(2020, 1, 1) + datetime.timedelta(R.randint(0, 900)) for _ in range(R.randint(0, 5))}
        assert m.business_days(a, b, hol) == ref.business_days(a, b, hol), (a, b, hol)
    t = time.time()
    a, b = D(1, 1, 1), D(9999, 12, 31)
    assert m.business_days(a, b, {D(2026, 10, 21)}) == ref.business_days(a, b, {D(2026, 10, 21)})
    assert time.time() - t < 2.0, "too slow"

def t_canon(m, R):
    v = {"€": "Euro", "\r": "CR", "דּ": "Hebrew", "1": "One", "\U0001F600": "Emoji", "\u0080": "Ctl", "ö": "o"}
    assert m.canonical_json(v) == ref.canonical_json(v)
    assert m.canonical_json(["a/b", "\x00\x1f\x7f", True, False, None, -0, 10**30]) == ref.canonical_json(["a/b", "\x00\x1f\x7f", True, False, None, 0, 10**30])
    alphabet = ["a", "Z", "\"", "\\", "\n", "\x01", "é", "€", "\U0001F600", "דּ", "/", " ", "\x7f", " "]
    def gen(d):
        r = R.random()
        if d == 0 or r < 0.3: return R.choice([None, True, False, R.randint(-1000, 1000), "".join(R.choice(alphabet) for _ in range(R.randint(0, 4)))])
        if r < 0.6: return [gen(d - 1) for _ in range(R.randint(0, 3))]
        return {"".join(R.choice(alphabet) for _ in range(R.randint(0, 3))): gen(d - 1) for _ in range(R.randint(0, 4))}
    for _ in range(1000):
        v = gen(3); assert m.canonical_json(v) == ref.canonical_json(v), repr(v)

def t_bigmul(m, R):
    for a, b in [("0", "0"), ("-1", "0"), ("0", "-5"), ("-3", "-4"), ("12", "-12"), ("999", "999")]:
        assert m.multiply(a, b) == ref.multiply(a, b), (a, b)
    for _ in range(300):
        def num():
            n = str(R.randint(0, 10 ** R.randint(0, 60)))
            return ("-" + n) if n != "0" and R.random() < 0.4 else n
        a, b = num(), num(); assert m.multiply(a, b) == ref.multiply(a, b), (a, b)
    a, b = "7" * 3000, "-" + "9" * 3000
    t = time.time(); assert m.multiply(a, b) == ref.multiply(a, b); assert time.time() - t < 10, "too slow"

def t_dijkstra(m, R):
    g = {"a": [("b", 1), ("c", 1)], "b": [("d", 1)], "c": [("d", 1)]}
    assert m.shortest_path(g, "a", "d") == (2, ["a", "b", "d"])
    assert m.shortest_path(g, "a", "a") == (0, ["a"])
    assert m.shortest_path(g, "d", "a") is None
    assert m.shortest_path({}, "x", "x") == (0, ["x"])
    for _ in range(800):
        names = [chr(97 + i) for i in range(R.randint(1, 7))]
        g = {n: [(x, R.randint(1, 4)) for x in names if x != n and R.random() < 0.4] for n in names}
        s, t = R.choice(names), R.choice(names)
        got = m.shortest_path(g, s, t); want = ref.shortest_path(g, s, t)
        assert (None if got is None else (got[0], list(got[1]))) == want, (g, s, t)

def t_wrap(m, R):
    assert m.wrap("", 5) == [] and m.wrap("  \n \n", 5) == []
    assert m.wrap("a b c", 3) == ["a b", "c"]
    assert m.wrap("abcdefg h", 3) == ["abc", "def", "g", "h"]
    assert m.wrap("x\n\n\ny", 10) == ["x", "", "y"]
    for _ in range(1000):
        words = ["a", "bb", "ccc", "dddddddd", "\n", "\n\n", " \n \n", "  ", "\t"]
        s = " ".join(R.choice(words) for _ in range(R.randint(0, 12)))
        w = R.randint(1, 9); assert m.wrap(s, w) == ref.wrap(s, w), (s, w)

TESTS = {"slugify": t_slugify, "roman": t_roman, "intervals": t_intervals, "lru": t_lru, "expr": t_expr,
         "glob": t_glob, "topo": t_topo, "busdays": t_busdays, "canon": t_canon, "bigmul": t_bigmul,
         "dijkstra": t_dijkstra, "wrap": t_wrap}

def grade(task, path):
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError("timeout")))
    signal.alarm(60)
    try:
        TESTS[task](load(path), random.Random(1234))
        return "PASS"
    except AssertionError as e:
        return f"FAIL: wrong on {str(e)[:150]}"
    except Exception as e:
        return f"FAIL: {type(e).__name__}: {str(e)[:150]}"
    finally:
        signal.alarm(0)

if __name__ == "__main__":
    print(grade(sys.argv[1], sys.argv[2]))
