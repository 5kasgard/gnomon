#!/usr/bin/env python3
"""Exact property test for Memory.state(as_of=t).

Property: for a valid log (every event accepted by the gate) and every t among
the log's record times (plus one time before the first and one after the last),
Memory.state(as_of=t) must not raise, and its ["state"] must equal the state of
a fresh log built by appending, in the original log order, exactly the events
whose record_time <= t (same writer, kind, body, record_time). Also compared
with compartments=[c] for each compartment. If that rebuild is itself refused
by the gate, that is a failure too (an event depends on a later-dated one).

Usage: python3 asof_prop.py --gnomon <python dir> --logs 2000 --seed 1
       [--one LOGSEED]  (re-run a single log, printing its diff paths)
"""
import argparse
import copy
import json
import multiprocessing as mp
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# Fields removed from the comparison: NONE. Inspected: the outer result has count/digest/head/ok (outside
# ["state"], so not compared; digest/head/count describe the view's position) and ["state"] holds appeals,
# appointments, claims, episodes, handoffs, positions, schema_version, supersessions, writers, with no field that
# records the as_of time or a view position (a diff on main and on the other build confirmed none). If one
# appears, add its path here with the reason.
STRIP = {}  # path-tuple -> reason (documented below)

EMPTY = {"appeals": {}, "appointments": {}, "claims": {}, "episodes": {}, "handoffs": {}, "positions": {},
         "schema_version": 1, "supersessions": {}, "writers": {}}
BASE = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
NOW = datetime.now(timezone.utc).replace(microsecond=0)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ts(dt):
    return dt.strftime(FMT)


