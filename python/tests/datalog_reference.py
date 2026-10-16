"""A deliberately naive Datalog evaluator, as an oracle for the Rust engine.

No indexes, no semi-naive deltas, no cleverness: every rule is re-run over
every fact until nothing changes, stratum by stratum. Slow, but simple
enough to trust by reading. It shares only the parser with the package
(gnomon.proofs), so the evaluation itself is independent of the core.
"""

from __future__ import annotations

from gnomon.proofs import WILD, Const, Var, ascii_lower, parse, query_rule


def _same(a, b):
    return isinstance(a, int) == isinstance(b, int)


def _holds(op, a, b):
    if op == "=":
        return _same(a, b) and a == b
    if op == "!=":
        return not (_same(a, b) and a == b)
    if not _same(a, b):
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _val(t, env):
    if isinstance(t, Const):
        return t.value
    return env.get(t.name)


def _match(args, row, env):
    env = dict(env)
    for t, v in zip(args, row):
        if t == WILD:
            continue
        if isinstance(t, Const):
            if not (_same(t.value, v) and t.value == v):
                return None
        elif t.name in env:
            if not (_same(env[t.name], v) and env[t.name] == v):
                return None
        else:
            env[t.name] = v
    return env


def _solve(body, i, env, facts):
    if i == len(body):
        yield env
        return
    lit = body[i]
    kind = lit[0]
    if kind == "pos":
        for row in list(facts.get(lit[1].pred, ())):
            if len(row) == len(lit[1].args):
                e = _match(lit[1].args, row, env)
                if e is not None:
                    yield from _solve(body, i + 1, e, facts)
    elif kind == "neg":
        if not any(_match(lit[1].args, row, env) is not None for row in facts.get(lit[1].pred, ())):
            yield from _solve(body, i + 1, env, facts)
    elif kind == "cmp":
        op, a, b = lit[1], lit[2], lit[3]
        va = None if a == WILD else _val(a, env)
        vb = None if b == WILD else _val(b, env)
        if va is None and op == "=":
            yield from _solve(body, i + 1, {**env, a.name: vb}, facts)
        elif vb is None and op == "=":
            yield from _solve(body, i + 1, {**env, b.name: va}, facts)
        elif _holds(op, va, vb):
            yield from _solve(body, i + 1, env, facts)
    else:
        a, b = _val(lit[1], env), _val(lit[2], env)
        if isinstance(a, str) and isinstance(b, str) and ascii_lower(b) in ascii_lower(a):
            yield from _solve(body, i + 1, env, facts)


def answers(src: str) -> set:
    """The set of answer tuples for a self-contained program."""
    rules, query = parse(src)
    rules = rules + [query_rule(query)]
    heads = {r.head.pred for r in rules}
    # Strata: a relation sits above everything it negates.
    level = {p: 0 for p in heads}
    for _ in range(len(heads) + 2):
        for r in rules:
            for lit in r.body:
                if lit[0] in ("pos", "neg") and lit[1].pred in heads:
                    need = level[lit[1].pred] + (1 if lit[0] == "neg" else 0)
                    level[r.head.pred] = max(level[r.head.pred], need)
    if any(v > len(heads) for v in level.values()):
        raise ValueError("not stratifiable")
    facts: dict[str, set] = {}
    for s in sorted(set(level.values())):
        stratum = [r for r in rules if level[r.head.pred] == s]
        changed = True
        while changed:
            changed = False
            for r in stratum:
                for env in list(_solve(r.body, 0, {}, facts)):
                    row = tuple(_val(t, env) for t in r.head.args)
                    if row not in facts.setdefault(r.head.pred, set()):
                        facts[r.head.pred].add(row)
                        changed = True
    return facts.get("?", set())
