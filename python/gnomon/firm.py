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

* **Prices** are the published per-token prices (`PRICES`, with the date
  they were read). Haiku's rise above 100,000 prompt tokens is modelled, so
  staff desks are kept under it. Work that is not urgent can go to the
  Batch API at half price (the night shift).
* **Track record** comes from the memory itself: each finished task is a
  claim in a `firm/<kind>` frame by the worker that did it, and each check
  is a verdict on it by another party. `supports` counts as a success,
  `refutes` as a failure. Gnomon's rules already stop anyone verifying its
  own work, and group sessions of one model together. Per tier and kind of
  task, successes and failures update a Beta prior (`PRIORS`, stated
  assumptions until the record outweighs them).
* **A decision** compares every way of doing one unit of work: one tier
  alone, or a cascade (cheap first, escalate when a check fails) with an
  exact check, a reviewing tier, or none. Each is priced as expected spend
  plus expected loss (the chance a wrong result goes unnoticed, times what
  that costs, `stakes_usd`), plus what each further wait costs
  (`step_usd`), and the cheapest total wins. Attempts are treated as
  independent: a task one model gets wrong is taken to be no harder for the
  next. That flatters cascades; the record will say by how much.
* **The shape** follows: parts that can be done in parallel are staffed in
  parallel; a planner decomposes and an integrator assembles when there is
  more than one part; each manager takes as many reports as fit on their
  desk (their context), and more floors are added when one floor of
  managers would have too many. The baseline it is compared with is one
  agent doing every part in turn, re-reading its growing context each time
  (as cache hits).

The output is a plan, with its arithmetic, for a host that can start agents
on a chosen model (Claude Code's subagents, the API). Whether a plan beats a
single agent in practice is measured by recording what each worker did and
how it was judged, which is exactly what feeds the next plan.
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


@dataclass(frozen=True)
class Tier:
    name: str
    model: str
    price: Price
    long_price: Optional[Price] = None  # above long_threshold prompt tokens
    long_threshold: Optional[int] = None
    desk: int = 150_000  # prompt tokens a worker on this tier is given at most

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0, batch: bool = False) -> float:
        """USD for one request: `input_tokens` fresh, `cached_tokens` read
        from the prompt cache, `output_tokens` written."""
        p = self.price
        if self.long_threshold is not None and input_tokens + cached_tokens > self.long_threshold:
            p = self.long_price or p
        usd = (input_tokens * p.input + cached_tokens * p.cache_hit + output_tokens * p.output) / 1e6
        return usd / 2 if batch else usd


TIERS: dict[str, Tier] = {
    "haiku": Tier("haiku", "claude-haiku-5-5", Price(0.10, 0.50, 0.01), Price(0.50, 2.50, 0.05), 100_000,
                  desk=100_000),
    "sonnet": Tier("sonnet", "claude-sonnet-5-5", Price(2.0, 10.0, 0.10)),
    "opus": Tier("opus", "claude-opus-5-5", Price(4.0, 20.0, 0.20)),
}
ORDER = ["haiku", "sonnet", "opus"]  # cheapest first

# Prior beliefs, as pseudo-counts (successes, failures) per tier, before any
# record: assumptions, stated so they can be argued with. The record soon
# outweighs them.
PRIORS: dict[str, tuple[float, float]] = {"haiku": (6, 4), "sonnet": (8, 2), "opus": (9, 1)}
# How often a reviewing tier catches a wrong result (and wrongly flags a
# right one): assumptions too, until reviews are on record.
REVIEW: dict[str, tuple[float, float]] = {"haiku": (0.55, 0.10), "sonnet": (0.75, 0.06), "opus": (0.85, 0.04)}


# ------------------------------------------------------------ track record

@dataclass
class Record:
    """Successes and failures per (tier, kind of task), from verdicts."""
    counts: dict = field(default_factory=dict)

    def add(self, tier: str, kind: str, success: bool, n: int = 1) -> None:
        s, f = self.counts.get((tier, kind), (0, 0))
        self.counts[(tier, kind)] = (s + n, f) if success else (s, f + n)

    def p(self, tier: str, kind: str) -> float:
        """Posterior mean chance that `tier` gets a `kind` task right."""
        a, b = PRIORS[tier]
        s, f = self.counts.get((tier, kind), (0, 0))
        return (a + s) / (a + b + s + f)

    def seen(self, tier: str, kind: str) -> int:
        return sum(self.counts.get((tier, kind), (0, 0)))


def tier_of(model: Optional[str]) -> Optional[str]:
    m = (model or "").lower()
    for t in ORDER:
        if t in m:
            return t
    return None


