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
        ("writer", 3),
        ("capability", 2),
        ("member", 2),
    ]
}

/// Built-in views: ordinary Datalog over the base relations, so anyone can
/// read exactly what each one means.
pub const VIEWS: &str = r#"
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
"#;

/// Run `program` against `ledger`, showing only `compartments`.
pub fn run(ledger: &Ledger, compartments: &[String], program: &str, lim: Limits) -> GateResult<Value> {
    let schema = schema();
    let prog = Program::compile(&schema, VIEWS, program, &lim)?;
    let mut eng = Engine::new(&prog, lim);
    let mut b = Builder::new(ledger, &prog, compartments);
    b.load(&mut eng)?;
    eng.run()?;
    let refs: Vec<Value> = b.sources.iter().map(|&seq| b.event_ref(seq)).collect();
    // Where the view ends, with that event's time: a result always states
    // the moment it describes.
    let through = b
        .last_visible()
        .map(|seq| {
            let time = ledger.events[seq].get("record_time").cloned().unwrap_or(Value::Null);
            b.event_ref(seq).with("time", time)
        })
        .unwrap_or(Value::Null);
    Ok(eng.answers(&|i| refs[i].clone())?.with("through", through))
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

        // Claims and their fields, from the event that first recorded each.
        for (id, c) in &st.claims {
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
        for (seq, e) in ledger.events.iter().enumerate() {
            if e.get("kind").and_then(|v| v.as_str()) != Some("claim") {
                continue;
            }
            let body = e.get("body").expect("body");
            let id = claim_id(body);
            let visible = st.claims.get(&id).map(|c| self.sees(&c.compartment)).unwrap_or(false);
            if visible {
                let w = e.get("writer").and_then(|v| v.as_str()).unwrap_or("");
                let src = self.src_seq(seq);
                self.add(eng, "asserted_by", vec![s(&id), s(w)], src)?;
            }
        }

        for (vid, v) in &st.verdicts {
            let visible = st.claims.get(&v.claim).map(|c| self.sees(&c.compartment)).unwrap_or(false);
            if !visible {
                continue;
            }
            let method = self.body(vid).get("method").and_then(|m| m.as_str()).unwrap_or("");
            let src = self.src(vid);
            self.add(eng, "verdict", vec![s(vid), s(&v.claim), s(&v.writer), s(&v.result), s(method)], src)?;
        }

        for (sid, sp) in &st.supersessions {
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

        for (aid, a) in &st.appeals {
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

        for (eid, e) in &st.episodes {
            if !self.sees(&e.compartment) {
                continue;
            }
            let src = self.src(eid);
            let medium = e.body.get("medium").and_then(|m| m.as_str()).unwrap_or("");
            self.add(eng, "episode", vec![s(eid), s(&e.writer), s(medium)], src)?;
            for p in &e.participants {
                self.add(eng, "participant", vec![s(eid), s(p)], src)?;
            }
        }

        // Writers who share a compartment with the reader, from their
        // registration; only the shared compartments are shown.
        let mut registered: HashMap<&str, usize> = HashMap::new();
        for (seq, e) in ledger.events.iter().enumerate() {
            if e.get("kind").and_then(|v| v.as_str()) == Some("register") {
                if let Some(w) = e.get("body").and_then(|b| b.get("writer")).and_then(|v| v.as_str()) {
                    registered.entry(w).or_insert(seq);
                }
            }
        }
        for (wid, w) in &st.writers {
            let shared: Vec<&String> = w.compartments.iter().filter(|c| self.sees(c)).collect();
            if shared.is_empty() {
                continue;
            }
            let src = self.src_seq(registered[wid.as_str()]);
            self.add(eng, "writer", vec![s(wid), s(&w.party), s(&w.kind)], src)?;
            for cap in &w.capabilities {
                self.add(eng, "capability", vec![s(wid), s(cap)], src)?;
            }
            for c in shared {
                self.add(eng, "member", vec![s(wid), s(c)], src)?;
            }
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
