"""Recall on LoCoMo: does Gnomon find the turns that answer each question?

LoCoMo (Maharana et al., 2024) has 10 long conversations (up to 35 sessions
each) and 1,986 questions, each labelled with the dialogue turns that hold
its evidence. That makes retrieval measurable exactly, with no language
model judging anything: for each question, what fraction of its evidence
turns are among the top k recalled?

The dataset is CC BY-NC 4.0 and is not part of this repository: pass the
path to a clone of https://github.com/snap-research/locomo (or let this
script clone it).

    python bench/locomo.py [--data path/to/locomo10.json] [--ks 1,5,10,20,50]

Each conversation becomes its own memory; each session one episode with its
turns, recorded at the session's date. Image captions are kept as text, as
the benchmark gives them to models. Category 5 (adversarial, unanswerable)
is reported separately and left out of the total, as is usual.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from gnomon import Memory  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402

CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}


def session_time(text: str) -> str:
    """'1:56 pm on 8 May, 2023' -> '2023-05-08T13:56:00Z'."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", text, re.I)
    if not m:
        raise ValueError(f"unrecognised session time: {text!r}")
    h, mi, ap, d, mon, y = m.groups()
    h = int(h) % 12 + (12 if ap.lower() == "pm" else 0)
    dt = datetime.strptime(f"{d} {mon} {y} {h}:{mi}", "%d %B %Y %H:%M")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def turn_text(t: dict) -> str:
    text = t.get("text", "").strip()
    if t.get("blip_caption"):
        text += f" [shares a photo: {t['blip_caption'].strip()}]"
    return text


def load(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def build(sample: dict, workdir: Path) -> tuple[Memory, str, dict]:
    """A memory holding one conversation. Returns it, its reader, and a map
    from (episode id, turn index) to LoCoMo dialogue id."""
    conv = sample["conversation"]
    log = workdir / f"{sample['sample_id']}.jsonl"
    m = Memory(log)
    m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"], compartments=["chat"],
               record_time="2000-01-01T00:00:00Z")
    sessions = []
    for key in conv:
        mm = re.fullmatch(r"session_(\d+)", key)
        if mm and conv[key]:
            sessions.append((session_time(conv[f"{key}_date_time"]), int(mm.group(1)), conv[key]))
    sessions.sort()
    ids = {}
    for when, _, turns in sessions:
        msgs = [(t["speaker"], turn_text(t)) for t in turns]
        kept = [t for t in turns if turn_text(t)]
        ep = record_messages(m, "owner", msgs, compartment="chat", record_time=when)
        for i, t in enumerate(kept):
            ids[(ep, i)] = t["dia_id"]
    return m, "owner", ids


def corrections(path: Path | None) -> dict:
    """Evidence corrections from an audit of LoCoMo's answer key
    (github.com/dial481/locomo-audit, errors.json): (sample index,
    question) -> the evidence the audit says is right. Only citation errors
    change evidence; answer errors do not affect retrieval."""
    if not path:
        return {}
    out = {}
    for e in json.loads(path.read_text(encoding="utf-8")):
        m = re.match(r"locomo_(\d+)_qa", e.get("question_id", ""))
        if m and e.get("correct_evidence") and set(e["correct_evidence"]) != set(e.get("cited_evidence", [])):
            out[(int(m.group(1)), e["question"].strip())] = {x.strip() for x in e["correct_evidence"]}
    return out


def session_of(dia_id: str | None) -> str | None:
    return dia_id.split(":", 1)[0] if dia_id else None