def record_from(memory, reader: str) -> Record:
    """The firm's track record as `reader` sees it: verdicts by other parties
    on claims in `firm/<kind>` frames, by the tier of the claim's author."""
    q = "?- frame(C, F), author(C, A), verdict(V, C, W, R, M)."
    r = memory.query(reader, q, proofs=0)
    writers = memory.writers()

    def model_of(w: str) -> Optional[str]:
        info = writers.get(w, {})
        while info.get("model") is None and info.get("instance_of"):
            info = writers.get(info["instance_of"], {})
        return info.get("model")

    rec = Record()
    for c, frame, author, _v, _w, result, _m in (a["values"] for a in r["answers"]):
        if not frame.startswith("firm/") or result not in ("supports", "refutes"):
            continue
        t = tier_of(model_of(author))
        if t:
            rec.add(t, frame[len("firm/"):], result == "supports")
    return rec


# ------------------------------------------------------------- decisions

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
    step_usd: float = 0.0  # what waiting for one more attempt costs (time has a price)


@dataclass
class Choice:
    """A way to do one unit of work, priced."""
    workers: tuple  # tiers tried in turn
    reviewer: Optional[str]
    spend: float  # expected USD on tokens
    loss: float  # expected USD lost to wrong results unnoticed
    p_right: float  # chance the result kept is right

    @property
    def total(self) -> float:
        return self.spend + self.loss

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
    """Expected spend and loss of trying `workers` in turn, each result
    judged by `reviewer` ("exact", a tier, or None) before the next is
    tried. Exact: a wrong result is always caught, a right one never
    flagged. A tier: caught with its sensitivity, flagged wrongly with its
    false-alarm rate. None: the first result is kept."""
    batch = not task.urgent
    spend = loss = 0.0
    p_kept_right = 0.0
    reach = 1.0  # chance this step is reached
    for i, t in enumerate(workers):
        tier = TIERS[t]
        spend += reach * (tier.cost(task.input_tokens, task.output_tokens, batch=batch) + task.step_usd)
        p = rec.p(t, task.kind)
        last = i == len(workers) - 1
        if reviewer is None:
            # Kept as it is.
            p_kept_right += reach * p
            loss += reach * (1 - p) * task.stakes_usd
            break
        if reviewer == "exact":
            catch, false_alarm = 1.0, 0.0
        else:
            rin, rout = _review_tokens(task)
            spend += reach * TIERS[reviewer].cost(rin, rout, batch=batch)
            catch, false_alarm = REVIEW[reviewer]
        passed_right = p * (1 - false_alarm)
        passed_wrong = (1 - p) * (1 - catch)
        flagged = 1 - passed_right - passed_wrong
        p_kept_right += reach * passed_right
        loss += reach * passed_wrong * task.stakes_usd
        if last:
            # Flagged at the top of the cascade: nothing better to try. The
            # flag is acted on (the work is known not to be done), which
            # costs the stakes when the work was in fact wrong, and the
            # chance of a right result wrongly set aside otherwise.
            loss += reach * flagged * task.stakes_usd
        reach *= flagged
    return Choice(tuple(workers), reviewer, spend, loss, p_kept_right)


def choices(task: Task, rec: Record) -> list:
    """Every way to do one unit: each tier alone, each rising cascade, with
    each kind of check the task allows. Cheapest total first."""
    reviewers: list = [None]
    if task.check == "exact":
        reviewers.append("exact")
    if task.check in ("exact", "review"):
        reviewers += ORDER
    out = []
    for n in range(1, len(ORDER) + 1):
        for workers in itertools.combinations(ORDER, n):
            for r in reviewers:
                if r is None and n > 1:
                    continue  # without a check nothing tells it to escalate
                out.append(price_choice(task, workers, r, rec))
    out.sort(key=lambda c: (c.total, c.spend))
    return out


def decide(task: Task, rec: Optional[Record] = None) -> Choice:
    return choices(task, rec or Record())[0]


# ---------------------------------------------------------------- shapes

@dataclass
class Plan:
    task: Task
    unit: Choice  # how each part is done
    floors: list  # top floor first: [(role, tier, headcount)]
    span: int  # reports per manager
    spend: float
    loss: float
    steps: int  # sequential steps (latency, in rounds of work)
    baseline_spend: float  # one agent doing every part in turn
    baseline_loss: float
    baseline_steps: int
    notes: list

    @property
    def headcount(self) -> int:
        return sum(n for _, _, n in self.floors)

    def summary(self) -> str:
        lines = [f"Plan for {self.task.parts} part(s) of '{self.task.kind}' "
                 f"(stakes ${self.task.stakes_usd:g} each, check: {self.task.check}):"]
        for role, tier, n in self.floors:
            lines.append(f"  {role}: {n} x {tier}")
        lines.append(f"  each part: {self.unit.describe()} (right {self.unit.p_right:.0%})")
        lines.append(f"  expected: ${self.spend:.4f} spent + ${self.loss:.4f} at risk = ${self.spend + self.loss:.4f}, "
                     f"{self.steps} step(s)")
        lines.append(f"  one {ORDER[-1]} agent alone: ${self.baseline_spend:.4f} + ${self.baseline_loss:.4f} = "
                     f"${self.baseline_spend + self.baseline_loss:.4f}, {self.baseline_steps} step(s)")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


