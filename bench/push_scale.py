"""Push at scale: protocol in push_scale.md.

    python bench/push_scale.py --beam BEAM --locomo F --lme F --convomem DIR [--json push_scale_results.json]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from beam import cluster_ci, tokens  # noqa: E402
from boundaries import CACHE  # noqa: E402
from situation import REQUEST, SELF, SENT  # noqa: E402
from situation_reader import STOP  # noqa: E402
from views import terms  # noqa: E402
from views_forage import kwic_aware, record_stats  # noqa: E402

START, BUDGETS = 1500, (500, 1000, 2000)


def session_start(bs) -> set:
    """Message ids of the situation pushed at session start: the most recent
    first-person, non-request user sentences that fit START tokens."""
    cand = []
    for b in bs:
        for t in b["turns"]:
            for m in t:
                if m["role"] != "user":
                    continue
                for x in SENT.finditer(m["content"]):
                    s = x.group(0).strip()
                    if s and SELF.search(s) and not REQUEST.search(s):
                        cand.append((m["id"], s))
    keep, used = set(), 0
    for mid, s in reversed(cand):
        c = (len(s) + 8) // 4
        if used + c > START:
            break
        keep.add(mid)
        used += c
    return keep


def per_message(question, order, msgs, idf, budget):
    qw, text, got, used = terms(question), [], set(), 0
    for g in order:
        if g not in msgs:
            continue
        body = kwic_aware(msgs[g], qw, idf)
        cost = tokens(body) + 6
        if used + cost > budget:
            break
        text.append(body)
        got.add(g)
        used += cost
    return " ".join(text), got


def run(name, loader, cache_file, rows):
    it = iter(pickle.loads((CACHE / cache_file).read_bytes()))
    for conv, bs, qs in loader:
        msgs = start = None
        for q in qs:
            if msgs is None:
                msgs = {x["id"]: x["content"] for b in bs for t in b["turns"] for x in t if x["content"].strip()}
                idf, _, _ = record_stats(msgs)
                start = session_start(bs)
            ev = set(q["ev"]) & set(msgs)
            if not ev:
                continue
            c = next(it)
            assert c["ev"] == ev, (name, conv)
            gold = {w for w in terms(q.get("answer") or q.get("rubric") or "") if w not in STOP} - terms(q["question"])
            if not gold:
                continue
            row = {"dataset": name, "conv": conv, "start": float(bool(ev & start))}
            for b in BUDGETS:
                text, got = per_message(q["question"], c["hybrid"], msgs, idf, b)
                row[f"words {b}"] = len(gold & terms(text)) / len(gold)
                row[f"ev {b}"] = float(bool(ev & got))
            rows.append(row)
    assert next(it, None) is None, name
    print(f"{name}: {sum(r['dataset'] == name for r in rows)} questions", flush=True)


def main():
    import multi
    p = argparse.ArgumentParser()
    p.add_argument("--beam", required=True)
    p.add_argument("--locomo", required=True)
    p.add_argument("--lme", required=True)
    p.add_argument("--convomem", required=True)
    p.add_argument("--json", default="push_scale_results.json")
    a = p.parse_args()
    rows = []
    for size in ("100K", "1M", "10M"):
        run(f"BEAM {size}", multi.load_beam(Path(a.beam), size), f"{size}.pkl", rows)
    run("LoCoMo", multi.load_locomo(Path(a.locomo)), "multi_locomo.pkl", rows)
    run("LongMemEval-S", multi.load_lme(Path(a.lme)), "multi_lme_s.pkl", rows)
    run("ConvoMem", multi.load_convomem(sorted(Path(a.convomem).glob("*_evidence.json"))), "multi_convomem.pkl", rows)

    def ci(rs, key):
        g = {}
        for r in rs:
            k = r["conv"] if r["dataset"].startswith(("BEAM", "LoCoMo")) else r["conv"] + str(id(r))
            g.setdefault(k, []).append(key(r))
        return cluster_ci(g)

    out = {}
    print(f"\n{'record':14s}{'n':>6s}{'start ev':>10s}" + "".join(f"{f'ev {b}':>10s}" for b in BUDGETS)
          + "".join(f"{f'words {b}':>12s}" for b in BUDGETS))
    for name in dict.fromkeys(r["dataset"] for r in rows):
        rs = [r for r in rows if r["dataset"] == name]
        res = {k: ci(rs, lambda r, k=k: r[k]) for k in ["start"] + [f"ev {b}" for b in BUDGETS]
               + [f"words {b}" for b in BUDGETS]}
        res["n"] = len(rs)
        out[name] = res
        print(f"{name:14s}{len(rs):6d}{res['start'][0]:10}" + "".join(f"{res[f'ev {b}'][0]:10}" for b in BUDGETS)
              + "".join(f"{res[f'words {b}'][0]:12}" for b in BUDGETS))
    (HERE / a.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