def parse(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ generator
STATS = {"accepted": 0, "ooo": 0, "refused_n": 0, "refused": {}, "kinds": {}}


def generate(g, path, rng):
    """Build a random valid log at `path`; return the list of accepted events
    (as read back from the log file, in order)."""
    Memory, GateError = g.Memory, g.GateError
    m = Memory(path)
    comps_all = list("abcd")[: rng.randint(2, 4)]
    times = []

    def pick_time():
        # The gate refuses record times more than 300 s ahead of the wall clock, so stay in the past.
        if not times:
            return BASE
        if rng.random() < 0.75:
            ref = parse(rng.choice(times))
            t = ref + timedelta(seconds=rng.randint(-1800, 1800))
        else:
            t = parse(max(times)) + timedelta(seconds=rng.randint(1, 1800))
        return min(t, NOW - timedelta(minutes=1))

    owner = "own"
    writers = {}  # id -> dict(kind, caps, comps)
    claims = []   # dict(id, comp, writer)
    verdicts = []  # dict(id, claim, result)
    judged = set()
    leases = []   # dict(id, frame, granter, target)
    frames = ["f0", "f1", "f2"]
    counter = [0]

    def uniq(prefix):
        counter[0] += 1
        return f"{prefix}{counter[0]}"

    def attempt(fn, *a, **k):
        rt = ts(pick_time())
        try:
            r = fn(*a, record_time=rt, **k)
        except GateError as ex:
            STATS["refused"][ex.code] = STATS["refused"].get(ex.code, 0) + 1
            STATS["refused_n"] += 1
            return None
        if times and rt < max(times):
            STATS["ooo"] += 1
        STATS["accepted"] += 1
        times.append(rt)
        return r if r is not None else True

    # genesis
    rt = ts(pick_time())
    m.register(owner, owner, party=owner, kind="human",
               capabilities=["owner", "write", "verify", "govern", "schedule"],
               compartments=comps_all, name="Owner", record_time=rt)
    times.append(rt)
    writers[owner] = dict(kind="human", caps=["owner", "write", "verify", "govern", "schedule"],
                          comps=list(comps_all), party=owner)
    humans = [owner]

    def new_writer():
        wid = uniq("w")
        kind = rng.choice(["human", "model", "model", "tool"] if rng.random() < 0.9 else ["human"])
        kind = "human" if kind == "tool" and rng.random() < 0.5 else kind
        caps = rng.sample(["verify", "schedule", "govern"], rng.randint(0, 2))
        if rng.random() < 0.92:
            caps.append("write")
        if not caps:
            caps = ["verify"]
        if kind != "human" and rng.random() < 0.2:
            caps.append("spawn")
        comps = rng.sample(comps_all, rng.randint(1, len(comps_all)))
        kw = {}
        if kind != "human":
            kw["operator"] = rng.choice(humans)
            if kind == "model":
                kw["model"] = rng.choice(["m-alpha", "m-beta", "m-gamma"])
        party = uniq("p")
        r = attempt(m.register, owner, wid, party=party, kind=kind, capabilities=caps,
                    compartments=comps, name=wid.upper(), **kw)
        if r:
            writers[wid] = dict(kind=kind, caps=caps, comps=comps, party=party)
            if kind == "human":
                humans.append(wid)
            return wid

    for _ in range(rng.randint(3, 6)):
        new_writer()

    def vis_claims(w):
        cs = writers[w]["comps"]
        return [c for c in claims if c["comp"] in cs]

    def op_claim():
        w = rng.choice(list(writers))
        comp = rng.choice(writers[w]["comps"] + ([rng.choice(comps_all)] if rng.random() < 0.1 else []))
        frame = rng.choice(frames)
        extra = {}
        live = [l for l in leases if l["frame"] == frame and l["exp"] > ts(NOW)]
        if live and rng.random() < 0.7:
            extra["lease"] = rng.choice(live)["id"]
        body = g.claim(uniq("statement "), speaker=w, frame=frame, compartment=comp,
                       truth=rng.choice(["unknown", "true", "false", "contested"]),
                       confidence=rng.randint(0, 9000), **extra)
        if claims and rng.random() < 0.2:
            tgt = rng.choice(claims)
            body["about"] = [{"claim": tgt["id"], "relation": rng.choice(["replies", "cites", "critiques"])}]
        r = attempt(m.append, w, "claim", body)
        if r:
            claims.append(dict(id=g.claim_id(body), comp=comp, writer=w))

    def op_episode():
        w = rng.choice(list(writers))
        comp = rng.choice(writers[w]["comps"])
        body = dict(compartment=comp, content=uniq("said "), medium="chat", participants=["Pat"])
        attempt(m.append, w, "episode", body)

    def op_verdict():
        w = rng.choice([x for x in writers if "verify" in writers[x]["caps"]] or [owner])
        vc = [c for c in vis_claims(w) if writers[c["writer"]]["party"] != writers[w]["party"]]
        if not vc:
            return
        vc = [c for c in vc if (writers[w]["party"], c["id"]) not in judged]
        if not vc:
            return
        c = rng.choice(vc)
        res = rng.choice(["supports", "refutes", "refutes", "abstain"])
        body = dict(claim=c["id"], result=res,
                    method=rng.choice(["human", "test", "computed"]))
        r = attempt(m.append, w, "verdict", body)
        if r:
            verdicts.append(dict(id=r["event_id"], claim=c["id"], result=res))
            judged.add((writers[w]["party"], c["id"]))

    def op_supersede():
        judged_claims = [c for c in claims if any(v["claim"] == c["id"] for v in verdicts)]
        if not judged_claims:
            return
        old = rng.choice(judged_claims)
        pool = [c for c in claims if c["comp"] == old["comp"] and c is not old
                and writers[c["writer"]]["party"] != writers[old["writer"]]["party"]]
        if not pool:
            return
        new = rng.choice(pool)
        w = new["writer"]
        kind = rng.choice(["refuted", "narrowed", "generalised", "contested"])
        want = "refutes" if kind == "refuted" else "supports"
        vs = [v["id"] for v in verdicts if v["claim"] == old["id"] and v["result"] == want] \
            or [v["id"] for v in verdicts if v["claim"] == old["id"]]
        ev = rng.sample(vs, min(len(vs), rng.randint(1, 2)))
        attempt(m.append, w, "supersede",
                dict(old=old["id"], new=new["id"], kind=kind, evidence=ev, reason=uniq("because ")))

    def op_lease():
        w = rng.choice([owner] + [x for x in writers if "schedule" in writers[x]["caps"]])
        tgt = rng.choice([x for x in writers if x != owner and x != w] or [owner])
        frame = rng.choice(frames)
        exp = ts(NOW + timedelta(hours=rng.randint(1, 12)))  # leases expire by the wall clock
        r = attempt(m.lease, w, tgt, frame, exp)
        if r:
            leases.append(dict(id=r, frame=frame, granter=w, target=tgt, exp=exp))

    def op_release():
        if not leases:
            return
        l = rng.choice([x for x in leases if x["exp"] > ts(NOW)] or leases)
        w = rng.choice([owner, l["granter"], l["target"]])
        attempt(m.release, w, l["id"], reason="done")

    def op_revoke():
        others = [x for x in writers if x != owner]
        if not others:
            return
        tgt = rng.choice(others)
        cs = rng.sample(writers[tgt]["comps"], rng.randint(1, len(writers[tgt]["comps"])))
        attempt(m.revoke, owner, tgt, cs, "no longer needed")

    ops = [(op_claim, 8), (op_episode, 2), (op_verdict, 4), (op_supersede, 5),
           (op_lease, 2), (op_release, 1), (op_revoke, 1), (new_writer, 1)]
    fns = [f for f, w in ops for _ in range(w)]
    for _ in range(rng.randint(15, 40)):
        rng.choice(fns)()

    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------- comparison
def strip(obj, path=()):
    """Remove STRIP paths ('*' matches any key) from a copy of obj."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = path + (k,)
            if any(len(s) == len(p) and all(a in ("*", b) for a, b in zip(s, p)) for s in STRIP):
                continue
            out[k] = strip(v, p if True else path)
        return out
    if isinstance(obj, list):
        return [strip(v, path + ("[]",)) for v in obj]
    return obj


def first_diff(a, b, path="state"):
    if type(a) != type(b):
        return f"{path}: type {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k}: missing in as_of view"
            if k not in b:
                return f"{path}.{k}: missing in rebuilt log"
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


# ------------------------------------------------------------------- driver
def check_log(args):
    gdir, logseed, verbose = args
    sys.path.insert(0, gdir)
    import gnomon as g
    rng = random.Random(logseed)
    STATS.update(accepted=0, ooo=0, refused_n=0, refused={}, kinds={})
    tmp = tempfile.mkdtemp(prefix="asof_", dir=os.environ.get("ASOF_TMP", "/dev/shm" if os.path.isdir("/dev/shm") else None))
    checks, fails, refused_ops = 0, [], 0
    nev = 0
    try:
        path = os.path.join(tmp, "log.jsonl")
        events = generate(g, path, rng)
        nev = len(events)
        for e in events:
            STATS["kinds"][e["kind"]] = STATS["kinds"].get(e["kind"], 0) + 1
        m = g.Memory(path)
        comps_all = sorted({c for e in events if e["kind"] == "register" for c in e["body"]["compartments"]})
        rts = sorted({e["record_time"] for e in events})
        tests = [ts(parse(rts[0]) - timedelta(hours=1))] + rts + [ts(parse(rts[-1]) + timedelta(hours=1))]
        for t in tests:
            sub = [e for e in events if e["record_time"] <= t]
            rpath = os.path.join(tmp, "r.jsonl")
            if os.path.exists(rpath):
                os.remove(rpath)
            rebuilt = g.Memory(rpath)
            refused = None
            for e in sub:
                try:
                    rebuilt.append(e["writer"], e["kind"], e["body"], e["record_time"])
                except g.GateError as ex:
                    refused = (e, str(ex))
                    break
            scopes = [None] + [[c] for c in comps_all]
            if refused:
                checks += len(scopes)
                e, why = refused
                fails.append(dict(seed=logseed, t=t, comp="*", path=f"REBUILD REFUSED at {e['event_id']} "
                                  f"({e['kind']} by {e['writer']} @ {e['record_time']}): {why}"))
                continue
            rb = g.Memory(rpath)
            for sc in scopes:
                checks += 1
                label = "all" if sc is None else sc[0]
                try:
                    got = m.state(as_of=t, compartments=sc)["state"]
                except Exception as ex:
                    fails.append(dict(seed=logseed, t=t, comp=label, path=f"as_of RAISED {type(ex).__name__}: {ex}"))
                    continue
                try:
                    # A log with no events has no file to read, so its state is the literal EMPTY state.
                    exp = copy.deepcopy(EMPTY) if not sub else rb.state(compartments=sc)["state"]
                except Exception as ex:
                    fails.append(dict(seed=logseed, t=t, comp=label, path=f"rebuilt state RAISED {type(ex).__name__}: {ex}"))
                    continue
                d = first_diff(strip(got), strip(exp))
                if d:
                    fails.append(dict(seed=logseed, t=t, comp=label, path=d))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return logseed, nev, checks, fails, copy.deepcopy(STATS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gnomon", required=True)
    ap.add_argument("--logs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--one", type=int, help="re-run this single log seed")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()
    gdir = os.path.abspath(a.gnomon)
    seeds = [a.one] if a.one is not None else [a.seed * 1_000_000 + i for i in range(a.logs)]
    work = [(gdir, s, False) for s in seeds]
    nlogs = nchecks = nevents = 0
    fails = []
    tot = {'accepted': 0, 'ooo': 0, 'refused_n': 0, 'refused': {}, 'kinds': {}}
    with mp.Pool(a.jobs) as pool:
        for s, nev, c, f, st in pool.imap_unordered(check_log, work, chunksize=4):
            tot["accepted"] += st["accepted"]; tot["ooo"] += st["ooo"]; tot["refused_n"] += st["refused_n"]
            for k, v in st["kinds"].items():
                tot["kinds"][k] = tot["kinds"].get(k, 0) + v
            for k, v in st["refused"].items():
                tot["refused"][k] = tot["refused"].get(k, 0) + v
            nlogs += 1
            nchecks += c
            nevents += nev
            fails += f
    print(f"operations (after the genesis): accepted {tot['accepted']} (of which out of order {tot['ooo']}), "
          f"refused {tot['refused_n']} {dict(sorted(tot['refused'].items(), key=lambda kv: -kv[1]))}")
    print(f"events by kind: {dict(sorted(tot['kinds'].items()))}")
    fails.sort(key=lambda f: (f["seed"], f["t"], f["comp"]))
    bad_logs = len({f["seed"] for f in fails})
    print(f"logs: {nlogs}  events accepted: {nevents}  checks: {nchecks}  failures: {len(fails)} (in {bad_logs} logs)")
    for f in fails[:5]:
        print(f"  seed={f['seed']} t={f['t']} compartment={f['comp']} first diff: {f['path']}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
