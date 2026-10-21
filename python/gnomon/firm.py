"""The firm: shaping a team of models to a piece of work, by price and
by track record.

Think of a request as work for a firm. Most of it is routine and goes to
the cheapest staff who do it reliably; judgement that is costly to get
wrong goes to the expensive people; checking is cheap when the computer can
do it exactly, and is a job in its own right when it cannot. How big the
firm is (headcount), how tall (floors of management) and who works on each
floor (its demographics) follow from the work, and change as the record of
who got what right grows.

Everything here is exact arithmetic over stated inputs:

* **Prices** are the published per-token prices (`TIERS`, read on the date
  in `PRICES_READ`), with cache reads and writes. Haiku's rise above 100,000
  prompt tokens is modelled, so staff desks are kept under it; a tier whose
  context window cannot hold a part is never given it. Work that is not
  urgent can go to the Batch API at half price (the night shift).
* **Track record** comes from the memory itself, as work orders and
  results. A work order is a claim in a `firm/<kind>` frame; a result is a
  claim in the same frame, by a model, that `answers` a work order posted by
  someone outside the worker's own group (another model, a person, a
  tool). Each result counts once, settled by the verdicts on it from
  groups other than the worker's own: one vote per group, majority wins,
  ties count for nothing. A worker cannot post its own work orders, and its
  own model cannot judge it.
* **A decision** compares every way of doing one unit of work: one tier
  alone, or a cascade (cheap first, escalate when a check fails) with an
  exact check, a reviewing tier, or none. Each is priced as expected spend
  on tokens, plus expected loss, plus the price of waiting (`step_usd` per
  sequential round). Loss is `stakes_usd` for a wrong result kept unnoticed
  and `detected_usd` (by default the same) for a part known not to be done
  (flagged at the top of its cascade, rightly or wrongly). The cheapest total wins; ties go to the
  option more likely to be right.
* **The shape** follows: parts that can be done in parallel are staffed in
  parallel; a planner decomposes and an integrator assembles when there is
  more than one part; each manager takes as many reports as fit on their
  desk, and more floors are added when one floor of managers would have
  too many.
* **The baseline** is one agent (Opus unless another is named) doing every part in turn, with the
  same checks available to it, re-reading what it has done so far from the
  prompt cache, its context compacted to its desk when it grows past it.

Stated assumptions, until the record replaces them: the priors (`PRIORS`),
reviewers' catch and false-alarm rates (`REVIEW`), the size of a summary
handed up a floor, the Haiku context window, and that attempts are
independent (a part one tier got wrong is taken to be no harder for the
next, which flatters cascades; the record of escalated parts is also
selected, holding the harder parts).

The output is a plan, with its arithmetic, for a host that can start agents
on a chosen model (Claude Code's subagents, the API). Whether a plan beats a
single agent in practice is measured by recording the work orders, the
results and the verdicts, which is also what feeds the next plan.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ------------------------------------------------------------------ prices

PRICES_READ = "2026-10-21, https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""
    input: float
    output: float
    cache_hit: float
    cache_write: float  # five-minute cache


@dataclass(frozen=True)
class Tier:
    name: str
    model_prefix: str  # a model id starting with this is this tier
    model: str
    price: Price
    window: int  # most prompt tokens the model takes
    desk: int  # most prompt tokens a worker on this tier is given by choice
    long_price: Optional[Price] = None  # above long_threshold prompt tokens
    long_threshold: Optional[int] = None

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0, written_tokens: int = 0,
             batch: bool = False) -> float:
        """USD for one request: `input_tokens` fresh, `cached_tokens` read
        from the prompt cache, `written_tokens` of the fresh ones also
        written to it, `output_tokens` written out."""
        p = self.price
        if self.long_threshold is not None and input_tokens + cached_tokens > self.long_threshold:
            p = self.long_price or p
        usd = (input_tokens * p.input + cached_tokens * p.cache_hit + written_tokens * (p.cache_write - p.input)
               + output_tokens * p.output) / 1e6
        return usd / 2 if batch else usd


TIERS: dict[str, Tier] = {
    "haiku": Tier("haiku", "claude-haiku-", "claude-haiku-5-5", Price(0.10, 0.50, 0.01, 0.125),
                  window=200_000, desk=100_000,  # window: an assumption
                  long_price=Price(0.50, 2.50, 0.05, 0.625), long_threshold=100_000),
    "sonnet": Tier("sonnet", "claude-sonnet-", "claude-sonnet-5-5", Price(2.0, 10.0, 0.10, 2.5),
                   window=1_000_000, desk=150_000),
    "opus": Tier("opus", "claude-opus-", "claude-opus-5-5", Price(4.0, 20.0, 0.20, 5.0),
                 window=1_000_000, desk=150_000),
    "fable": Tier("fable", "claude-fable-", "claude-fable-5-1", Price(10.0, 50.0, 0.25, 12.5),
                  window=1_000_000, desk=150_000),
}
ORDER = ["haiku", "sonnet", "opus", "fable"]  # cheapest first
MAX_OUTPUT = 32_000  # output tokens per request the planner is given at most

# Prior beliefs, as pseudo-counts (successes, failures) per tier, before any
# record: assumptions, stated so they can be argued with. The record soon
# outweighs them.
PRIORS: dict[str, tuple[float, float]] = {"haiku": (6, 4), "sonnet": (8, 2), "opus": (9, 1), "fable": (9.5, 0.5)}
# How often a reviewing tier catches a wrong result (and wrongly flags a
# right one): assumptions. (Gnomon refuses a party's verdict on its own
# party's claims: register reviewing models as parties of their own to put
# their reviews on record.)
REVIEW: dict[str, tuple[float, float]] = {"haiku": (0.55, 0.10), "sonnet": (0.75, 0.06), "opus": (0.85, 0.04),
                                          "fable": (0.90, 0.03)}
CHECKS = ("exact", "review", "none")


# ------------------------------------------------------------ track record

@dataclass
class Record:
    """Successes and failures per (tier, kind of task), from verdicts."""
    counts: dict = field(default_factory=dict)
    truncated: bool = False

    def add(self, tier: str, kind: str, success: bool, n: int = 1) -> None:
        kind = kind.lower()
        s, f = self.counts.get((tier, kind), (0, 0))
        self.counts[(tier, kind)] = (s + n, f) if success else (s, f + n)

    def p(self, tier: str, kind: str) -> float:
        """Posterior mean chance that `tier` gets a `kind` task right."""
        a, b = PRIORS[tier]
        s, f = self.counts.get((tier, kind.lower()), (0, 0))
        return (a + s) / (a + b + s + f)

    def seen(self, tier: str, kind: str) -> int:
        return sum(self.counts.get((tier, kind.lower()), (0, 0)))


def tier_of(model: Optional[str]) -> Optional[str]:
    m = (model or "").lower()
    for t in ORDER:
        if m.startswith(TIERS[t].model_prefix):
            return t
    return None


# Results that answer someone else's work order in the same firm frame, with
# verdicts from outside the worker's group: one row per verdict.
_RECORD_QUERY = (
    '?- frame(C, F), contains(F, "firm/"), reference(C, T, "answers"), frame(T, F), '
    'author(C, A), author(T, P), group(A, GA), group(P, GP), GA != GP, '
    'verdict(V, C, W, R, M), group(W, GW), GA != GW.'
)


def outcomes(memory, reader: str) -> tuple[list, bool]:
    """Every judged result `reader` can see, one outcome each (see the module
    documentation for what counts): dicts with the result's `claim`, its
    `author`, the `kind` of work and whether it was a `success`; and whether
    the record was too large to read in full."""
    r = memory.query(reader, _RECORD_QUERY, proofs=0)
    cols = r["variables"]
    votes: dict = {}  # claim -> {verifier group: result}
    meta: dict = {}  # claim -> (author, frame)
    for a in r["answers"]:
        row = dict(zip(cols, a["values"]))
        if not row["F"].startswith("firm/") or row["R"] not in ("supports", "refutes"):
            continue
        meta[row["C"]] = (row["A"], row["F"])
        votes.setdefault(row["C"], {})[row["GW"]] = row["R"]
    out = []
    for c, by_group in sorted(votes.items()):
        author, frame = meta[c]
        yes = sum(1 for v in by_group.values() if v == "supports")
        no = len(by_group) - yes
        if yes != no:
            out.append({"claim": c, "author": author, "kind": frame[len("firm/"):].lower(), "success": yes > no})
    return out, bool(r.get("truncated"))


def record_from(memory, reader: str) -> Record:
    """The firm's track record as `reader` sees it, by tier: only authors of
    kind `model` whose model id has a tier's prefix count."""
    results, truncated = outcomes(memory, reader)
    writers = memory.writers(compartments=memory._compartments(reader))
    rec = Record(truncated=truncated)
    for o in results:
        w = writers.get(o["author"], {})
        t = tier_of(w.get("model")) if w.get("kind") == "model" else None
        if t is not None:
            rec.add(t, o["kind"], o["success"])
    return rec


