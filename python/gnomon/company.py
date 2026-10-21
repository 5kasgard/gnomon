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
  only the founding party's owners and governors appoint or dismiss it, so
  no one in production can remove the check on themselves. (Positions give
  no power over verdicts: who may verify is set by registration.)
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

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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
class Role:
    """A role from a handbook: a title, its duties in one line, the tier its
    author suggests, and the tools it uses."""
    title: str
    duties: str
    tier: Optional[str] = None
    tools: tuple = ()


def roles_from_agent_files(directory: str) -> list:
    """Roles read from agent definition files (Markdown with a front matter
    of `name`, `description`, `model`, `tools`, as in Claude Code's
    `.claude/agents` and collections such as ECC). Files without a name and
    a description are skipped."""
    out = []
    for f in sorted(Path(directory).glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"---\n(.*?)\n---", text, re.S)
        if not m:
            continue
        meta = {}
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
        if not meta.get("name") or not meta.get("description"):
            continue
        duties = " ".join(meta["description"].split())
        if len(duties) > 480:
            duties = duties[:477] + "..."
        tier = meta.get("model", "").lower() or None
        tools = tuple(t.strip() for t in meta.get("tools", "").split(",") if t.strip())
        title = meta["name"].replace("-", " ").strip().title()[:64]
        out.append(Role(title, duties, tier if tier in ("haiku", "sonnet", "opus", "fable") else None, tools))
    return out


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

    def adopt_roles(self, by: str, reports_to: str, roles: list) -> list:
        """Positions for `roles` (see `roles_from_agent_files`) under
        `reports_to`, skipping titles that exist. Returns the titles made."""
        have = self.positions()
        made = []
        for r in roles:
            if r.title not in have:
                self.department(by, r.title, reports_to, r.duties)
                made.append(r.title)
        return made

    def may_act(self, by: str, title: str) -> bool:
        """Whether `by` may appoint to `title` or end an appointment to it,
        by the gate's rule: an owner or governor of the party that founded
        the company, or a holder of a position above `title`."""
        st = self.memory.state(compartments=[self.compartment])["state"]
        w = st["writers"].get(by)
        if w is None:
            return False
        pos = {p["title"]: p for p in st["positions"].values() if p["org"] == self.org}
        if title not in pos:
            return False
        if any(c in ("owner", "govern") for c in w["capabilities"]):
            # The company's owner founded it, so its party governs it.
            if st["writers"].get(self.owner, {}).get("party") == w["party"]:
                return True
        held = {a["title"] for a in st["appointments"].values()
                if a["org"] == self.org and a["holder"] == by and a["ended"] is None}
        up = pos[title].get("reports_to")
        seen = set()
        while up and up not in seen:
            if up in held:
                return True
            seen.add(up)
            up = pos.get(up, {}).get("reports_to")
        return False

    def appoint(self, by: str, title: str, holder: str) -> str:
        return self.memory.appoint(by, self.org, title, holder, compartment=self.compartment)

    def hire(self, by: str, title: str, tier: str, reason: str) -> str:
        """A new member of staff on `tier`, appointed to `title` by `by`
        (who must hold a position above it, or be an owner). Returns the new
        writer's id. The reason is recorded in the people channel."""
        template = self.templates.get(tier)
        if template is None:
            raise GateError("E_ORG", f"no template writer for tier '{tier}'")
        if not self.may_act(by, title):  # before anyone is registered
            raise GateError("E_ORG", f"'{by}' may not appoint to '{title}'")
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
        # Checked first, so a refused dismissal leaves no handoff behind.
        if by != a["holder"] and not self.may_act(by, a["title"]):
            raise GateError("E_ORG", f"'{by}' may not end this appointment")
        self.memory.handoff(a["holder"], frame=f"org/{self.org}/{a['title']}", compartment=self.compartment,
                            summary=handoff_summary)
        self.memory.dismiss(by, appointment, reason)

    # ---------------------------------------------------------- the record
    def note(self, by: str, text: str, channel: str = "general", stance: str = "asserted") -> str:
        """A memo in one of the company's channels (internal record)."""
        return self.memory.remember(by, claim(text, speaker=by, frame=f"org/{self.org}/{channel}",
                                              compartment=self.compartment, stance=stance, truth="true",
                                              origin="manual", confidence=9000 if stance == "decided" else 6000))

    def journal(self, writer: str, text: str) -> str:
        """An entry in a member's own working log (`org/<name>/journal/<writer>`):
        visible inside the company, as a colleague's notebook would be."""
        return self.note(writer, text, channel=f"journal/{writer}")

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
        """The holder of `title` with the fewest open orders (ties: by id).
        Without `open_orders`, the one with the fewest results delivered in
        the company so far, so work is spread."""
        hs = self.holders(title)
        if not hs:
            return None
        if open_orders is None:
            st = self.memory.state(compartments=[self.compartment])["state"]
            open_orders = {}
            for c in st["claims"].values():
                if c["body"].get("frame", "").startswith("firm/") and c["author"] in hs:
                    open_orders[c["author"]] = open_orders.get(c["author"], 0) + 1
        return min(hs, key=lambda h: (open_orders.get(h, 0), h))

    # ----------------------------------------------------- personnel review
    def review(self, reader: str, policy: Optional[Policy] = None) -> list:
        """Proposals from the record, one per member of staff with enough of
        it: each with the writer, titles, judged results, the posterior
        chance of a right result (its tier's prior plus its own outcomes),
        and the proposal (`keep`, `probation`, `dismiss` or `promote`).
        Only results judged from outside the worker's group count; nothing
        else (escalations, questions, abstentions) is held against anyone."""
        policy = policy or Policy()
        results, _ = _firm.outcomes(self.memory, reader, compartment=self.compartment)
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