SUMMARY_TOKENS = 600  # what a report hands up the floor


def plan(task: Task, rec: Optional[Record] = None, manager: str = "sonnet", executive: str = "opus") -> Plan:
    """The firm for `task`: who does each part, who checks it, how many
    managers on how many floors, what it is expected to cost, and the same
    for one top-tier agent doing it all alone."""
    rec = rec or Record()
    unit = decide(task, rec)
    batch = not task.urgent
    notes: list = []
    floors: list = []
    spend = task.parts * unit.spend
    loss = task.parts * unit.loss
    steps = len(unit.workers) * (2 if unit.reviewer not in (None, "exact") else 1)
    span = max(2, TIERS[manager].desk // (SUMMARY_TOKENS * 2))
    if task.parts > 1:
        # Managers: each takes as many reports as fit on their desk; more
        # floors when one floor of managers would itself be too many.
        levels, width = [], task.parts
        while width > span:
            width = math.ceil(width / span)
            levels.append(width)
        top = TIERS[executive]
        # The planner splits the work, the integrator puts it together: one
        # mistake there costs every part, so it goes to the top tier.
        plan_in, plan_out = task.input_tokens + 2000, 300 * task.parts
        spend += top.cost(plan_in, plan_out, batch=batch)
        spend += top.cost(SUMMARY_TOKENS * min(task.parts, span), task.output_tokens, batch=batch)
        for i, n in enumerate(reversed(levels)):
            floors.append((f"managers (floor {len(levels) - i})", manager, n))
            spend += n * TIERS[manager].cost(SUMMARY_TOKENS * span, SUMMARY_TOKENS, batch=batch)
        floors.insert(0, ("executive (plans and integrates)", executive, 1))
        steps += 2 + len(levels)
    staff = _staff(unit, task.parts)
    floors += [(role, t, n) for role, t, n in staff]
    if unit.workers and TIERS[unit.workers[0]].long_threshold and \
            task.input_tokens > (TIERS[unit.workers[0]].long_threshold or 0):
        notes.append(f"each part's prompt is over {TIERS[unit.workers[0]].long_threshold:,} tokens: split it "
                     f"further to keep {unit.workers[0]} at its lower price")
    if batch:
        notes.append("not urgent: the Batch API halves every price (slower)")
    # Baseline: one top-tier agent, every part in turn, re-reading what it
    # has done so far as cache hits.
    top = TIERS[ORDER[-1]]
    b_spend = 0.0
    for i in range(task.parts):
        seen = i * (task.input_tokens + task.output_tokens)
        b_spend += top.cost(task.input_tokens, task.output_tokens, cached_tokens=seen, batch=batch)
    b_loss = task.parts * (1 - rec.p(ORDER[-1], task.kind)) * task.stakes_usd
    return Plan(task, unit, floors, span, spend, loss, steps, b_spend, b_loss, task.parts, notes)


def _staff(unit: Choice, parts: int) -> list:
    """Staff on the ground floor: the first worker of the cascade for every
    part; the later ones only as often as they are expected to be needed
    (rounded up: someone must be on call)."""
    out = [("staff", unit.workers[0], parts)]
    for t in unit.workers[1:]:
        out.append(("escalation", t, 1))
    if unit.reviewer not in (None, "exact"):
        out.append(("reviewers", unit.reviewer, parts))
    return out


def as_dict(p: Plan) -> dict:
    return {
        "floors": [{"role": r, "tier": t, "model": TIERS[t].model, "headcount": n} for r, t, n in p.floors],
        "each_part": {"workers": list(p.unit.workers), "check": p.unit.reviewer, "p_right": round(p.unit.p_right, 4)},
        "expected": {"spend_usd": round(p.spend, 6), "at_risk_usd": round(p.loss, 6), "steps": p.steps},
        "single_agent": {"spend_usd": round(p.baseline_spend, 6), "at_risk_usd": round(p.baseline_loss, 6),
                         "steps": p.baseline_steps, "tier": ORDER[-1]},
        "span": p.span,
        "notes": p.notes,
        "prices_read": PRICES_READ,
    }


def explain(rec: Record, kinds: Iterable[str]) -> list:
    """The current belief per tier and kind, and how much of it is record."""
    return [{"tier": t, "kind": k, "p_right": round(rec.p(t, k), 4), "on_record": rec.seen(t, k)}
            for k in kinds for t in ORDER]
