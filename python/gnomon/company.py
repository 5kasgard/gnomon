"""A company of models: a firm with an organisation chart, staff, a client,
internal and external records, and personnel decisions taken from the
record.

`gnomon.firm` prices how to staff a piece of work. This module runs the
organisation that does it, on Gnomon's own primitives:

* **The constitution.** A company is founded with positions that keep
  the powers apart (`CONSTITUTION`): the CEO plans and delivers; the Chief
  of Staff allocates work and budget; the Head of People hires and reviews;
  the Head of IT keeps the tools. The Head of Assurance decides whether
  work is accepted and reports to the owners, not to the CEO: the gate lets
  only owners appoint or dismiss it, so production can never overrule the
  check on itself.
* **Staff.** Each hire is a new writer, an instance of a template writer
  for its model tier, appointed to a position by someone above it. Titles,
  not models, are what people hold: any tier can hold any title, and the
  record decides who keeps it. Departments are positions too, created by
  whoever they report to.
* **Records.** The company's compartment is its internal record and its
  memory: work orders, results, memos, decisions, meetings and handoffs.
  Work orders are questions in `firm/<kind>` frames; results answer them;
  assurance gives verdicts. Personnel notes go in `org/<name>/people`, and
  the structure chosen for each order in `org/<name>/structure`, so which
  shapes work can be learned.
* **Clients and subcontractors.** A client is any writer outside the
  company that posts a work order in its compartment (Claude in a chat,
  say). A subcontractor is a writer of another party: its verdicts, unlike
  the company's own, count as independent.
* **Personnel decisions from the record, with care.** Only results judged
  by someone outside the worker's own group count (`firm.outcomes`);
  escalating, asking for help or saying a part could not be done is never
  counted against anyone, so being honest is never the costly choice. A
  review proposes probation, dismissal or promotion by stated thresholds,
  and someone with the authority to act decides. A leaver writes a handoff
  first. Work goes to the holder with the fewest open orders.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import GateError, Memory, claim
from . import firm as _firm
from .session import instance_for

CONSTITUTION = [
    ("CEO", None, "Turns the client's goal into a plan, owns delivery and integration, within policy and budget."),
    ("Head of Assurance", None, "Decides whether work is accepted: exact checks first, independent reviews when "
                                "needed. Answers to the owners, not to the CEO."),
    ("Chief of Staff", "CEO", "Allocates work and budget, schedules, routes escalations, runs the channels."),
    ("Head of People", "Chief of Staff", "Hires, onboards and reviews staff from the record; proposes probation, "
                                         "promotion and dismissal."),
    ("Head of IT", "Chief of Staff", "Keeps the firm's tools: calculators, test runners, web access, checkers."),
]


@dataclass
class Policy:
    """Thresholds for personnel reviews, stated so they can be argued with."""
    min_judged: int = 5  # no judgement on fewer results than this
    probation_below: float = 0.6  # posterior chance of a right result
    dismiss_below: float = 0.4
    promote_above: float = 0.95
    promote_after: int = 20


@dataclass
class Company:
    memory: Memory
    org: str
    compartment: str
    owner: str
    templates: dict = field(default_factory=dict)  # tier -> template writer (with 'spawn')

    # ------------------------------------------------------------ founding
    @classmethod
    def found(cls, memory: Memory, owner: str, org: str, compartment: str,
              templates: Optional[dict] = None) -> "Company":
        """Found the company (or reopen it): the constitutional positions
        are defined by the owner if they do not exist yet."""
        c = cls(memory, org, compartment, owner, dict(templates or {}))
        have = c.positions()
        for title, up, duties in CONSTITUTION:
            if title not in have:
                memory.position(owner, org, title, compartment=compartment, duties=duties, reports_to=up)
        return c

    def positions(self) -> dict:
        st = self.memory.state(compartments=[self.compartment])["state"]
        return {p["title"]: p for p in st["positions"].values() if p["org"] == self.org}

    def appointments(self, active: bool = True) -> dict:
        st = self.memory.state(compartments=[self.compartment])["state"]
        return {k: a for k, a in st["appointments"].items()
                if a["org"] == self.org and (not active or a["ended"] is None)}

    def holders(self, title: str) -> list:
        return sorted(a["holder"] for a in self.appointments().values() if a["title"] == title)

    def titles_of(self, writer: str) -> list:
        return sorted(a["title"] for a in self.appointments().values() if a["holder"] == writer)

    # --------------------------------------------------------------- staff
    def department(self, by: str, title: str, reports_to: str, duties: str) -> None:
        """A new position (a department, a team, a role) under `reports_to`,
        created by its holder or anyone above."""
        self.memory.position(by, self.org, title, compartment=self.compartment, duties=duties, reports_to=reports_to)

    def appoint(self, by: str, title: str, holder: str) -> str:
        return self.memory.appoint(by, self.org, title, holder, compartment=self.compartment)

    def hire(self, by: str, title: str, tier: str, reason: str) -> str:
        """A new member of staff on `tier`, appointed to `title` by `by`
        (who must hold a position above it, or be an owner). Returns the new
        writer's id. The reason is recorded in the people channel."""
        template = self.templates.get(tier)
        if template is None:
            raise GateError("E_ORG", f"no template writer for tier '{tier}'")
        writer = instance_for(self.memory, template, session=f"{self.org}-{uuid.uuid4().hex[:12]}")
        self.appoint(by, title, writer)
        self.note(by, f"Hired {writer} as {title}: {reason}", channel="people")
        return writer

    def offboard(self, by: str, appointment: str, reason: str, handoff_summary: str) -> None:
        """End an appointment; the leaver writes a handoff first, so their
        work in progress is never lost."""
        a = self.appointments().get(appointment)
        if a is None:
            raise GateError("E_REF", f"appointment '{appointment}' is not active")
        self.memory.handoff(a["holder"], frame=f"org/{self.org}/{a['title']}", compartment=self.compartment,
                            summary=handoff_summary)
        self.memory.dismiss(by, appointment, reason)

    # ---------------------------------------------------------- the record
    def note(self, by: str, text: str, channel: str = "general", stance: str = "asserted") -> str:
        """A memo in one of the company's channels (internal record)."""
        return self.memory.remember(by, claim(text, speaker=by, frame=f"org/{self.org}/{channel}",
                                              compartment=self.compartment, stance=stance, truth="true",
                                              origin="manual", confidence=9000 if stance == "decided" else 6000))

    def order(self, by: str, goal: str, kind: str, parent: Optional[str] = None) -> str:
        """A work order (from a client, or a sub-order citing its parent)."""
        about = [(parent, "cites")] if parent else None
        return self.memory.ask(by, goal, frame=f"firm/{kind}", compartment=self.compartment, about=about)

    def deliver(self, worker: str, order: str, kind: str, result: str) -> str:
        """A worker's result, answering its work order."""
        return self.memory.remember(worker, claim(result, speaker=worker, frame=f"firm/{kind}",
                                                  compartment=self.compartment, stance="asserted", truth="true",
                                                  origin="derived", confidence=7000,
                                                  about=[{"claim": order, "relation": "answers"}]))

    def structure(self, by: str, order: str, description: str) -> str:
        """Record the structure chosen for an order (and, in the text, the
        alternatives weighed), so which shapes work can be learned."""
        return self.memory.remember(by, claim(description, speaker=by, frame=f"org/{self.org}/structure",
                                              compartment=self.compartment, stance="decided", truth="true",
                                              origin="manual", confidence=9000,
                                              about=[{"claim": order, "relation": "cites"}]))

    def assignee(self, title: str, open_orders: Optional[dict] = None) -> Optional[str]:
        """The holder of `title` with the fewest open orders (ties: by id)."""
        hs = self.holders(title)
        if not hs:
            return None
        load = open_orders or {}
        return min(hs, key=lambda h: (load.get(h, 0), h))

    # ----------------------------------------------------- personnel review
    def review(self, reader: str, policy: Optional[Policy] = None) -> list:
        """Proposals from the record, one per member of staff with enough of
        it: each with the writer, titles, judged results, the posterior
        chance of a right result (its tier's prior plus its own outcomes),
        and the proposal (`keep`, `probation`, `dismiss` or `promote`).
        Only results judged from outside the worker's group count; nothing
        else (escalations, questions, abstentions) is held against anyone."""
        policy = policy or Policy()
        results, _ = _firm.outcomes(self.memory, reader)
        writers = self.memory.writers(compartments=[self.compartment])
        out = []
        staff = {}
        for a in self.appointments().values():
            staff.setdefault(a["holder"], []).append(a["title"])
        for w, titles in sorted(staff.items()):
            mine = [o for o in results if o["author"] == w]
            tier = _firm.tier_of(writers.get(w, {}).get("model")) if writers.get(w, {}).get("kind") == "model" else None
            a, b = _firm.PRIORS.get(tier, (1, 1)) if tier else (1, 1)
            s = sum(o["success"] for o in mine)
            n = len(mine)
            p = (a + s) / (a + b + n)
            if n < policy.min_judged:
                act = "keep"
            elif p < policy.dismiss_below:
                act = "dismiss"
            elif p < policy.probation_below:
                act = "probation"
            elif n >= policy.promote_after and p >= policy.promote_above:
                act = "promote"
            else:
                act = "keep"
            out.append({"writer": w, "titles": sorted(titles), "tier": tier, "judged": n, "right": s,
                        "p_right": round(p, 4), "proposal": act})
        return out