def evaluate(data: list, ks: list[int], kinds=("turns",), tuning: dict | None = None,
             fixes: dict | None = None, offset: int = 0) -> dict:
    per_cat = defaultdict(lambda: {"n": 0, **{f"recall@{k}": 0.0 for k in ks}, **{f"all@{k}": 0 for k in ks},
                                   **{f"session@{k}": 0.0 for k in ks}})
    timing = []
    fixes = fixes or {}
    with tempfile.TemporaryDirectory() as d:
        for si, sample in enumerate(data):
            m, reader, ids = build(sample, Path(d))
            for qa in sample["qa"]:
                # A few evidence entries hold two ids ("D8:6; D9:17") or a
                # malformed one ("D"): split the former, drop the latter.
                evidence = {x.strip() for e in qa.get("evidence", []) for x in re.split(r"[;,]", e)
                            if re.fullmatch(r"D\d+:\d+", x.strip())}
                evidence = fixes.get((si + offset, qa["question"].strip()), evidence)
                if not evidence:
                    continue
                t0 = time.perf_counter()
                r = m.recall(reader, qa["question"], k=max(max(ks), 200), kinds=list(kinds), tuning=tuning)
                timing.append(time.perf_counter() - t0)
                got = [ids.get((h["episode"], int(h["id"].rsplit("#", 1)[1]))) for h in r["hits"]]
                # Sessions ranked by their best turn, as session-level retrievers report.
                sessions = []
                for g in got:
                    sg = session_of(g)
                    if sg and sg not in sessions:
                        sessions.append(sg)
                ev_sessions = {session_of(e) for e in evidence}
                c = per_cat[qa["category"]]
                c["n"] += 1
                for k in ks:
                    found = evidence & set(got[:k])
                    c[f"recall@{k}"] += len(found) / len(evidence)
                    c[f"all@{k}"] += int(found == evidence)
                    c[f"session@{k}"] += len(ev_sessions & set(sessions[:k])) / len(ev_sessions)
    out = {}
    for cat, c in sorted(per_cat.items()):
        n = c["n"]
        out[CATEGORIES.get(cat, str(cat))] = {"questions": n, **{key: round(100 * v / n, 1) for key, v in c.items()
                                                                if key != "n"}}
    main = [c for cat, c in per_cat.items() if cat != 5]
    n = sum(c["n"] for c in main)
    out["all (1-4)"] = {"questions": n, **{key: round(100 * sum(c[key] for c in main) / n, 1)
                                          for key in main[0] if key != "n"}}
    every = list(per_cat.values())
    n = sum(c["n"] for c in every)
    out["all (1-5)"] = {"questions": n, **{key: round(100 * sum(c[key] for c in every) / n, 1)
                                          for key in every[0] if key != "n"}}
    timing.sort()
    out["latency_ms"] = {"median": round(1000 * timing[len(timing) // 2], 1),
                         "p95": round(1000 * timing[int(len(timing) * 0.95)], 1)}
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", help="path to locomo10.json (cloned if omitted)")
    p.add_argument("--ks", default="1,5,10,20,50")
    p.add_argument("--json", action="store_true")
    p.add_argument("--split", choices=["all", "dev", "test"], default="all",
                   help="dev = conversations 0-4 (where choices were made), test = 5-9 (held out)")
    p.add_argument("--ablation", action="store_true", help="each ranking technique on and off")
    p.add_argument("--tuning", help="JSON tuning to evaluate, e.g. '{\"session\": 300}'")
    p.add_argument("--grid", help="JSON {knob: [values,...]}: evaluate each value of each knob alone")
    p.add_argument("--audit", help="errors.json from github.com/dial481/locomo-audit: score against corrected "
                                   "evidence")
    a = p.parse_args()
    if a.data:
        path = Path(a.data)
    else:
        clone = Path(tempfile.gettempdir()) / "locomo"
        if not clone.exists():
            subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/snap-research/locomo",
                            str(clone)], check=True)
        path = clone / "data" / "locomo10.json"
    ks = [int(x) for x in a.ks.split(",")]
    data = load(path)
    offset = 5 if a.split == "test" else 0
    data = {"all": data, "dev": data[:5], "test": data[5:]}[a.split]
    fixes = corrections(Path(a.audit)) if a.audit else {}
    if a.ablation:
        techniques = ["joins", "speaker_prior", "propagate", "prf"]
        configs = [("BM25 only", {t: False for t in techniques})]
        configs += [(f"+ {t}", {x: x == t for x in techniques}) for t in techniques]
        configs += [("all on", {t: True for t in techniques})]
        configs += [(f"all but {t}", {x: x != t for x in techniques}) for t in techniques]
        print(f"{'configuration':<22}" + "".join(f"{'R@' + str(k):>8}" for k in ks))
        for name, tuning in configs:
            row = evaluate(data, ks, tuning=tuning)["all (1-4)"]
            print(f"{name:<22}" + "".join(f"{row[f'recall@{k}']:>8}" for k in ks))
        return 0
    if a.grid:
        grid = json.loads(a.grid)
        base = json.loads(a.tuning) if a.tuning else {}
        print(f"{'setting':<28}" + "".join(f"{'R@' + str(k):>8}" for k in ks))
        row = evaluate(data, ks, tuning=base or None)["all (1-4)"]
        print(f"{'base ' + json.dumps(base):<28}" + "".join(f"{row[f'recall@{k}']:>8}" for k in ks))
        for knob, values in grid.items():
            for v in values:
                t = dict(base, **{knob: v})
                row = evaluate(data, ks, tuning=t)["all (1-4)"]
                print(f"{knob + '=' + str(v):<28}" + "".join(f"{row[f'recall@{k}']:>8}" for k in ks), flush=True)
        return 0
    res = evaluate(data, ks, tuning=json.loads(a.tuning) if a.tuning else None, fixes=fixes, offset=offset)
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    cols = [f"recall@{k}" for k in ks] + [f"all@{k}" for k in ks] + [f"session@{k}" for k in ks]
    print(f"{'category':<14}{'n':>6}" + "".join(f"{c:>11}" for c in cols))
    for cat, row in res.items():
        if cat == "latency_ms":
            continue
        print(f"{cat:<14}{row['questions']:>6}" + "".join(f"{row[c]:>11}" for c in cols))
    print(f"latency per question: median {res['latency_ms']['median']} ms, p95 {res['latency_ms']['p95']} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
