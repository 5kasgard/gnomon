"""An alphabet of organisational forms, with dials, grounded in evidence.

A firm of models is not one fixed chart. It is a word spelt from a small
alphabet of forms (split the work, propose independently, select by a
check, route to the expert, take turns, review independently, escalate,
...), and each letter has a size. Which word fits depends on the work: how
much of it can run in parallel, how tightly its parts are coupled, whether
a computer can check it, whether one member is clearly the expert, and
how strong the models are. The dials say how much effort, breadth,
scrutiny, speed and thrift the client wants.

Nothing here is invented from a blank page. Each letter carries what is
known about it, with its source and how strong the evidence is:

* ``measured``: controlled experiments with language-model teams
  (mostly 2026), with the numbers they report;
* ``theory``: an established result from organisation science, distributed
  computing or statistics, whose assumptions are stated;
* ``hypothesis``: a reasonable extrapolation not yet measured for models.

`compose` turns a task profile and dials into a word by explicit rules,
each citing the evidence it rests on, and lists the alternatives it
weighed. The rules are priors, not laws: model bindings change winners
(Chen et al. 2026 found the best placement of accountability flips across
model families), so `Learner` (LinUCB, a contextual bandit) learns from the
firm's own record which of the candidate words actually pays, per kind of
task. The research behind each rule is in `SOURCES`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------- sources

SOURCES = {
    "selforg": ("Dochkina 2026, Drop the Hierarchy and Roles (arXiv 2603.28990)",
                "25,000 runs, 8 models, 4-256 agents: fixed order with self-chosen roles, each agent seeing its "
                "predecessors' finished outputs (Sequential), beat a central coordinator by 14% (p<0.001, "
                "replicated on 3 models) and fully autonomous agents by 44%; below a capability threshold the "
                "effect reverses and rigid structure is better; 64 to 256 agents gave no quality gain at 4.6x cost; "
                "self-organised hierarchies stay 1-2 levels deep."),
    "experts": ("Pappu et al. 2026, Multi-Agent Teams Hold Experts Back (ICML, arXiv 2602.01011)",
                "Self-organising teams fall 6.3-41.1% short of their best member on MMLU-Pro, GPQA, HLE, MATH, "
                "even when told who the expert is: they average expert and non-expert views (integrative "
                "compromise); the gap grows with team size; the same averaging makes them robust to a saboteur."),
    "bottleneck": ("Yu et al. 2026, When Do Multi-Agent Systems Help? (arXiv 2607.16133)",
                   "With unbounded messages a team can simulate one agent; gains come from isolating contexts. "
                   "Splitting helps when a short message suffices for the next step (ALFWorld +0.19, WideSearch "
                   "+0.08) and hurts or reverses when later steps need exact upstream detail (WorkBench negative; "
                   "TravelPlanner hard constraints -0.23 for the strongest model). Stronger models gain less."),
    "tax": ("Ann, Liu and Tan 2026, The Interaction Tax (ICML, arXiv 2608.23541)",
            "On 11 verifier-scored tasks, agents that read each other's full solutions converge within one round "
            "and lose the diversity of mixed model families (debate -0.078, critique -0.035); independent "
            "proposals with selection keep it (diversity +0.19, p<0.001). Critique helps only when the fault is "
            "easy to locate (knapsack feasibility 10/10 vs 2/10) and hurts when it is not (0/10 vs 6/10); "
            "first-round critique degraded graded solutions 57% of the time."),
    "distributed": ("Mieczkowski et al. 2026, Language Model Teams as Distributed Systems (arXiv 2603.12229)",
                    "Speed-ups follow Amdahl's law in the parallel fraction; pre-assigned (central) teams beat "
                    "self-claiming teams (median 1.36x vs 0.88x speed-up); token cost outgrows speed-up on serial "
                    "work (5.8x tokens for 1.1x) and grows with team size when agents negotiate."),
    "codebook": ("Yu et al. 2026, Codebook Agent (arXiv 2609.02264)",
                 "Topologies that succeed collapse to about six distinct graphs however large the codebook; the "
                 "best fixed topology is within 1.4 points of any generated one, but which one wins changes per "
                 "task (up to 4.3 points, 2.9x tokens); choosing from a short list by measured cost cuts tokens "
                 "22-33%. Fewer edges did not mean fewer tokens (r about -0.4)."),
    "imacs": ("Chen et al. 2026, Toward an Organizational Science of Multi-Agent LLM Systems (arXiv 2607.25446)",
              "Keeping who (organisation), how (coordination) and which algorithm apart makes each a variable; a "
              "LinUCB router over task features beat every fixed protocol (0.708 vs 0.669 best fixed, 82% routing "
              "accuracy); accountability placement matters only when the protocol routes the deliverable through "
              "the accountable agent, and its best placement flips across model families."),
    "scaling": ("Li et al. 2026, Scaling Behavior of Single LLM-Driven MAS (arXiv 2606.00655)",
                "Same-model teams follow an inverted U in team size, peaking around 2-5 agents; logic tasks fell "
                "27-33 points from 1 to 8 agents; small models degrade monotonically; the cause is coordination "
                "overhead, not context length."),
    "amdahl": ("Amdahl 1967; Gustafson 1988", "Speed-up with n workers is at most 1/((1-p)+p/n) for parallel "
               "fraction p."),
    "ashby": ("Ashby 1956, requisite variety", "A regulator needs as many distinct responses as the disturbances "
              "it must handle: the alphabet needs enough letters for the work it meets."),
    "galbraith": ("Galbraith 1974; Thompson 1967", "Coordination cost rises with uncertainty and interdependence: "
                  "pooled work needs standards, sequential work needs plans, reciprocal work needs mutual "
                  "adjustment."),
    "mintzberg": ("Mintzberg 1979", "Five coordination mechanisms: direct supervision, standardised work, "
                  "standardised outputs, standardised skills, mutual adjustment."),
    "condorcet": ("Condorcet 1785; Krogh and Vedelsby 1995", "A majority of independent better-than-chance voters "
                  "beats one; ensemble error is mean member error minus diversity, so correlated members add "
                  "little."),
    "conway": ("Conway 1968", "Systems mirror the communication structure of the teams that build them: draw "
               "team boundaries along the work's module boundaries."),
    "goodhart": ("Goodhart 1975; Campbell 1979", "A measure optimised hard stops measuring: whoever is judged "
                 "must not set or grade the measure."),
    "stigmergy": ("Grassé 1959; Heylighen 2016; FunSearch, Romera-Paredes et al. 2024",
                  "Coordination through traces left in a shared medium (scores, notes, failure causes) instead of "
                  "messages; evolutionary search keeps a population diverse by selection, not conversation."),
    "bottled": ("Sonthalia et al. 2026, Agent in a Bottle (arXiv 2610.08775)",
                "Agents given a whole workload of millions of items can build a cheap artifact (a program, a small "
                "trained model) instead of calling the model per item: Opus 5 kept 95% of its per-item F1 at 889x "
                "lower cost on attribute extraction and 82% at 657x on relevance; but 48 of 60 runs fell below "
                "per-item quality and 31 of 60 lost to naive distillation (label with a cheap model, fine-tune a "
                "0.6B student) on the same budget."),
    "paw": ("Zhang et al. 2026, Program-as-Weights (arXiv 2607.02512)",
            "A large model compiles a fuzzy function once into a 23 MB adapter for a 0.6B interpreter, which then "
            "beats prompting a 32B model per input (73.8% vs 68.7%) at a fiftieth of the memory, 30 tokens/s on a "
            "laptop; code written by a model instead reached 36%."),
    "batching": ("Pipal et al. 2026, Classifying one text at a time wastes 80% (arXiv 2604.03684)",
                 "Putting 25-100 items in one request (and up to 10 questions per item) cut cost by over 80% with "
                 "under 2 points of accuracy lost for six of eight models; Claude Haiku lost at most 1.1 points up "
                 "to 1,000 items."),
    "cascade": ("Bouchard 2026, Is Escalation Worth It? (arXiv 2605.06350); Wang et al. 2026, Dynamic LLM "
                "Routers are Often Misguided (arXiv 2610.02762)",
                "A cheap model escalating to a strong one beat the best single model at equal budget by 2-8 "
                "points, but a third tier added nothing in 118 of 135 comparisons; no commercial router beat "
                "random choice between two well-chosen models at matched cost: the roster matters more than the "
                "router."),
    "sampling": ("Mirzaei 2026, Sample More, Reflect Less (arXiv 2607.28576); Sunkaraneni et al. 2026, "
                 "Inference-Time Amplification of Weak Reasoning Models (arXiv 2605.14163)",
                 "At equal tokens, no self-critique, reflection or debate method beat plain repeated sampling with "
                 "a vote; every method where the model judged its own work lost (by 4-14 points). Eight samples "
                 "from a small model plus a good selector recovered 78-85% of the gap to the best-of-eight oracle; "
                 "a computer check (tests, a plan checker) matched a learned selector at 6x fewer tokens."),
    "floor": ("Gnomon bench/floor (this repository, 2026-10-21; protocol in bench/floor/PROTOCOL.md)",
              "On 5,574 real SMS messages a linear model on 1,000 human labels matched Claude Haiku on a 300-item "
              "probe (99.7% each) at about a cent per million items against $9.81; with the escalation share "
              "chosen on half the probe and scored on the other, it escalated nothing. On 10,000 Yelp reviews "
              "(stars 1-5) no cheap worker came close (48.8% vs Haiku's 66%) and the floor sent everything up. On "
              "positive/negative sentiment the floor matched Haiku at a seventh of the cost, or fell 2 points "
              "short, depending on how Haiku's '3 stars' answers are read. Swarms of dumb nodes (300 boosted "
              "stumps; Mycel's nuclei) never beat one linear model on text."),
    "hayek": ("Hayek 1945; Vickrey 1961", "Prices and bids gather knowledge no planner has; a second-price "
              "auction makes bidding one's true estimate the best strategy."),
}


@dataclass(frozen=True)
class Evidence:
    source: str  # key in SOURCES
    finding: str
    strength: str  # measured | theory | hypothesis


@dataclass(frozen=True)
class Letter:
    code: str
    name: str
    does: str
    helps: str
    hurts: str
    evidence: tuple
    mechanism: str  # Mintzberg's coordination mechanism it relies on


def _e(source, finding, strength="measured"):
    return Evidence(source, finding, strength)


ALPHABET = {
    L.code: L for L in [
        Letter("S", "Solo", "One agent does the part with the whole context.",
               "Tightly coupled work where later steps need exact earlier detail; strong models.",
               "Long, separable work that drowns one context; independent checks needed.",
               (_e("bottleneck", "strong models lose when coupled work is split (-0.23 on hard constraints)"),
                _e("experts", "teams fell 6-41% short of their best member")), "standardised skills"),
        Letter("D", "Divide", "Split into parts with isolated contexts; each passes on a short, complete brief.",
               "Parallel work, or steps whose hand-over fits in a short message.",
               "Steps that need exact upstream state (ids, outputs) the brief would drop.",
               (_e("bottleneck", "+0.08 to +0.19 when a short relay suffices; negative when it cannot"),
                _e("amdahl", "speed-up bounded by the serial fraction", "theory"),
                _e("conway", "cut along the work's own module boundaries", "theory")), "standardised outputs"),
        Letter("C", "Coordinate", "A planner assigns the parts up front; workers do not negotiate.",
               "Many independent parts; weaker models; anything where self-claiming would collide.",
               "Open-ended work strong models could organise better themselves.",
               (_e("distributed", "pre-assigned 1.36x vs self-claimed 0.88x median speed-up"),
                _e("selforg", "below the capability threshold, rigid structure wins")), "direct supervision"),
        Letter("Q", "Queue", "Agents take turns in a fixed order, each choosing its own role after reading what "
                             "the earlier ones produced, or abstaining.",
               "Open-ended or multi-domain work with strong models.",
               "Weak models (they over-abstain or lose the thread); long chains (latency grows with n).",
               (_e("selforg", "+14% over a coordinator on 3 models, +44% over free-for-all"),),
               "mutual adjustment"),
        Letter("P", "Propose", "Several agents (ideally of different model families) work independently; no one "
                               "sees another's solution.",
               "A check can pick the best; models differ in where they fail.",
               "Without a way to choose, extra proposals only cost.",
               (_e("tax", "independent proposals keep diversity (+0.19); reading full solutions erases it"),
                _e("condorcet", "independent, diverse members beat one", "theory")), "standardised outputs"),
        Letter("V", "Verify-select", "A computer check (tests, arithmetic, a validator) picks among proposals.",
               "Anything with an exact check.", "Nothing, when the check is exact; a weak check is gamed.",
               (_e("goodhart", "the check must not be set by those it judges", "theory"),
                _e("tax", "selection kept the gains; synthesis copied the best proposal 80% of the time")),
               "standardised outputs"),
        Letter("A", "Aggregate", "One agent reads independent proposals and writes the answer.",
               "Open-ended writing where parts of several drafts are useful.",
               "Verifiable work (it mostly copies the best anyway).",
               (_e("tax", "MoA was the only interacting form that kept a positive gain with mixed models"),),
               "standardised outputs"),
        Letter("R", "Route", "Send the part to the member who is clearly expert in it, and defer to them.",
               "Known expertise asymmetry (a specialist, a stronger tier, a tool).",
               "No one is clearly better: routing becomes guessing.",
               (_e("experts", "deliberation averages the expert away; deference correlates with accuracy "
                              "(r = -0.44 to -0.68 with the gap)"),), "standardised skills"),
        Letter("K", "Critique-repair", "A reviewer names a concrete fault and the author repairs it.",
               "Faults a checker or reviewer can point to (a violated constraint, a failing test).",
               "Vague faults: critique steers solutions toward the trivial (57% regressions).",
               (_e("tax", "knapsack feasibility 10/10 with critique vs 2/10; 0/10 vs 6/10 when faults are "
                          "hard to locate"),
                _e("sampling", "self-critique lost to more samples at equal tokens; a computer check does not")),
               "mutual adjustment"),
        Letter("B", "Debate", "Agents exchange and argue full answers over rounds.",
               "Same-model teams on factual questions; resisting a manipulator.",
               "Mixed model families (diversity erased in one round); expertise asymmetry.",
               (_e("tax", "debate -0.078 with mixed families, +0.012 with one family"),
                _e("experts", "consensus filters a saboteur but dilutes the expert")), "mutual adjustment"),
        Letter("M", "Market", "Members bid for parts with their own estimate of cost and chance; the best "
                              "expected value wins; abstaining is free.",
               "Heterogeneous members whose fit for a part is better known to them than to a planner.",
               "Members who cannot estimate their own competence.",
               (_e("hayek", "bids gather dispersed knowledge", "theory"),
                _e("selforg", "voluntary abstention (8.6% for Claude) rose with quality", "measured")),
               "mutual adjustment"),
        Letter("G", "Ground (stigmergy)", "Workers coordinate through the shared record: scores, notes and "
                                          "failure causes, never each other's full solutions.",
               "Long searches with many attempts; keeping exploration diverse.",
               "Work that needs a single integrated plan.",
               (_e("stigmergy", "selection over a shared population keeps diversity", "theory"),
                _e("tax", "low-bandwidth signals proposed as the fix for the interaction tax", "hypothesis")),
               "standardised outputs"),
        Letter("E", "Evolve", "A population of attempts, scored, the best mutated over generations.",
               "Optimisation with a graded score and budget for many attempts.",
               "Pass/fail work or small budgets.",
               (_e("stigmergy", "FunSearch and AlphaEvolve find new results this way", "measured"),
                _e("tax", "verifier-guided evolution was competitive with every interacting form")),
               "standardised outputs"),
        Letter("H", "Hierarchy", "Managers over workers, in floors, for many parts.",
               "More parts than one planner can brief or integrate (beyond its context).",
               "Few parts: each level adds a planner's cost and a relay's loss.",
               (_e("selforg", "self-organised teams settle at 1-2 levels; deeper for harder work"),
                _e("bottleneck", "each relay can lose information")), "direct supervision"),
        Letter("X", "Escalate", "Cheapest capable tier first; on a failed check, the next tier up.",
               "A check that can tell failure (exact, or a review).", "No check: failures go unseen.",
               (_e("condorcet", "a cascade's expected cost falls when early tiers usually succeed", "theory"),),
               "standardised outputs"),
        Letter("T", "Toolmaker (bottle)", "A capable model builds a cheap artifact once (a program, a small "
                                          "trained model, an adapter) that then does the repetitive work.",
               "Many items under one instruction: the build cost is spread over all of them.",
               "Few items; open-ended work; when the artifact cannot be checked against labels.",
               (_e("bottled", "95% of quality at 889x lower cost at best; most agents lost a quarter or more"),
                _e("paw", "a compiled 0.6B worker beat prompting a 32B model per input"),
                _e("floor", "on spam, a program and a linear model matched the model's accuracy")),
               "standardised skills"),
        Letter("F", "Floor", "Many cheap workers (code, small models, the cheapest tier) answer what they are "
                             "confident about; the rest go up to one capable tier.",
               "High-volume, narrow work where confidence separates easy items from hard ones.",
               "Fuzzy work where the cheap worker's confidence does not track its errors; more than two tiers.",
               (_e("cascade", "two tiers gain 2-8 points over one; a third adds nothing on average"),
                _e("floor", "spam: matched the model for almost nothing; reviews: savings only in proportion to loss")),
               "standardised outputs"),
        Letter("N", "Batch", "Many items, and several questions about each, in one request.",
               "Classification and extraction at volume.", "Long items, or tasks whose items interfere.",
               (_e("batching", "80% cheaper, under 2 points lost up to 100 items per request"),),
               "standardised work"),
        Letter("I", "Independent assurance", "Someone outside the workers' group judges the result; reviewers "
                                            "from distinct groups, majority rule.",
               "Anything that matters, and anything without an exact check.",
               "Nothing, except cost.",
               (_e("goodhart", "the judged must not judge themselves", "theory"),
                _e("condorcet", "independent reviewers, not copies of one model, make a majority meaningful",
                   "theory")), "standardised outputs"),
    ]
}


# ----------------------------------------------------------- the problem

TIERS = ("haiku", "sonnet", "opus", "fable")
STRONG = {"sonnet", "opus", "fable"}  # above the self-organisation threshold (selforg: Sonnet was)


@dataclass(frozen=True)
class Profile:
    """What the work is like. All fields have neutral defaults."""
    parts: int = 1  # independent parts
    parallel: float = 1.0  # fraction of the work that can run at once (Amdahl's p)
    coupling: str = "low"  # low: a short brief carries what the next step needs; high: it needs exact detail
    check: str = "none"  # exact | review | none
    locatable: bool = False  # a failed check names the fault (a failing test, a broken constraint)
    expert: bool = False  # one member is clearly best at this part
    open_ended: bool = False  # no single right answer (design, writing, planning)
    adversarial: bool = False  # conflicting stakeholders or a possible saboteur
    graded: bool = False  # the check gives a score, not pass/fail (optimisation)
    tier: str = "sonnet"  # the strongest tier available to the firm
    families: int = 1  # distinct model families available (diversity)
    context_parts: int = 40  # parts one planner can brief and integrate
    repeats: int = 1  # items that share one instruction (a workload: classify these 100,000 messages)
    narrow: bool = True  # with repeats: one fixed question per item, so cheap workers can learn it

    def __post_init__(self):
        if not isinstance(self.parts, int) or isinstance(self.parts, bool) or not 1 <= self.parts <= 1_000_000:
            raise ValueError("parts must be a whole number from 1 to 1000000")
        if isinstance(self.parallel, bool) or not isinstance(self.parallel, (int, float)) \
                or not 0.0 <= self.parallel <= 1.0:
            raise ValueError("parallel must be a number from 0 to 1")
        if not isinstance(self.repeats, int) or isinstance(self.repeats, bool) or not 1 <= self.repeats <= 10**12:
            raise ValueError("repeats must be a whole number from 1 to 10^12")
        for name in ("locatable", "expert", "open_ended", "adversarial", "graded", "narrow"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be true or false")
        if self.coupling not in ("low", "high"):
            raise ValueError("coupling must be low or high")
        if self.check not in ("exact", "review", "none"):
            raise ValueError("check must be exact, review or none")
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {', '.join(TIERS)}")
        for name, lo in (("families", 1), ("context_parts", 2)):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= 1000:
                raise ValueError(f"{name} must be a whole number from {lo} to 1000")

    def features(self) -> list:
        """The context the learner sees: bounded, interpretable numbers."""
        return [1.0, float(self.parallel), float(self.coupling == "high"), float(self.check == "exact"),
                float(self.check == "review"), float(self.expert), float(self.open_ended),
                float(self.tier in STRONG), min(math.log10(self.parts) / 3, 1.0), float(self.adversarial),
                min(math.log10(self.repeats) / 6, 1.0)]


@dataclass(frozen=True)
class Dials:
    """How the client wants resources spent, each from 0 to 1.

    effort: how many attempts and members; breadth: how much model
    diversity; scrutiny: how much independent review; speed: how much to
    pay for parallelism; thrift: how much cost counts against quality."""
    effort: float = 0.5
    breadth: float = 0.5
    scrutiny: float = 0.5
    speed: float = 0.5
    thrift: float = 0.5

    def __post_init__(self):
        for k in ("effort", "breadth", "scrutiny", "speed", "thrift"):
            v = getattr(self, k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
                raise ValueError(f"{k} must be a number from 0 to 1")


# ------------------------------------------------------------ the answer

@dataclass
class Stage:
    letter: str
    size: int
    why: str
    sources: tuple

    def spell(self) -> str:
        return self.letter + (f"[{self.size}]" if self.size > 1 else "")


@dataclass
class Design:
    stages: list
    calls: float  # expected model calls per part, before escalation
    rounds: int  # serial steps (latency)
    workers: int  # parts in flight at once
    considered: list = field(default_factory=list)  # (word, why not)
    warnings: list = field(default_factory=list)

    @property
    def word(self) -> str:
        return "·".join(s.spell() for s in self.stages)

    @property
    def shape(self) -> str:
        """The word without sizes: what the learner tells apart (sizes
        follow the dials, and keying on them would split the evidence)."""
        return "·".join(s.letter for s in self.stages)

    def as_dict(self) -> dict:
        return {
            "word": self.word,
            "stages": [{"letter": s.letter, "name": ALPHABET[s.letter].name, "size": s.size, "why": s.why,
                        "sources": [SOURCES[k][0] for k in s.sources]} for s in self.stages],
            "calls_per_part": round(self.calls, 2), "rounds": self.rounds, "workers": self.workers,
            "considered": [{"word": w, "why_not": y} for w, y in self.considered],
            "warnings": self.warnings,
        }

    def summary(self) -> str:
        lines = [f"Form: {self.word}  (≈{self.calls:.1f} model calls per part, {self.rounds} serial round(s), "
                 f"{self.workers} at once)"]
        for s in self.stages:
            lines.append(f"  {s.spell():<6} {ALPHABET[s.letter].name}: {s.why}")
        for w, y in self.considered:
            lines.append(f"  not {w}: {y}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


def amdahl(p: float, n: int) -> float:
    return 1.0 / ((1.0 - p) + p / n)


def workers_for(p: float, parts: int, speed: float) -> int:
    """The fewest workers that reach a share of the best possible speed-up:
    60% of it at speed 0, 95% at speed 1 (Amdahl). Past that knee, extra
    workers mostly add cost (distributed: tokens outgrow speed-up)."""
    if parts <= 1 or p <= 0:
        return 1
    best = amdahl(p, parts)
    want = 1 + (best - 1) * (0.6 + 0.35 * speed)
    n = 1
    while n < parts and amdahl(p, n) < want:
        n += 1
    return n


def compose(profile: Profile, dials: Optional[Dials] = None) -> Design:
    """Spell a form for `profile` under `dials`, by stated rules, each
    citing its evidence. Returns the design with the alternatives weighed."""
    p, d = profile, dials or Dials()
    strong = p.tier in STRONG
    stages: list = []
    considered: list = []
    warnings: list = []
    calls = 0.0
    rounds = 0

    # 0. A workload: many items under one instruction.
    if p.repeats >= 1000 and p.narrow and not p.open_ended:
        if p.repeats >= 10_000:
            stages.append(Stage("T", 1, f"{p.repeats:,} items: build a program or small model once and let it do "
                                        "the bulk; label a probe of a few hundred items with the model first, "
                                        "and keep the artifact only if it holds up on them",
                                ("bottled", "paw", "floor")))
            considered.append(("per-item model calls", "cost grows with every item; an artifact's barely does"))
        stages.append(Stage("F", 2, "the cheap worker answers what it is confident about; its least confident "
                                    "items go to one capable tier", ("cascade", "floor")))
        considered.append(("a third tier", "added nothing on average in 118 of 135 comparisons"))
        b = 25 if d.thrift < 0.7 else 100
        stages.append(Stage("N", b, f"{b} items per request to the capable tier", ("batching",)))
        warnings.append("a floor pays only where the cheap worker is nearly as good and its confidence tracks "
                        "its errors: on spam it matched the model for almost nothing; on five-star reviews it "
                        "saved only what it gave up in quality. Measure on the probe first")
        calls += 0.2  # share of items expected to reach the capable tier (measured: 10-30% on easy work)

    # 1. Shape across parts: split, and who assigns.
    workers = workers_for(p.parallel, p.parts, d.speed) if p.coupling == "low" else 1
    if p.parts > 1:
        if p.coupling == "high" and strong:
            stages.append(Stage("S", 1, "the parts depend on exact detail from each other, and a strong model "
                                        "keeps it all in one context", ("bottleneck",)))
            considered.append(("D", "a short brief would drop detail the next part needs"))
            rounds += p.parts
        else:
            if p.coupling == "high":
                warnings.append("coupled work split across a weaker tier: briefs must carry exact state "
                                "(ids, outputs), or results will drift")
            if p.parts > p.context_parts:
                floors = math.ceil(math.log(p.parts) / math.log(p.context_parts))
                stages.append(Stage("H", floors, f"{p.parts} parts exceed what one planner can brief "
                                                 f"({p.context_parts}); {floors} floors of managers",
                                    ("selforg", "bottleneck")))
                calls += floors / max(p.context_parts, 1)
            stages.append(Stage("C", 1, "parts assigned up front, not claimed by negotiation",
                                ("distributed",)))
            considered.append(("self-claim", "self-claiming teams ran slower (0.88x vs 1.36x) at more tokens"))
            stages.append(Stage("D", workers, f"{workers} worker(s) at once reach the knee of Amdahl's curve "
                                              f"for p={p.parallel:g}", ("amdahl", "distributed", "bottleneck")))
            rounds += math.ceil(p.parts / workers)
    else:
        rounds += 1

    # 2. How each part is produced.
    if p.expert:
        stages.append(Stage("R", 1, "a member is clearly expert: route to it and defer, rather than "
                                    "deliberate", ("experts",)))
        considered.append(("B", "deliberation averages the expert away (6-41% below the best member)"))
        calls += 1
    elif p.check == "exact" and p.graded and d.effort >= 0.6:
        gens = 2 + round(d.effort * 4)
        stages.append(Stage("E", gens, f"a graded exact score and effort {d.effort:g}: a population "
                                       f"selected over {gens} generations", ("stigmergy", "tax")))
        stages.append(Stage("G", 1, "attempts share scores and failure causes through the record, never "
                                    "full solutions", ("tax", "stigmergy")))
        calls += 4 * gens
        rounds += gens
    elif p.check == "exact":
        k = 1 + round(d.effort * 5)
        stages.append(Stage("P", k, f"{k} independent attempt(s)"
                                    + (", across model families" if p.families > 1 and d.breadth >= 0.5 else ""),
                            ("tax", "condorcet")))
        stages.append(Stage("V", 1, "the exact check chooses; no one reads another's solution", ("tax",)))
        considered.append(("B", "full-solution exchange erases diversity within one round"))
        calls += k
        if p.locatable:
            stages.append(Stage("K", 1, "a failed check names the fault, so one repair round is worth it",
                                ("tax",)))
            calls += 0.5
            rounds += 1
    elif p.open_ended or p.adversarial:
        n = min(4, 2 + round(d.effort * 2))
        if strong:
            stages.append(Stage("Q", n, f"{n} agents in a fixed order, each reading the others' finished work "
                                        "and choosing its own role or abstaining", ("selforg",)))
            considered.append(("C", "a coordinator's plan scored 14% lower with strong models"))
            calls += n
            rounds += n
        else:
            stages.append(Stage("C", n, f"{n} fixed roles: below the capability threshold structure helps",
                                ("selforg", "scaling")))
            considered.append(("Q", "weaker models lose quality when left to organise themselves"))
            calls += n
            rounds += 1
        if p.open_ended and d.breadth >= 0.5 and p.families > 1 and not p.adversarial:
            stages.append(Stage("A", 1, "a draft from another model family, written without seeing the queue's, merged in by one writer",
                                ("tax",)))
            calls += 1
        considered.append(("more than 4 voices", "returns peak at 2-5 agents and the expert is diluted"))
    else:
        k = 1 + round(d.effort * 2) if p.check == "review" else 1
        stages.append(Stage("P", k, "independent attempt(s); the review below picks" if k > 1 else
                                    "one attempt", ("experts", "tax")))
        considered.append(("B", "teams deliberating fell short of their best member"))
        calls += k

    # 3. Cost control.
    if p.check in ("exact", "review") and d.thrift >= 0.3 and not p.expert:
        stages.append(Stage("X", 1, "cheapest capable tier first; a failed check moves the part up a tier",
                            ("condorcet",)))

    # 4. Assurance from outside the workers' group.
    if p.check == "exact":
        stages.append(Stage("I", 1, "the check is run by a tool outside the workers' group", ("goodhart",)))
    elif p.check == "review" or d.scrutiny >= 0.5 or p.adversarial:
        r = 3 if d.scrutiny >= 0.75 or p.adversarial else 1
        stages.append(Stage("I", r, f"{r} reviewer(s) from distinct groups"
                                    + (", majority rule" if r > 1 else ""), ("goodhart", "condorcet")))
        calls += r
    else:
        warnings.append("no check and little scrutiny: errors will go unseen")

    if not strong and p.open_ended:
        warnings.append("self-organisation needs a capable model; the evidence is for Sonnet-class and above")
    considered.insert(0, ("S (one agent)" if p.parts == 1 else "one agent for everything",
                          "the baseline every form must beat on cost and quality"))
    return Design(stages, calls, max(rounds, 1), workers, considered, warnings)


def candidates(profile: Profile, dials: Optional[Dials] = None) -> list:
    """A short list of designs to choose between (Codebook: the useful
    space is a handful of forms): the composed one first, then the same
    work at lower and higher effort, and one agent alone."""
    d = dials or Dials()
    out, seen = [], set()
    for dd in (d, Dials(max(d.effort - 0.4, 0), d.breadth, d.scrutiny, d.speed, d.thrift),
               Dials(min(d.effort + 0.4, 1), d.breadth, d.scrutiny, d.speed, d.thrift)):
        x = compose(profile, dd)
        if x.word not in seen:
            seen.add(x.word)
            out.append(x)
    solo = Design([Stage("S", 1, "one agent, no organisation", ("experts",))], 1.0, max(p_rounds(profile), 1), 1)
    if solo.word not in seen:
        out.append(solo)
    return out


def p_rounds(profile: Profile) -> int:
    return profile.parts


# ------------------------------------------------------------- learning

def reward(quality: float, cost_usd: float, budget_usd: float, thrift: float = 0.5) -> float:
    """Quality in [0, 1] less cost as a share of budget, weighted by thrift
    (the cost-adjusted reward of IMACS and Codebook Agent)."""
    if budget_usd <= 0:
        raise ValueError("budget_usd must be positive")
    return float(quality) - thrift * min(cost_usd / budget_usd, 2.0)


def _inverse(m: list) -> list:
    """Gauss-Jordan inverse of a small positive-definite matrix."""
    n = len(m)
    a = [list(row) + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        piv = a[c][c]
        if abs(piv) < 1e-12:
            raise ValueError("singular matrix")
        a[c] = [v / piv for v in a[c]]
        for r in range(n):
            if r != c and a[r][c] != 0.0:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [row[n:] for row in a]


class Learner:
    """LinUCB over forms: per form, a ridge regression of reward on the
    task's features, chosen by its upper confidence bound (Li et al. 2010;
    used for protocol routing by IMACS). Untried forms are explored only
    when the bound says the chance is worth it; with no data the composed
    form (first candidate) wins ties.

    `decay` below 1 forgets old evidence (discounted LinUCB, Russac et al.
    2019): every update shrinks all forms' evidence by that factor, so a
    form that used to pay but stopped (a new model, a changed workload) is
    noticed. It is a trade, measured in tests/test_forms_stress.py (four
    task types, four forms, the best and worst forms swapping at round
    600): in the 100 rounds after the change, regret per round fell from
    0.13-0.19 (decay 1) to 0.08-0.09 (decay 0.995); in a world that does not
    change it rose from 0.01 to 0.04. The default is 1 (no forgetting):
    change detection (below) does as well after a change without the cost."""

    def __init__(self, alpha: float = 0.6, dim: int = 11, decay: float = 1.0, ridge: float = 1.0,
                 key: str = "shape", detect: float = 8.0):
        if not 0.5 <= decay <= 1.0:
            raise ValueError("decay must be from 0.5 to 1")
        if key not in ("shape", "word"):
            raise ValueError("key must be shape or word")
        if detect < 0:
            raise ValueError("detect must be 0 (off) or positive")
        if not ridge > 0:
            raise ValueError("ridge must be positive")
        self.alpha, self.dim, self.decay, self.ridge, self.key = alpha, dim, decay, ridge, key
        # Change detection (Page-Hinkley on each form's prediction errors,
        # in units of their own running spread): when a form's rewards drift
        # from what it predicts by more than `detect` standard deviations
        # (cumulative), its evidence is reset to the prior and re-learnt.
        # 0 turns it off. Measured in simulation on seeds not used to choose
        # it (10 worlds per noise level 0.05, 0.15, 0.3): at 8, regret in a
        # world that never changes was identical to no detection (0.004,
        # 0.007, 0.019; at most 0.2 false alarms per 1,200 rounds); in the
        # 100 rounds after the best and worst forms swap it fell from 0.13,
        # 0.17, 0.20 to 0.08, 0.11, 0.16. Plain forgetting (decay 0.995)
        # recovered about as fast but cost 0.036-0.046 when nothing changed.
        self.detect = detect
        self.alarms = 0
        self.arms: dict = {}  # word -> {"A": [[...]], "b": [...], "n": int}
        self._inv: dict = {}

    def _arm(self, word: str) -> dict:
        if word not in self.arms:
            self.arms[word] = {"A": [[self.ridge * float(i == j) for j in range(self.dim)] for i in range(self.dim)],
                               "b": [0.0] * self.dim, "n": 0}
        return self.arms[word]

    @staticmethod
    def _mv(m, v):
        return [sum(a * b for a, b in zip(row, v)) for row in m]

    def _A_inv(self, word: str) -> list:
        if word not in self._inv:
            self._inv[word] = _inverse(self._arm(word)["A"])
        return self._inv[word]

    def score(self, word: str, x: list) -> float:
        a = self._arm(word)
        inv = self._A_inv(word)
        theta = self._mv(inv, a["b"])
        mean = sum(t * xi for t, xi in zip(theta, x))
        width = math.sqrt(max(sum(xi * yi for xi, yi in zip(x, self._mv(inv, x))), 0.0))
        return mean + self.alpha * width

    def name(self, design) -> str:
        """The arm a design counts under (a string is taken as given)."""
        return design if isinstance(design, str) else getattr(design, self.key)

    def choose(self, profile: Profile, designs: list) -> Design:
        x = profile.features()
        scored = [(self.score(self.name(d), x) + (1e-9 if i == 0 else 0.0), -i, d) for i, d in enumerate(designs)]
        return max(scored, key=lambda t: (t[0], t[1]))[2]

    def update(self, profile: Profile, design, r: float) -> None:
        """Record reward `r` for `design` (a Design, or its arm name)."""
        word = self.name(design)
        x = profile.features()
        self._arm(word)
        if self.detect > 0:
            a0 = self.arms[word]
            if a0["n"] >= 5:
                inv = self._A_inv(word)
                e = r - sum(t * xi for t, xi in zip(self._mv(inv, a0["b"]), x))
                # Page-Hinkley on prediction errors scaled by their own
                # running spread, so the threshold means the same at any
                # noise level: `detect` is in standard deviations.
                ph = a0.setdefault("ph", {"n": 0, "mean": 0.0, "m2": 0.0, "up": 0.0, "lo": 0.0, "dn": 0.0,
                                          "hi": 0.0})
                ph["n"] += 1
                d0 = e - ph["mean"]
                ph["mean"] += d0 / ph["n"]
                ph["m2"] += d0 * (e - ph["mean"])
                sd = math.sqrt(ph["m2"] / (ph["n"] - 1)) if ph["n"] > 1 else 0.0
                if ph["n"] >= 10 and sd > 0:
                    z = e / max(sd, 1e-3)
                    ph["up"] += z - 0.5
                    ph["lo"] = min(ph["lo"], ph["up"])
                    ph["dn"] += z + 0.5
                    ph["hi"] = max(ph["hi"], ph["dn"])
                    if ph["up"] - ph["lo"] > self.detect or ph["hi"] - ph["dn"] > self.detect:
                        self.arms[word] = {"A": [[self.ridge * float(i == j) for j in range(self.dim)]
                                                 for i in range(self.dim)], "b": [0.0] * self.dim, "n": 0}
                        self._inv.pop(word, None)
                        self.alarms += 1
        g = self.decay
        for w, a in self.arms.items():
            if g < 1.0:
                # Discount toward the prior, so the matrix stays invertible.
                a["A"] = [[g * a["A"][i][j] + (1 - g) * self.ridge * float(i == j) for j in range(self.dim)]
                          for i in range(self.dim)]
                a["b"] = [g * v for v in a["b"]]
                self._inv.pop(w, None)
        a = self.arms[word]
        a["A"] = [[a["A"][i][j] + x[i] * x[j] for j in range(self.dim)] for i in range(self.dim)]
        a["b"] = [bi + r * xi for bi, xi in zip(a["b"], x)]
        a["n"] += 1
        self._inv.pop(word, None)

    def to_dict(self) -> dict:
        return {"alpha": self.alpha, "dim": self.dim, "decay": self.decay, "ridge": self.ridge, "key": self.key,
                "detect": self.detect, "arms": self.arms}

    @classmethod
    def from_dict(cls, d: dict) -> "Learner":
        lr = cls(d["alpha"], d["dim"], d.get("decay", 1.0), d.get("ridge", 1.0), d.get("key", "word"),
                 d.get("detect", 0.0))
        for w, a in d["arms"].items():
            if "A_inv" in a:  # an older save
                a = {"A": _inverse(a["A_inv"]), "b": a["b"], "n": a["n"]}
            lr.arms[w] = a
        return lr