# ------------------------------------------------------------- decisions

def _number(name: str, v, lo: float = 0, hi: float = 1e9, integer: bool = False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (integer and not isinstance(v, int)) \
            or (isinstance(v, float) and not math.isfinite(v)) or not lo <= v <= hi:
        raise ValueError(f"{name} must be {'a whole number' if integer else 'a number'} from {lo:g} to {hi:g}")
    return v


@dataclass(frozen=True)
class Task:
    """One unit of work (or `parts` like it, independent of each other)."""
    kind: str
    input_tokens: int
    output_tokens: int
    stakes_usd: float  # what a wrong result that goes unnoticed costs
    check: str = "none"  # "exact" (the computer can check it), "review", or "none"
    parts: int = 1
    urgent: bool = True
    step_usd: float = 0.0  # what one more sequential round of waiting costs
    detected_usd: Optional[float] = None  # what a part known not to be done costs (default: stakes_usd)

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("kind must be a name")
        object.__setattr__(self, "kind", self.kind.strip().lower())
        _number("input_tokens", self.input_tokens, 0, 10_000_000, True)
        _number("output_tokens", self.output_tokens, 0, 1_000_000, True)
        _number("stakes_usd", self.stakes_usd)
        _number("parts", self.parts, 1, 100_000, True)
        _number("step_usd", self.step_usd, 0, 1e6)
        if self.detected_usd is None:
            object.__setattr__(self, "detected_usd", self.stakes_usd)
        _number("detected_usd", self.detected_usd)
        if self.check not in CHECKS:
            raise ValueError(f"check must be one of {', '.join(CHECKS)}")
        if not isinstance(self.urgent, bool):
            raise ValueError("urgent must be true or false")


@dataclass
class Choice:
    """A way to do one unit of work, priced."""
    workers: tuple  # tiers tried in turn
    reviewer: Optional[str]
    spend: float  # expected USD on tokens
    loss: float  # expected USD lost: wrong results kept, parts left undone
    rounds: float  # expected sequential rounds (each attempt; each review is another)
    p_right: float  # chance the result kept is right
    reach: tuple  # chance each attempt is made

    def total(self, step_usd: float) -> float:
        return self.spend + self.loss + step_usd * self.rounds

    def describe(self) -> str:
        how = " then ".join(self.workers)
        if len(self.workers) > 1:
            how = f"cascade {how}"
        if self.reviewer == "exact":
            how += ", checked exactly"
        elif self.reviewer:
            how += f", reviewed by {self.reviewer}"
        return how


def _review_tokens(task: Task) -> tuple[int, int]:
    """A reviewer reads the task and the work, and writes a short verdict."""
    return task.input_tokens + task.output_tokens, 200


def price_choice(task: Task, workers: tuple, reviewer: Optional[str], rec: Record) -> Choice:
    """Expected spend, loss and rounds of trying `workers` in turn, each
    result judged by `reviewer` ("exact", a tier, or None) before the next
    is tried. Exact: a wrong result is always caught, a right one never
    flagged. A tier: caught with its catch rate, flagged wrongly with its
    false-alarm rate. None: the first result is kept. A result flagged at
    the top of the cascade is set aside: the part is known not to be done."""
    batch = not task.urgent
    spend = loss = rounds = 0.0
    p_kept_right = 0.0
    reach = 1.0  # chance this attempt is made
    reaches = []
    for i, t in enumerate(workers):
        reaches.append(reach)
        spend += reach * TIERS[t].cost(task.input_tokens, task.output_tokens, batch=batch)
        rounds += reach
        p = rec.p(t, task.kind)
        if reviewer is None:
            p_kept_right += reach * p
            loss += reach * (1 - p) * task.stakes_usd
            break
        if reviewer == "exact":
            catch, false_alarm = 1.0, 0.0
        else:
            rin, rout = _review_tokens(task)
            spend += reach * TIERS[reviewer].cost(rin, rout, batch=batch)
            rounds += reach
            catch, false_alarm = REVIEW[reviewer]
        passed_right = p * (1 - false_alarm)
        passed_wrong = (1 - p) * (1 - catch)
        flagged = 1 - passed_right - passed_wrong
        p_kept_right += reach * passed_right
        loss += reach * passed_wrong * task.stakes_usd
        if i == len(workers) - 1:
            loss += reach * flagged * task.detected_usd
        reach *= flagged
    return Choice(tuple(workers), reviewer, spend, loss, rounds, p_kept_right, tuple(reaches))


def _fits(task: Task, t: str) -> bool:
    return task.input_tokens <= TIERS[t].window


def choices(task: Task, rec: Record, tiers: Iterable[str] = ORDER) -> list:
    """Every way to do one unit with `tiers`: each alone, each rising
    cascade, with each kind of check the task allows. Best first."""
    tiers = [t for t in tiers if _fits(task, t)]
    if not tiers:
        raise ValueError("no model's context window holds one part: split the parts further")
    reviewers: list = [None]
    if task.check == "exact":
        reviewers.append("exact")
    if task.check in ("exact", "review"):
        reviewers += [t for t in ORDER if TIERS[t].window >= _review_tokens(task)[0]]
    out = []
    for n in range(1, len(tiers) + 1):
        for workers in itertools.combinations(tiers, n):
            for r in reviewers:
                if r is None and n > 1:
                    continue  # without a check nothing tells it to escalate
                out.append(price_choice(task, workers, r, rec))
    # Ties: more likely right, then cheaper, then checked rather than not.
    out.sort(key=lambda c: (c.total(task.step_usd), -c.p_right, c.spend, c.reviewer is None))
    return out


def decide(task: Task, rec: Optional[Record] = None) -> Choice:
    return choices(task, rec or Record())[0]


# ---------------------------------------------------------------- shapes

SUMMARY_TOKENS = 600  # what a report hands up a floor: an assumption


@dataclass
class Plan:
    task: Task
    unit: Choice  # how each part is done
    floors: list  # top floor first: [(role, tier, headcount)]
    span: int  # reports per manager
    spend: float  # expected USD on tokens
    loss: float  # expected USD lost
    rounds: int  # sequential rounds on the critical path (worst case)
    single: str  # the tier of the single agent compared with
    baseline: Choice  # how that one agent does each part
    baseline_spend: float
    baseline_loss: float
    baseline_rounds: float  # expected
    notes: list

    @property
    def headcount(self) -> int:
        return sum(n for _, _, n in self.floors)

    @property
    def total(self) -> float:
        return self.spend + self.loss + self.task.step_usd * self.rounds

    @property
    def baseline_total(self) -> float:
        return self.baseline_spend + self.baseline_loss + self.task.step_usd * self.baseline_rounds

    def summary(self) -> str:
        t = self.task
        lines = [f"Plan for {t.parts} part(s) of '{t.kind}' (stakes ${t.stakes_usd:g} each, check: {t.check}):"]
        for role, tier, n in self.floors:
            lines.append(f"  {role}: {n} x {tier}")
        lines.append(f"  each part: {self.unit.describe()} (right {self.unit.p_right:.1%})")
        wait = f" + ${t.step_usd * self.rounds:.4f} waiting" if t.step_usd else ""
        lines.append(f"  expected: ${self.spend:.4f} tokens + ${self.loss:.4f} lost{wait} = ${self.total:.4f}; "
                     f"{self.rounds} round(s) at most")
        bwait = f" + ${t.step_usd * self.baseline_rounds:.4f} waiting" if t.step_usd else ""
        lines.append(f"  one {self.single} agent alone ({self.baseline.describe()}, each part in turn): "
                     f"${self.baseline_spend:.4f} tokens + ${self.baseline_loss:.4f} lost{bwait} = "
                     f"${self.baseline_total:.4f}; {self.baseline_rounds:.1f} round(s) expected")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def _baseline(task: Task, rec: Record, top: str) -> tuple:
    """One agent of tier `top` doing every part in turn, with the best
    checks it can use, re-reading its work so far from the prompt cache
    (compacted to its desk once it outgrows it) and writing each part's
    prompt to the cache. Returns (how each part is done, tokens, loss,
    rounds)."""
    tier = TIERS[top]
    unit = choices(task, rec, [top])[0]
    batch = not task.urgent
    per_part = task.input_tokens + task.output_tokens
    room = max(0, tier.desk - task.input_tokens)
    spend = 0.0
    for i in range(task.parts):
        context = min(i * per_part, room)
        # Every attempt at this part re-reads the context so far.
        spend += unit.spend + sum(unit.reach) * tier.cost(0, 0, cached_tokens=context,
                                                          written_tokens=task.input_tokens, batch=batch)
    return unit, spend, task.parts * unit.loss, task.parts * unit.rounds


def plan(task: Task, rec: Optional[Record] = None, manager: str = "sonnet", executive: str = "opus",
         single: str = "opus") -> Plan:
    """The firm for `task`: who does each part, who checks it, how many
    managers on how many floors, what it is expected to cost and risk, and
    the same for one top-tier agent doing it all alone."""
    rec = rec or Record()
    unit = decide(task, rec)
    batch = not task.urgent
    notes: list = []
    floors: list = []
    spend = task.parts * unit.spend
    loss = task.parts * unit.loss
    # Rounds on the critical path: with many parts in parallel, some part
    # takes every attempt (and review) of its cascade.
    rounds = len(unit.workers) * (2 if unit.reviewer not in (None, "exact") else 1)
    span = max(2, TIERS[manager].desk // (SUMMARY_TOKENS * 2))
    if task.parts > 1:
        top = TIERS[executive]
        # Managers: each reads its own reports' summaries; another floor when
        # one floor of managers would itself be more than a span.
        widths, width = [], task.parts
        while width > span:
            below = width
            width = math.ceil(width / span)
            widths.append(width)
            full, rest = divmod(below, span)
            spend += full * TIERS[manager].cost(SUMMARY_TOKENS * span, SUMMARY_TOKENS, batch=batch)
            if rest:
                spend += TIERS[manager].cost(SUMMARY_TOKENS * rest, SUMMARY_TOKENS, batch=batch)
        for i, n in enumerate(reversed(widths)):
            floors.append((f"managers (floor {len(widths) - i})", manager, n))
        # The planner writes the work orders (at most MAX_OUTPUT tokens a
        # request); the integrator reads the top floor's summaries.
        orders = 300 * task.parts
        requests = math.ceil(orders / MAX_OUTPUT)
        for k in range(requests):
            spend += top.cost(task.input_tokens + 2000, min(MAX_OUTPUT, orders - k * MAX_OUTPUT), batch=batch)
        top_reports = widths[-1] if widths else task.parts
        spend += top.cost(SUMMARY_TOKENS * top_reports, task.output_tokens, batch=batch)
        floors.insert(0, ("executive (plans and integrates)", executive, 1))
        rounds += requests + len(widths) + 1
        notes.append("a mistake in the plan or the integration affects every part; its risk is not priced here")
    floors.append(("staff", unit.workers[0], task.parts))
    for t, r in zip(unit.workers[1:], unit.reach[1:]):
        floors.append(("escalations (expected)", t, math.ceil(task.parts * r)))
    if unit.reviewer not in (None, "exact"):
        floors.append(("reviews (expected)", unit.reviewer, math.ceil(task.parts * sum(unit.reach))))
    first = TIERS[unit.workers[0]]
    if first.long_threshold and task.input_tokens > first.long_threshold:
        notes.append(f"each part's prompt is over {first.long_threshold:,} tokens: split it further to keep "
                     f"{first.name} at its lower price")
    if batch:
        notes.append("not urgent: the Batch API halves every price (slower)")
    b_unit, b_spend, b_loss, b_rounds = _baseline(task, rec, single)
    return Plan(task, unit, floors, span, spend, loss, rounds, single, b_unit, b_spend, b_loss, b_rounds, notes)


def as_dict(p: Plan) -> dict:
    return {
        "floors": [{"role": r, "tier": t, "model": TIERS[t].model, "headcount": n} for r, t, n in p.floors],
        "each_part": {"workers": list(p.unit.workers), "check": p.unit.reviewer, "p_right": round(p.unit.p_right, 4)},
        "expected": {"tokens_usd": round(p.spend, 6), "lost_usd": round(p.loss, 6), "rounds": p.rounds,
                     "total_usd": round(p.total, 6)},
        "single_agent": {"tier": p.single, "each_part": p.baseline.describe(),
                         "tokens_usd": round(p.baseline_spend, 6), "lost_usd": round(p.baseline_loss, 6),
                         "rounds": round(p.baseline_rounds, 3), "total_usd": round(p.baseline_total, 6)},
        "span": p.span,
        "notes": p.notes,
        "prices_read": PRICES_READ,
    }


def explain(rec: Record, kinds: Iterable[str]) -> list:
    """The current belief per tier and kind, and how much of it is record."""
    return [{"tier": t, "kind": k.lower(), "p_right": round(rec.p(t, k), 4), "on_record": rec.seen(t, k)}
            for k in kinds for t in ORDER]
