//! Queries over the memory: base facts from the log, built-in views, proofs.
//!
//! Every base fact is read from the ledger as of some moment, restricted to
//! the reader's compartments, and cites the log events it comes from (event
//! id, position and hash). A derived answer's proof is a tree of named rules
//! ending in those events, so "why is this believed?" always traces back to
//! the hash chain.
//!
//! Absence (`not p(...)`) is judged against the same view: the log as of the
//! query's time, through the reader's compartments. Sources name events by
//! id and hash only, never by position, and the result carries no event
//! count or head hash: either would reveal how many events the reader cannot
//! see.

use std::collections::HashMap;

use crate::datalog::{Engine, Limits, Program, Schema, Val};
use crate::json::Value;
use crate::ledger::{claim_id, Ledger};
use crate::schema::GateResult;

/// Base relations, in the order documented in the spec.
pub fn schema() -> Schema {
    vec![
        ("claim", 1),
        ("statement", 2),
        ("speaker", 2),
        ("frame", 2),
        ("compartment", 2),
        ("stance", 2),
        ("origin", 2),
        ("statement_kind", 2),
        ("confidence", 2),
        ("asserted_truth", 2),
        ("recorded", 2),
        ("author", 2),
        ("asserted_by", 2),
        ("source", 2),
        ("reference", 3),
        ("quote_start", 2),
        ("validity_domain", 2),
        ("valid_from", 2),
        ("valid_to", 2),
        ("truth", 2),
        ("disputed", 1),
        ("defeated", 1),
        ("verdict", 5),
        ("supersession", 5),
        ("evidence", 2),
        ("resolution", 3),
        ("appeal", 3),
        ("episode", 3),
        ("participant", 2),
        ("handoff", 4),
        ("position", 3),
        ("holds", 3),
        ("appointed", 4),
        ("dismissed", 2),
        ("bound", 3),
        ("writer", 3),
        ("identity", 3),
        ("group", 2),
        ("instance_of", 2),
        ("speaks_as", 2),
        ("capability", 2),
        ("member", 2),
    ]
}

/// Built-in views: ordinary Datalog over the base relations, so anyone can
/// read exactly what each one means.
pub const VIEWS: &str = r#"
% Organisations: U is above T in O (directly or through others).
above(O, T, U) :- position(O, T, U), U != "".
above(O, T, U) :- position(O, T, M), M != "", above(O, M, U).
% B oversees W: B holds a position above one W holds.
oversees(B, W) :- holds(W, O, T), above(O, T, U), holds(B, O, U).

% Currently true and not under an open dispute.
believed(C) :- truth(C, "true"), not disputed(C).
% Currently false: refuted, or lost a ruling.
rejected(C) :- truth(C, "false").
% A decision that stands.
decision(C) :- stance(C, "decided"), believed(C).
% At least one verdict supports it.
supported(C) :- verdict(_, C, _, "supports", _).
% Some verdict refutes it.
challenged(C) :- verdict(_, C, _, "refutes", _).
% Party P asserted claim C (as author or corroborator).
asserted_by_party(C, P) :- asserted_by(C, W), writer(W, P, _).
% A narrower or more general claim New stands in for Old within its domain.
refined(Old, New) :- supersession(_, Old, New, "narrowed", _).
refined(Old, New) :- supersession(_, Old, New, "generalised", _).
% The groups whose agreement claim C has: every instance of one model is
% one group (shared weights, shared mistakes); each person is their own.
asserted_group(C, G) :- asserted_by(C, W), group(W, G).
% Asserted by at least two independent groups, not just two sessions.
independently_asserted(C) :- asserted_group(C, G1), asserted_group(C, G2), G1 != G2.
% Two writers that are instances of the same model.
same_model(W1, W2) :- identity(W1, "model", M), identity(W2, "model", M), writer(W1, _, "model"), writer(W2, _, "model"), W1 != W2.
% --- the record as a forum (M7) ---
% A question, and the claims that answer it.
question(Q) :- stance(Q, "asked").
answer(A, Q) :- reference(A, Q, "answers").
% Settled: some answer stands and someone verified it.
settled(Q) :- answer(A, Q), believed(A), supported(A).
% Open: neither settled nor itself rejected (a question can rest on a
% false premise, and be refuted like any claim).
open_question(Q) :- question(Q), not settled(Q), not rejected(Q).
% An argument about a claim (not a verdict: it changes no truth).
critique(K, C) :- reference(K, C, "critiques").
% A critique of C that C's author has replied to; one critique may name
% several claims, and each author answers for their own.
answered_critique(K, C) :- critique(K, C), author(C, W), reference(R, K, "replies"), author(R, W).
unanswered_critique(K, C) :- critique(K, C), not answered_critique(K, C).
% Submitted and not yet judged by anyone.
awaiting_verification(C) :- truth(C, "unknown"), not question(C), not supported(C), not challenged(C).
% Who a model or tool acts for.
acts_for(W, H) :- identity(W, "operator", H).
"#;

/// Run `program` against `ledger`, showing only `compartments`.
pub fn run(ledger: &Ledger, compartments: &[String], program: &str, lim: Limits) -> GateResult<Value> {
    let schema = schema();
    let prog = Program::compile(&schema, VIEWS, program, &lim)?;
    let mut eng = Engine::new(&prog, lim);
    let mut b = Builder::new(ledger, &prog, compartments);
    b.load(&mut eng)?;
    eng.run()?;
    // Event references are made only for the sources a proof shows.
    // Where the view ends, with that event's time: a result always states
    // the moment it describes.
    let through = b
        .last_visible()
        .map(|seq| {
            let time = ledger.events[seq].get("record_time").cloned().unwrap_or(Value::Null);
            b.event_ref(seq).with("time", time)
        })
        .unwrap_or(Value::Null);
    Ok(eng.answers(&|i| b.event_ref(b.sources[i]))?.with("through", through))
}

struct Builder<'a> {
    ledger: &'a Ledger,
    prog: &'a Program,
    visible: &'a [String],
    seq_of: HashMap<&'a str, usize>,
    /// Source index -> event seq.
    sources: Vec<usize>,
    source_of: HashMap<usize, usize>,
    needed: std::collections::HashSet<&'static str>,
}

impl<'a> Builder<'a> {
    fn new(ledger: &'a Ledger, prog: &'a Program, visible: &'a [String]) -> Self {
        let seq_of = ledger
            .events
            .iter()
            .enumerate()
            .filter_map(|(i, e)| e.get("event_id").and_then(|v| v.as_str()).map(|id| (id, i)))
            .collect();
        let needed = schema().into_iter().map(|(n, _)| n).filter(|n| prog.needs(n)).collect();
        Builder { ledger, prog, visible, seq_of, sources: Vec::new(), source_of: HashMap::new(), needed }
    }

    fn sees(&self, compartment: &str) -> bool {
        self.visible.iter().any(|c| c == compartment)
    }

    fn last_visible(&self) -> Option<usize> {
        last_visible(self.ledger, self.visible)
    }

    fn event_ref(&self, seq: usize) -> Value {
        let e = &self.ledger.events[seq];
        Value::obj(vec![
            ("event", e.get("event_id").cloned().unwrap_or(Value::Null)),
            ("kind", e.get("kind").cloned().unwrap_or(Value::Null)),
            ("hash", e.get("hash").cloned().unwrap_or(Value::Null)),
        ])
    }

    fn src(&mut self, event_id: &str) -> usize {
        let seq = self.seq_of[event_id];
        self.src_seq(seq)
    }

    fn src_seq(&mut self, seq: usize) -> usize {
        // Every source is an event the reader may see: the gate keeps a
        // dispute, its evidence and its ruling in the claim's compartment.
        if let Some(&i) = self.source_of.get(&seq) {
            return i;
        }
        let i = self.sources.len();
        self.sources.push(seq);
        self.source_of.insert(seq, i);
        i
    }

    fn body(&self, event_id: &str) -> &'a Value {
        let seq = self.seq_of[event_id];
        self.ledger.events[seq].get("body").expect("every event has a body")
    }

    fn add(&self, eng: &mut Engine, rel: &str, row: Vec<Val>, src: usize) -> GateResult<()> {
        self.add_all(eng, rel, row, vec![src])
    }

    /// A fact that only a set of events establishes together. Relations
    /// the query cannot reach are skipped: they cannot change an answer.
    fn add_all(&self, eng: &mut Engine, rel: &str, row: Vec<Val>, srcs: Vec<usize>) -> GateResult<()> {
        if !self.needed.contains(rel) {
            return Ok(());
        }
        let pred = self.prog.pred_id(rel).expect("schema relation");
        eng.add_base(pred, row, srcs)
    }

    fn load(&mut self, eng: &mut Engine) -> GateResult<()> {
        let ledger: &'a Ledger = self.ledger;
        let st = &ledger.state;
        let s = |x: &str| Val::str(x);

        // Claims and their fields, from the event that first recorded each
        // (skipped entirely when the query uses none of them).
        const CLAIM_RELS: &[&str] = &[
            "claim", "statement", "speaker", "frame", "compartment", "stance", "origin", "statement_kind",
            "asserted_truth", "source", "validity_domain", "valid_from", "valid_to", "confidence", "quote_start",
            "recorded", "author", "reference", "truth", "disputed", "defeated",
        ];
        let claims_needed = CLAIM_RELS.iter().any(|r| self.needed.contains(r));
        let need_1 = claims_needed;

        for (id, c) in st.claims.iter().filter(|_| need_1) {
            if !self.sees(&c.compartment) {
                continue;
            }
            let first = self.src(&c.first_event);
            let cid = s(id);
            self.add(eng, "claim", vec![cid.clone()], first)?;
            for (rel, field) in [
                ("statement", "statement"),
                ("speaker", "speaker"),
                ("frame", "frame"),
                ("compartment", "compartment"),
                ("stance", "stance"),
                ("origin", "origin"),
                ("statement_kind", "statement_kind"),
                ("asserted_truth", "truth"),
                ("source", "source_episode"),
                ("validity_domain", "validity_domain"),
                ("valid_from", "valid_from"),
                ("valid_to", "valid_to"),
            ] {
                if !self.needed.contains(rel) {
                    continue;
                }
                if let Some(v) = c.body.get(field).and_then(|v| v.as_str()) {
                    self.add(eng, rel, vec![cid.clone(), s(v)], first)?;
                }
            }
            for (rel, field) in [("confidence", "confidence"), ("quote_start", "quote_start")] {
                if let Some(v) = c.body.get(field).and_then(|v| v.as_int()) {
                    self.add(eng, rel, vec![cid.clone(), Val::Int(v)], first)?;
                }
            }
            self.add(eng, "recorded", vec![cid.clone(), s(&c.record_time)], first)?;
            self.add(eng, "author", vec![cid.clone(), s(&c.author)], first)?;
            if let Some(refs) = c.body.get("about").and_then(|v| v.as_array()) {
                for r in refs {
                    let target = r.get("claim").and_then(|v| v.as_str()).unwrap_or("");
                    let rel = r.get("relation").and_then(|v| v.as_str()).unwrap_or("");
                    self.add(eng, "reference", vec![cid.clone(), s(target), s(rel)], first)?;
                }
            }

            // The dispute folds cost work: only when the query can use them.
            if self.needed.contains("truth") {
                let (truth, cause) = c.truth_and_cause(id, st);
                let cause = cause.iter().map(|e| self.src(e)).collect();
                self.add_all(eng, "truth", vec![cid.clone(), s(&truth)], cause)?;
            }
            if self.needed.contains("disputed") {
                for d in st.open_disputes(id) {
                    let d = self.src(&d);
                    self.add(eng, "disputed", vec![cid.clone()], d)?;
                }
            }
            if self.needed.contains("defeated") {
                if let Some(cause) = st.defeated_by(id) {
                    let cause = cause.iter().map(|e| self.src(e)).collect();
                    self.add_all(eng, "defeated", vec![cid.clone()], cause)?;
                }
            }
        }

        // Every assertion of a visible claim, each from its own event.
        if self.needed.contains("asserted_by") {
            for (id, c) in &st.claims {
                if !self.sees(&c.compartment) {
                    continue;
                }
                for (w, seq) in &c.assertions {
                    let src = self.src_seq(*seq);
                    self.add(eng, "asserted_by", vec![s(id), s(w)], src)?;
                }
            }
        }

        let need_2 = self.needed.contains("verdict");

        for (vid, v) in st.verdicts.iter().filter(|_| need_2) {
            let visible = st.claims.get(&v.claim).map(|c| self.sees(&c.compartment)).unwrap_or(false);
            if !visible {
                continue;
            }
            let method = self.body(vid).get("method").and_then(|m| m.as_str()).unwrap_or("");
            let src = self.src(vid);
            self.add(eng, "verdict", vec![s(vid), s(&v.claim), s(&v.writer), s(&v.result), s(method)], src)?;
        }

        let need_3 = ["supersession", "evidence", "resolution"].iter().any(|r| self.needed.contains(r));

        for (sid, sp) in st.supersessions.iter().filter(|_| need_3) {
            if !self.sees(&sp.compartment) {
                continue;
            }
            let src = self.src(sid);
            self.add(eng, "supersession", vec![s(sid), s(&sp.old), s(&sp.new), s(&sp.kind), s(&sp.by)], src)?;
            for ev in &sp.evidence {
                self.add(eng, "evidence", vec![s(sid), s(ev)], src)?;
            }
            if let Some(r) = &sp.resolution {
                // The dispute and the event that ended it, together.
                let rs = vec![src, self.src(&r.event)];
                self.add_all(eng, "resolution", vec![s(sid), s(&r.outcome), s(&r.event)], rs)?;
            }
        }

        let need_4 = ["appeal", "evidence", "resolution"].iter().any(|r| self.needed.contains(r));

        for (aid, a) in st.appeals.iter().filter(|_| need_4) {
            if !self.sees(&a.compartment) {
                continue;
            }
            let src = self.src(aid);
            self.add(eng, "appeal", vec![s(aid), s(&a.claim), s(&a.by)], src)?;
            for ev in &a.evidence {
                self.add(eng, "evidence", vec![s(aid), s(ev)], src)?;
            }
            if let Some(r) = &a.resolution {
                // The dispute and the event that ended it, together.
                let rs = vec![src, self.src(&r.event)];
                self.add_all(eng, "resolution", vec![s(aid), s(&r.outcome), s(&r.event)], rs)?;
            }
        }

        let need_5 = self.needed.contains("handoff");

        for (cid, c) in st.handoffs.iter().filter(|_| need_5) {
            if !self.sees(&c.compartment) {
                continue;
            }
            let src = self.src(cid);
            self.add(eng, "handoff", vec![s(cid), s(&c.frame), s(&c.writer), s(&c.record_time)], src)?;
        }

        let need_org = ["position", "holds", "appointed", "dismissed"].iter().any(|r| self.needed.contains(r));
        for p in st.positions.values().filter(|_| need_org) {
            if !self.sees(&p.compartment) {
                continue;
            }
            let src = self.src(&p.event);
            let up = p.reports_to.clone().unwrap_or_default();
            self.add(eng, "position", vec![s(&p.org), s(&p.title), s(&up)], src)?;
        }
        for (aid, a) in st.appointments.iter().filter(|_| need_org) {
            if !self.sees(&a.compartment) {
                continue;
            }
            let src = self.src(aid);
            self.add(eng, "appointed", vec![s(aid), s(&a.holder), s(&a.org), s(&a.title)], src)?;
            match &a.ended {
                None => self.add(eng, "holds", vec![s(&a.holder), s(&a.org), s(&a.title)], src)?,
                Some((ev, ..)) => {
                    let end = self.src(ev);
                    self.add(eng, "dismissed", vec![s(aid), s(ev)], end)?
                }
            }
        }

        let episodes_needed = ["episode", "participant", "bound"].iter().any(|r| self.needed.contains(r));
        let need_6 = episodes_needed;

        for (eid, e) in st.episodes.iter().filter(|_| need_6) {
            if !self.sees(&e.compartment) {
                continue;
            }
            let src = self.src(eid);
            let medium = e.body.get("medium").and_then(|m| m.as_str()).unwrap_or("");
            self.add(eng, "episode", vec![s(eid), s(&e.writer), s(medium)], src)?;
            for p in &e.participants {
                self.add(eng, "participant", vec![s(eid), s(p)], src)?;
            }
            for (label, w) in &e.bindings {
                self.add(eng, "bound", vec![s(eid), s(label), s(w)], src)?;
            }
        }

        // Writers who share a compartment with the reader, from their
        // registration; only the shared compartments are shown.
        let registered: HashMap<&str, usize> = st.writers.iter().map(|(w, i)| (w.as_str(), i.seq)).collect();
        for (wid, w) in &st.writers {
            let shared: Vec<&String> = w.compartments.iter().filter(|c| self.sees(c)).collect();
            if shared.is_empty() {
                continue;
            }
            let src = self.src_seq(registered[wid.as_str()]);
            self.add(eng, "writer", vec![s(wid), s(&w.party), s(&w.kind)], src)?;
            for (k, v) in [("name", &w.name), ("model", &w.model), ("operator", &w.operator), ("surface", &w.surface), ("session", &w.session)] {
                if let Some(v) = v {
                    self.add(eng, "identity", vec![s(wid), s(k), s(v)], src)?;
                }
            }
            // Its group: an instance's is its parent's, so the facts come
            // from every registration up the line.
            let mut line = vec![src];
            let mut cur = w;
            while let Some(p) = cur.instance_of.as_deref() {
                let pseq = registered[p];
                line.push(self.src_seq(pseq));
                if line.len() == 2 {
                    self.add_all(eng, "instance_of", vec![s(wid), s(p)], line.clone())?;
                }
                cur = &st.writers[p];
            }
            self.add_all(eng, "group", vec![s(wid), s(&st.group(wid))], line)?;
            for cap in &w.capabilities {
                self.add(eng, "capability", vec![s(wid), s(cap)], src)?;
            }
            for c in shared {
                self.add(eng, "member", vec![s(wid), s(c)], src)?;
            }
        }
        // Who each visible claim's speaker is, as settled when it was first
        // recorded: from the claim and the episode that binds the name, or
        // the registration that names the writer. Only writers the reader
        // can see are named.
        let need_7 = self.needed.contains("speaks_as");

        for (id, c) in st.claims.iter().filter(|_| need_7) {
            let Some(who) = c.speaker_is.as_deref() else { continue };
            if !self.sees(&c.compartment) {
                continue;
            }
            let Some(wi) = st.writers.get(who) else { continue };
            if !wi.compartments.iter().any(|x| self.sees(x)) {
                continue;
            }
            let first = self.src(&c.first_event);
            let second = match c.body.get("source_episode").and_then(|v| v.as_str()) {
                Some(ep) => self.src(ep),
                None => self.src_seq(registered[who]),
            };
            self.add_all(eng, "speaks_as", vec![s(id), s(who)], vec![first, second])?;
        }
        Ok(())
    }
}

/// Can the reader see this event? Its compartment decides: a claim's,
/// an episode's, a verdict's claim's, a dispute's, an appeal's; a
/// registration is visible if the writer shares a compartment with the
/// reader; a schema change belongs to everyone.
pub fn event_visible(ledger: &Ledger, visible: &[String], e: &Value) -> bool {
    let st = &ledger.state;
    let sees = |c: &str| visible.iter().any(|x| x == c);
    let id = e.get("event_id").and_then(|v| v.as_str()).unwrap_or("");
    let body = e.get("body");
    let field = |k: &str| body.and_then(|b| b.get(k)).and_then(|v| v.as_str()).unwrap_or("");
    let claim_seen = |c: &str| st.claims.get(c).map(|c| sees(&c.compartment)).unwrap_or(false);
    match e.get("kind").and_then(|v| v.as_str()).unwrap_or("") {
        "claim" => body.map(|b| claim_seen(&claim_id(b))).unwrap_or(false),
        "episode" => st.episodes.get(id).map(|x| sees(&x.compartment)).unwrap_or(false),
        "verdict" => st.verdicts.get(id).map(|v| claim_seen(&v.claim)).unwrap_or(false),
        "supersede" => st.supersessions.get(id).map(|x| sees(&x.compartment)).unwrap_or(false),
        "appeal" => st.appeals.get(id).map(|x| sees(&x.compartment)).unwrap_or(false),
        "handoff" => st.handoffs.get(id).map(|x| sees(&x.compartment)).unwrap_or(false),
        "position" => sees(field("compartment")),
        "appoint" => st.appointments.get(id).map(|x| sees(&x.compartment)).unwrap_or(false),
        "dismiss" => st.appointments.get(field("appointment")).map(|x| sees(&x.compartment)).unwrap_or(false),
        "resolve" => {
            let d = field("dispute");
            st.supersessions.get(d).map(|x| sees(&x.compartment)).unwrap_or(false)
                || st.appeals.get(d).map(|x| sees(&x.compartment)).unwrap_or(false)
        }
        "register" => st
            .writers
            .get(field("writer"))
            .map(|w| w.compartments.iter().any(|c| sees(c)))
            .unwrap_or(false),
        _ => true,
    }
}

/// The last event the reader can see: where its view of the log ends.
/// Every event that can bear on a visible fact is at or before it.
pub fn last_visible(ledger: &Ledger, visible: &[String]) -> Option<usize> {
(0..ledger.events.len()).rev().find(|&i| event_visible(ledger, visible, &ledger.events[i]))
}

/// Where the reader's view ends, as an event reference with its time.
pub fn view_end(ledger: &Ledger, visible: &[String]) -> Value {
match last_visible(ledger, visible) {
    None => Value::Null,
    Some(seq) => {
        let e = &ledger.events[seq];
        Value::obj(vec![
            ("event", e.get("event_id").cloned().unwrap_or(Value::Null)),
            ("kind", e.get("kind").cloned().unwrap_or(Value::Null)),
            ("hash", e.get("hash").cloned().unwrap_or(Value::Null)),
            ("time", e.get("record_time").cloned().unwrap_or(Value::Null)),
        ])
    }
}
}
