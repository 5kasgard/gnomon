//! The ledger: hash-chained events, the write gate, and derived state.
//!
//! Every event passes through one function, [`Ledger::apply`], whether it is
//! being appended fresh or replayed from disk. There is no second path in, so
//! there is no way for a check to be skipped on replay or supplied by a test.
//!
//! The gate is two-phase. [`check`] evaluates every rule against unchanged
//! state and returns a single [`Change`]; only then does [`commit`] apply it.
//! State therefore cannot be half-updated by a rejected event.

use std::collections::BTreeMap;

use crate::json::{canonical, Value};
use crate::schema::{self, GateError, GateResult};
use crate::sha256::hex_digest;

pub const GENESIS_PREV: &str = "0000000000000000000000000000000000000000000000000000000000000000";

const ENVELOPE_FIELDS: &[&str] = &[
    "seq",
    "event_id",
    "writer",
    "writer_seq",
    "record_time",
    "prev",
    "kind",
    "body",
    "hash",
];

/// Content address of a claim body.
pub fn claim_id(body: &Value) -> String {
    format!("c:{}", hex_digest(canonical(body).as_bytes()))
}

/// Hash of an envelope: SHA-256 of its canonical form without `hash`.
pub fn envelope_hash(env: &Value) -> String {
    hex_digest(canonical(&env.without("hash")).as_bytes())
}

#[derive(Clone, Debug)]
pub struct WriterInfo {
    pub party: String,
    pub kind: String,
    pub capabilities: Vec<String>,
    pub compartments: Vec<String>,
    pub last_seq: i64,
}

impl WriterInfo {
    fn can(&self, cap: &str) -> bool {
        self.capabilities.iter().any(|c| c == cap || c == "owner")
    }
    fn in_compartment(&self, c: &str) -> bool {
        self.compartments.iter().any(|x| x == c)
    }
}

#[derive(Clone, Debug)]
pub struct EpisodeInfo {
    pub compartment: String,
    pub writer: String,
    pub record_time: String,
    pub hidden_chars: bool,
    pub body: Value,
    pub participants: Vec<String>,
    /// (speaker, start, end) in code points; empty if not recorded.
    pub turns: Vec<(String, usize, usize)>,
}

#[derive(Clone, Debug)]
pub struct ClaimInfo {
    pub body: Value,
    pub compartment: String,
    pub asserted_truth: String,
    pub author: String,
    pub first_event: String,
    pub record_time: String,
    pub corroborated_by: Vec<String>,
    /// Parties of the author and every corroborator: none may verify it.
    pub parties: Vec<String>,
    /// Parties that have given a verdict: none may then co-assert it.
    pub verifier_parties: Vec<String>,
    pub supports: Vec<String>,
    pub refutes: Vec<String>,
    pub abstains: Vec<String>,
    /// Supersession event ids where this claim is the old one.
    pub superseded_by: Vec<String>,
    /// Supersession event ids where this claim is the new one.
    pub supersedes: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Resolution {
    pub event: String,
    pub outcome: String,
    /// Party of the neutral resolver (or, if `by_refutation`, of the refuter).
    pub party: String,
    pub seq: usize,
    /// The contest was ended by an evidence-backed refutation, not a ruling:
    /// the refuter did not act as a judge.
    pub by_refutation: bool,
}

#[derive(Clone, Debug)]
pub struct SupersessionInfo {
    pub old: String,
    pub new: String,
    pub kind: String,
    pub compartment: String,
    pub evidence: Vec<String>,
    pub by: String,
    pub by_party: String,
    pub record_time: String,
    pub seq: usize,
    /// For contests: the challenger's party and every party that asserted
    /// either claim, frozen when the dispute opens.
    pub dispute_parties: Vec<String>,
    pub resolution: Option<Resolution>,
}

impl SupersessionInfo {
    fn is_open_dispute(&self) -> bool {
        self.kind == "contested" && self.resolution.is_none()
    }

    fn is_ruled(&self) -> bool {
        self.kind == "contested"
            && self
                .resolution
                .as_ref()
                .map(|r| r.outcome != "withdrawn")
                .unwrap_or(false)
    }

    fn involves(&self, claim: &str) -> bool {
        self.old == claim || self.new == claim
    }
}

#[derive(Clone, Debug)]
pub struct VerdictInfo {
    pub claim: String,
    pub result: String,
    pub writer: String,
    pub seq: usize,
}

#[derive(Clone, Debug)]
pub struct AppealInfo {
    pub claim: String,
    pub compartment: String,
    pub evidence: Vec<String>,
    pub by: String,
    pub by_party: String,
    pub record_time: String,
    pub seq: usize,
    /// Everyone with a stake, frozen when the appeal is filed: the
    /// appellant, the claim's parties, everyone interested in its earlier
    /// disputes, its refuters, and the givers of the cited evidence.
    pub appeal_parties: Vec<String>,
    pub resolution: Option<Resolution>,
}

impl AppealInfo {
    fn is_open(&self) -> bool {
        self.resolution.is_none()
    }
    fn is_ruled(&self) -> bool {
        self.resolution
            .as_ref()
            .map(|r| r.outcome != "withdrawn")
            .unwrap_or(false)
    }
}

#[derive(Clone, Debug, Default)]
pub struct State {
    pub writers: BTreeMap<String, WriterInfo>,
    pub episodes: BTreeMap<String, EpisodeInfo>,
    pub claims: BTreeMap<String, ClaimInfo>,
    pub supersessions: BTreeMap<String, SupersessionInfo>,
    pub verdicts: BTreeMap<String, VerdictInfo>,
    pub appeals: BTreeMap<String, AppealInfo>,
    /// Appeal ids by claim, in log order (an index; not part of the view).
    pub appeals_by_claim: BTreeMap<String, Vec<String>>,
    pub schema_version: i64,
}

/// What a caller submits; the ledger assigns sequence numbers and hashes.
pub struct Draft {
    pub writer: String,
    pub record_time: String,
    pub kind: String,
    pub body: Value,
}

#[derive(Default)]
pub struct Ledger {
    pub events: Vec<Value>,
    pub state: State,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum VerifyStatus {
    /// Every event checked and valid.
    Ok { count: usize, head: String },
    /// The first invalid event and why.
    Broken { seq: usize, error: GateError },
    /// The budget ran out before the end: not a pass.
    Exhausted { checked: usize, total: usize },
}

impl Ledger {
    pub fn new() -> Self {
        Ledger {
            events: Vec::new(),
            state: State {
                schema_version: schema::SCHEMA_VERSION,
                ..State::default()
            },
        }
    }

    pub fn head(&self) -> String {
        self.events
            .last()
            .and_then(|e| e.get("hash"))
            .and_then(|h| h.as_str())
            .unwrap_or(GENESIS_PREV)
            .to_string()
    }

    /// Build a full envelope for `draft`, run it through the gate, append it.
    pub fn append(&mut self, draft: Draft) -> GateResult<Value> {
        let next_writer_seq = self
            .state
            .writers
            .get(&draft.writer)
            .map(|w| w.last_seq + 1)
            .unwrap_or(1);
        let env = Value::obj(vec![
            ("seq", Value::Int(self.events.len() as i64)),
            ("event_id", Value::str(format!("{}/{}", draft.writer, next_writer_seq))),
            ("writer", Value::str(draft.writer)),
            ("writer_seq", Value::Int(next_writer_seq)),
            ("record_time", Value::str(draft.record_time)),
            ("prev", Value::str(self.head())),
            ("kind", Value::str(draft.kind)),
            ("body", draft.body),
        ]);
        let hash = envelope_hash(&env);
        let env = env.with("hash", Value::str(hash));
        self.apply(env.clone())?;
        Ok(env)
    }

    /// The write gate: check every rule, then commit. State is untouched on
    /// any error.
    pub fn apply(&mut self, env: Value) -> GateResult<()> {
        let checked = check(&self.state, &env, self.events.len(), &self.head(), self.events.last())?;
        commit(&mut self.state, checked, self.events.len());
        self.events.push(env);
        Ok(())
    }

    /// Replay `events` from empty, checking every rule. A budget smaller than
    /// the log yields `Exhausted`, never `Ok`.
    pub fn verify(events: &[Value], budget: Option<usize>) -> (VerifyStatus, Ledger) {
        let mut ledger = Ledger::new();
        for (i, env) in events.iter().enumerate() {
            if let Some(b) = budget {
                if i >= b {
                    return (
                        VerifyStatus::Exhausted {
                            checked: i,
                            total: events.len(),
                        },
                        ledger,
                    );
                }
            }
            if let Err(error) = ledger.apply(env.clone()) {
                return (VerifyStatus::Broken { seq: i, error }, ledger);
            }
        }
        let head = ledger.head();
        (
            VerifyStatus::Ok {
                count: events.len(),
                head,
            },
            ledger,
        )
    }

    /// Ledger rebuilt from events recorded at or before `t`.
    pub fn as_of(&self, t: &str) -> Ledger {
        let mut l = Ledger::new();
        for env in &self.events {
            let rt = env.get("record_time").and_then(|v| v.as_str()).unwrap_or("");
            if rt > t {
                break; // record_time is monotonic, so this is a prefix
            }
            l.apply(env.clone()).expect("events already verified");
        }
        l
    }
}

// ------------------------------------------------------------- the gate

/// The single state change an accepted event makes.
pub enum Change {
    Register { writer: String, info: WriterInfo },
    Episode { id: String, info: EpisodeInfo },
    NewClaim { id: String, info: ClaimInfo },
    Corroborate { id: String, writer: String, party: String },
    Verdict { event: String, info: VerdictInfo, party: String },
    /// `closes`: open disputes this refutation ends, with the outcome each
    /// takes (the refuted claim loses).
    Supersede { event: String, info: SupersessionInfo, closes: Vec<(String, String)> },
    Resolve { supersession: String, event: String, outcome: String, party: String },
    Appeal { event: String, info: AppealInfo },
    ResolveAppeal { appeal: String, event: String, outcome: String, party: String },
    SchemaVersion(i64),
}

pub struct Checked {
    writer: String,
    writer_seq: i64,
    change: Change,
}

fn err(code: &'static str, msg: impl Into<String>) -> GateError {
    GateError::new(code, msg)
}

/// "Not found" and "not accessible" are one answer, so the gate never
/// confirms that hidden content exists.
fn not_found(what: &str, id: &str) -> GateError {
    err("E_REF", format!("{what} '{id}' does not exist or is not accessible"))
}

fn first_duplicate_key(v: &Value) -> Option<String> {
    match v {
        Value::Object(m) => {
            for (i, (k, _)) in m.iter().enumerate() {
                if m[..i].iter().any(|(k2, _)| k2 == k) {
                    return Some(k.clone());
                }
            }
            m.iter().find_map(|(_, x)| first_duplicate_key(x))
        }
        Value::Array(items) => items.iter().find_map(first_duplicate_key),
        _ => None,
    }
}

fn check_envelope_shape(env: &Value) -> GateResult<()> {
    let obj = env
        .as_object()
        .ok_or_else(|| err("E_ENVELOPE", "event must be an object"))?;
    // Values built in code (not parsed) could carry duplicate keys, which
    // the canonical form would hash but the gate would only half-check.
    if let Some(k) = first_duplicate_key(env) {
        return Err(err("E_ENVELOPE", format!("duplicate key '{k}'")));
    }
    for (k, _) in obj {
        if !ENVELOPE_FIELDS.contains(&k.as_str()) {
            return Err(err("E_ENVELOPE", format!("unknown envelope field '{k}'")));
        }
    }
    for f in ENVELOPE_FIELDS {
        if env.get(f).is_none() {
            return Err(err("E_ENVELOPE", format!("missing envelope field '{f}'")));
        }
    }
    Ok(())
}

fn s<'a>(env: &'a Value, key: &str) -> GateResult<&'a str> {
    env.get(key)
        .and_then(|v| v.as_str())
        .ok_or_else(|| err("E_ENVELOPE", format!("'{key}' must be a string")))
}

fn n(env: &Value, key: &str) -> GateResult<i64> {
    env.get(key)
        .and_then(|v| v.as_int())
        .ok_or_else(|| err("E_ENVELOPE", format!("'{key}' must be an integer")))
}

fn need(w: &WriterInfo, cap: &str) -> GateResult<()> {
    if w.can(cap) {
        Ok(())
    } else {
        Err(err("E_CAPABILITY", format!("missing capability '{cap}'")))
    }
}

fn need_compartment(w: &WriterInfo, c: &str) -> GateResult<()> {
    if w.in_compartment(c) {
        Ok(())
    } else {
        Err(err("E_COMPARTMENT", format!("no access to compartment '{c}'")))
    }
}

fn visible_claim<'a>(st: &'a State, w: &WriterInfo, id: &str) -> GateResult<&'a ClaimInfo> {
    st.claims
        .get(id)
        .filter(|c| w.in_compartment(&c.compartment))
        .ok_or_else(|| not_found("claim", id))
}

/// Compartment of any citable item (claim, episode or verdict), if the
/// writer can see it.
fn visible_item_compartment<'a>(st: &'a State, w: &WriterInfo, id: &str) -> GateResult<&'a str> {
    let c = if let Some(c) = st.claims.get(id) {
        Some(c.compartment.as_str())
    } else if let Some(e) = st.episodes.get(id) {
        Some(e.compartment.as_str())
    } else if let Some(v) = st.verdicts.get(id) {
        st.claims.get(&v.claim).map(|c| c.compartment.as_str())
    } else {
        None
    };
    c.filter(|c| w.in_compartment(c))
        .ok_or_else(|| not_found("evidence", id))
}

fn commit(st: &mut State, checked: Checked, seq: usize) {
    match checked.change {
        Change::Register { writer, info } => {
            st.writers.insert(writer, info);
        }
        Change::Episode { id, info } => {
            st.episodes.insert(id, info);
        }
        Change::NewClaim { id, info } => {
            st.claims.insert(id, info);
        }
        Change::Corroborate { id, writer, party } => {
            let c = st.claims.get_mut(&id).expect("checked");
            c.corroborated_by.push(writer);
            if !c.parties.contains(&party) {
                c.parties.push(party);
            }
        }
        Change::Verdict { event, mut info, party } => {
            info.seq = seq;
            let c = st.claims.get_mut(&info.claim).expect("checked");
            match info.result.as_str() {
                "supports" => c.supports.push(event.clone()),
                "refutes" => c.refutes.push(event.clone()),
                _ => c.abstains.push(event.clone()),
            }
            c.verifier_parties.push(party);
            st.verdicts.insert(event, info);
        }
        Change::Supersede { event, mut info, closes } => {
            info.seq = seq;
            for (dispute, outcome) in closes {
                st.supersessions.get_mut(&dispute).expect("checked").resolution = Some(Resolution {
                    event: event.clone(),
                    outcome,
                    party: info.by_party.clone(),
                    seq,
                    by_refutation: true,
                });
            }
            st.claims.get_mut(&info.old).expect("checked").superseded_by.push(event.clone());
            st.claims.get_mut(&info.new).expect("checked").supersedes.push(event.clone());
            st.supersessions.insert(event, info);
        }
        Change::Resolve { supersession, event, outcome, party } => {
            st.supersessions.get_mut(&supersession).expect("checked").resolution =
                Some(Resolution { event, outcome, party, seq, by_refutation: false });
        }
        Change::Appeal { event, mut info } => {
            info.seq = seq;
            st.appeals_by_claim.entry(info.claim.clone()).or_default().push(event.clone());
            st.appeals.insert(event, info);
        }
        Change::ResolveAppeal { appeal, event, outcome, party } => {
            st.appeals.get_mut(&appeal).expect("checked").resolution =
                Some(Resolution { event, outcome, party, seq, by_refutation: false });
        }
        Change::SchemaVersion(v) => st.schema_version = v,
    }
    // The event's writer exists now (genesis registers itself in this change).
    st.writers
        .get_mut(&checked.writer)
        .expect("writer registered")
        .last_seq = checked.writer_seq;
}

/// Parties interested in `claim` through any ruled dispute involving it.
/// Their verdicts do not count as refutation of it.
fn judge_party(r: &Resolution) -> Option<String> {
    if r.by_refutation {
        None
    } else {
        Some(r.party.clone())
    }
}

fn interested_parties(st: &State, claim: &str) -> Vec<String> {
    let from_contests = involving(st, claim)
        .map(|(_, sp)| sp)
        .filter(|sp| sp.is_ruled())
        .flat_map(|sp| {
            let resolver = sp.resolution.as_ref().and_then(judge_party);
            sp.dispute_parties.iter().cloned().chain(resolver)
        });
    let from_appeals = appeals_of(st, claim)
        .map(|(_, a)| a)
        .filter(|a| a.is_ruled())
        .flat_map(|a| {
            let resolver = a.resolution.as_ref().map(|r| r.party.clone());
            a.appeal_parties.iter().cloned().chain(resolver)
        });
    from_contests.chain(from_appeals).collect()
}

/// Parties that refuted `claim`: who superseded it as refuted, and who gave
/// the refuting verdicts cited.
fn refuter_parties(st: &State, claim: &str) -> Vec<String> {
    let mut out = Vec::new();
    for (_, sp) in involving(st, claim).filter(|(_, sp)| sp.kind == "refuted" && sp.old == claim) {
        out.push(sp.by_party.clone());
        for ev in &sp.evidence {
            if let Some(v) = st.verdicts.get(ev) {
                out.push(st.writers[&v.writer].party.clone());
            }
        }
    }
    out
}

/// Parties that gave a verdict on `claim` (optionally only of one result).
fn verdict_parties(st: &State, claim: &str, result: Option<&str>) -> Vec<String> {
    // From the claim's own verdict lists, not every verdict in the log.
    st.claims
        .get(claim)
        .into_iter()
        .flat_map(|c| c.supports.iter().chain(&c.refutes).chain(&c.abstains))
        .map(|vid| &st.verdicts[vid])
        .filter(|v| result.map(|r| v.result == r).unwrap_or(true))
        .map(|v| st.writers[&v.writer].party.clone())
        .collect()
}

/// Everyone who has taken the other side against `claim`: its refuters,
/// every party that gave a refuting verdict, and the current parties of
/// every claim set against it (in contests or refutations).
fn opponents(st: &State, claim: &str) -> Vec<String> {
    let mut out = refuter_parties(st, claim);
    out.extend(verdict_parties(st, claim, Some("refutes")));
    for (_, sp) in involving(st, claim) {
        let other = if sp.kind == "contested" && sp.involves(claim) {
            if sp.old == claim { &sp.new } else { &sp.old }
        } else if sp.kind == "refuted" && sp.old == claim {
            &sp.new
        } else {
            continue;
        };
        out.extend(st.claims[other].parties.iter().cloned());
    }
    out
}

/// Parties that have already acted *against* or *on* `claim`, and so get no
/// second argument about it (only evidence): challengers of ruled contests
/// against it, its refuters, parties that gave a refuting verdict, and the
/// judges who ruled on it. Parties that merely defended against it are not
/// included.
fn argued(st: &State, claim: &str) -> Vec<String> {
    let mut out = refuter_parties(st, claim);
    out.extend(verdict_parties(st, claim, Some("refutes")));
    for (_, sp) in involving(st, claim).filter(|(_, sp)| sp.is_ruled()) {
        if sp.old == claim {
            out.push(sp.by_party.clone());
        }
        if let Some(j) = sp.resolution.as_ref().and_then(judge_party) {
            out.push(j);
        }
    }
    for (_, a) in appeals_of(st, claim).filter(|(_, a)| a.is_ruled()) {
        if let Some(j) = a.resolution.as_ref().and_then(judge_party) {
            out.push(j);
        }
    }
    out
}

/// Is there a last-resort judge: a writer with `govern` and `verify` who can
/// see the compartment and is not one of `core` (the advocates and
/// adversaries)? Used only when no ordinary neutral judge remains.
fn backstop_exists(st: &State, compartment: &str, core: &[String]) -> bool {
    st.writers.values().any(|w| {
        w.can("govern") && w.can("verify") && w.in_compartment(compartment) && !core.contains(&w.party)
    })
}

/// Open contest or appeal involving `claim`?
fn is_disputed(st: &State, claim: &str) -> bool {
    involving(st, claim).any(|(_, sp)| sp.is_open_dispute()) || appeals_of(st, claim).any(|(_, a)| a.is_open())
}

/// Supersessions involving `claim` (as old or new), from its own lists:
/// cost proportional to the claim's history, not the whole log.
fn involving<'a>(st: &'a State, claim: &str) -> impl Iterator<Item = (&'a String, &'a SupersessionInfo)> + 'a {
    st.claims
        .get(claim)
        .into_iter()
        .flat_map(|c| c.superseded_by.iter().chain(&c.supersedes))
        .map(move |sid| (sid, &st.supersessions[sid]))
}

/// Appeals of `claim`, in log order.
fn appeals_of<'a>(st: &'a State, claim: &str) -> impl Iterator<Item = (&'a String, &'a AppealInfo)> + 'a {
    st.appeals_by_claim
        .get(claim)
        .into_iter()
        .flatten()
        .map(move |aid| (aid, &st.appeals[aid]))
}

/// Seq of the latest reinstatement of `claim` on appeal, if any.
fn last_reinstatement(st: &State, claim: &str) -> Option<usize> {
    appeals_of(st, claim)
        .map(|(_, a)| a)
        .filter_map(|a| a.resolution.as_ref())
        .filter(|r| r.outcome == "reinstated")
        .map(|r| r.seq)
        .max()
}

/// A claim is defeated if it was refuted, or lost a ruling.
fn is_defeated(st: &State, claim: &str) -> bool {
    defeat_seq(st, claim).is_some()
}

/// Seq of the event that currently leaves `claim` defeated, if any: the
/// latest refutation or lost ruling, unless a later appeal reinstated it.
fn defeat_seq(st: &State, claim: &str) -> Option<usize> {
    defeat(st, claim).map(|(seq, _)| seq)
}

/// The defeat and the events that establish it: the refutation, or the
/// contest and the event that ended it against `claim`.
fn defeat(st: &State, claim: &str) -> Option<(usize, Vec<String>)> {
    let mut events: Vec<(usize, Option<Vec<String>>)> = Vec::new();
    for (sid, sp) in involving(st, claim) {
        if sp.kind == "refuted" && sp.old == claim {
            events.push((sp.seq, Some(vec![sid.clone()])));
        }
        if sp.is_ruled() && sp.involves(claim) {
            let r = sp.resolution.as_ref().unwrap();
            let lost = (r.outcome == "new" && sp.old == claim) || (r.outcome == "old" && sp.new == claim);
            if lost {
                events.push((r.seq, Some(vec![sid.clone(), r.event.clone()])));
            }
        }
    }
    if let Some(seq) = last_reinstatement(st, claim) {
        events.push((seq, None));
    }
    events.sort_by_key(|(seq, e)| (*seq, e.is_some()));
    match events.pop() {
        Some((seq, Some(cause))) => Some((seq, cause)),
        _ => None,
    }
}

/// Is there any registered writer who could neutrally resolve a dispute in
/// `compartment` between `parties`?
fn resolver_exists(st: &State, compartment: &str, parties: &[String]) -> bool {
    st.writers
        .values()
        .any(|w| w.can("verify") && w.in_compartment(compartment) && !parties.contains(&w.party))
}

/// Parties that resolved a dispute involving `claim`.
fn resolver_parties(st: &State, claim: &str) -> Vec<String> {
    let contests = st
        .supersessions
        .values()
        .filter(|sp| sp.is_ruled() && sp.involves(claim))
        .filter_map(|sp| sp.resolution.as_ref().and_then(judge_party));
    let appeals = st
        .appeals
        .values()
        .filter(|a| a.is_ruled() && a.claim == claim)
        .filter_map(|a| a.resolution.as_ref().map(|r| r.party.clone()));
    contests.chain(appeals).collect()
}

fn check(
    st: &State,
    env: &Value,
    expected_seq: usize,
    expected_prev: &str,
    last: Option<&Value>,
) -> GateResult<Checked> {
    check_envelope_shape(env)?;

    // --- chain integrity
    let hash = s(env, "hash")?;
    if !schema::is_hex64(hash) || envelope_hash(env) != hash {
        return Err(err("E_HASH", "hash does not match envelope content"));
    }
    if n(env, "seq")? != expected_seq as i64 {
        return Err(err("E_SEQ", format!("expected seq {expected_seq}")));
    }
    if s(env, "prev")? != expected_prev {
        return Err(err("E_PREV", "prev does not match the current head"));
    }

    // --- time: two clocks; record time never runs backwards
    let record_time = s(env, "record_time")?;
    if !schema::is_timestamp(record_time) {
        return Err(err("E_TIME_FORMAT", "record_time must be a real YYYY-MM-DDTHH:MM:SSZ time"));
    }
    if let Some(prev_rt) = last.and_then(|e| e.get("record_time")).and_then(|v| v.as_str()) {
        if record_time < prev_rt {
            return Err(err("E_CLOCK", "record_time is earlier than the previous event"));
        }
    }

    // --- writer namespace: ids are "writer/writer_seq", strictly sequential
    let writer = s(env, "writer")?;
    if !schema::is_ident(writer) {
        return Err(err("E_WRITER", "writer is not a valid identifier"));
    }
    let writer_seq = n(env, "writer_seq")?;
    let event_id = s(env, "event_id")?;
    if event_id != format!("{writer}/{writer_seq}") {
        return Err(err("E_EVENT_ID", "event_id must be '<writer>/<writer_seq>'"));
    }
    let kind = s(env, "kind")?;
    if !schema::EVENT_KINDS.contains(&kind) {
        return Err(err("E_KIND", format!("unknown event kind '{kind}'")));
    }
    let body = env.get("body").expect("shape checked");
    let done = |change| {
        Ok(Checked {
            writer: writer.to_string(),
            writer_seq,
            change,
        })
    };

    // --- genesis
    if expected_seq == 0 {
        if kind != "register" {
            return Err(err("E_GENESIS", "the first event must register the owner"));
        }
        let r = schema::register(body)?;
        if r.writer != writer || !r.capabilities.contains(&"owner") {
            return Err(err("E_GENESIS", "genesis must be an owner registering itself"));
        }
        if writer_seq != 1 {
            return Err(err("E_WRITER_SEQ", "first event of a writer must have writer_seq 1"));
        }
        return done(Change::Register {
            writer: writer.to_string(),
            info: writer_info(&r),
        });
    }

    let w = st
        .writers
        .get(writer)
        .ok_or_else(|| err("E_UNKNOWN_WRITER", format!("writer '{writer}' is not registered")))?;
    if writer_seq != w.last_seq + 1 {
        return Err(err(
            "E_WRITER_SEQ",
            format!(
                "writer '{writer}' must use writer_seq {} (got {writer_seq}); ids are never reused or skipped",
                w.last_seq + 1
            ),
        ));
    }

    match kind {
        "register" => {
            need(w, "owner")?;
            let r = schema::register(body)?;
            if st.writers.contains_key(r.writer) {
                return Err(err("E_DUPLICATE", format!("writer '{}' already registered", r.writer)));
            }
            done(Change::Register {
                writer: r.writer.to_string(),
                info: writer_info(&r),
            })
        }

        "episode" => {
            need(w, "write")?;
            let e = schema::episode(body)?;
            need_compartment(w, e.compartment)?;
            done(Change::Episode {
                id: event_id.to_string(),
                info: EpisodeInfo {
                    compartment: e.compartment.to_string(),
                    writer: writer.to_string(),
                    record_time: record_time.to_string(),
                    hidden_chars: schema::has_hidden_chars(e.content),
                    body: body.clone(),
                    participants: e.participants.iter().map(|p| p.to_string()).collect(),
                    turns: e.turns.iter().map(|(sp, a, b)| (sp.to_string(), *a, *b)).collect(),
                },
            })
        }

        "claim" => {
            need(w, "write")?;
            let c = schema::claim(body)?;
            need_compartment(w, c.compartment)?;
            // Did the writer's own party record the episode it quotes?
            let mut self_recorded = false;
            if let Some(ep) = c.source_episode {
                let e = st
                    .episodes
                    .get(ep)
                    .filter(|e| w.in_compartment(&e.compartment))
                    .ok_or_else(|| not_found("episode", ep))?;
                if e.compartment != c.compartment {
                    return Err(err(
                        "E_COMPARTMENT",
                        "a claim cannot cite an episode from another compartment",
                    ));
                }
                self_recorded = st.writers.get(&e.writer).map(|x| x.party == w.party).unwrap_or(false);
                // The quote must be exactly what the episode says at that
                // position: an extractor cannot put words in anyone's mouth.
                if let Some((quote, start)) = c.quote {
                    let content = e.body.get("content").and_then(|v| v.as_str()).unwrap_or("");
                    let found: String = content.chars().skip(start as usize).take(quote.chars().count()).collect();
                    if found != quote {
                        return Err(err(
                            "E_QUOTE",
                            "the quote does not match the source episode at quote_start",
                        ));
                    }
                    // And it must be the claimed speaker's words: inside one
                    // of their turns, or, for an episode without turns, an
                    // episode with that one participant.
                    let (a, b) = (start as usize, start as usize + quote.chars().count());
                    let theirs = if e.turns.is_empty() {
                        e.participants.len() == 1 && e.participants[0] == c.speaker
                    } else {
                        e.turns.iter().any(|(sp, ts, te)| *ts <= a && b <= *te && sp == c.speaker)
                    };
                    if !theirs {
                        return Err(err(
                            "E_QUOTE",
                            "the quote must lie within one turn spoken by the claim's speaker",
                        ));
                    }
                }
            }
            // Hearsay: a claim that someone else said something, without
            // quoting them, is the writer's interpretation, however it is
            // labelled. It cannot be stated with near-certainty.
            let own = c.speaker.to_lowercase() == writer.to_lowercase() || c.speaker.to_lowercase() == w.party.to_lowercase();
            if c.origin != "direct" && !own && c.confidence > schema::PARAPHRASE_MAX_CONFIDENCE {
                return Err(err(
                    "E_ATTRIBUTION",
                    format!(
                        "a claim about what '{}' said, recorded by someone else without quoting them, cannot carry confidence above {}",
                        c.speaker,
                        schema::PARAPHRASE_MAX_CONFIDENCE
                    ),
                ));
            }
            // A quote is only as good as its recording. Quoting someone else
            // from an episode the writer's own party recorded is still that
            // party's account of what was said: the same cap applies.
            if c.quote.is_some() && self_recorded && !own && c.confidence > schema::PARAPHRASE_MAX_CONFIDENCE {
                return Err(err(
                    "E_ATTRIBUTION",
                    format!(
                        "quoting '{}' from an episode the writer's own party recorded cannot carry confidence above {}",
                        c.speaker,
                        schema::PARAPHRASE_MAX_CONFIDENCE
                    ),
                ));
            }
            let id = claim_id(body);
            match st.claims.get(&id) {
                // Identical content from another writer corroborates; it never overwrites.
                Some(existing) => {
                    if existing.author == writer || existing.corroborated_by.iter().any(|x| x == writer) {
                        return Err(err("E_DUPLICATE", "this writer already recorded this claim"));
                    }
                    if existing.verifier_parties.contains(&w.party) {
                        return Err(err(
                            "E_ROLE_CONFLICT",
                            "a party that verified a claim cannot also assert it",
                        ));
                    }
                    if is_disputed(st, &id) {
                        return Err(err(
                            "E_OPEN_DISPUTE",
                            "a claim in an open dispute cannot be corroborated until it is resolved",
                        ));
                    }
                    if resolver_parties(st, &id).contains(&w.party) {
                        return Err(err(
                            "E_ROLE_CONFLICT",
                            "a party that resolved a dispute cannot then join a side of it",
                        ));
                    }
                    done(Change::Corroborate {
                        id,
                        writer: writer.to_string(),
                        party: w.party.clone(),
                    })
                }
                None => done(Change::NewClaim {
                    id,
                    info: ClaimInfo {
                        body: body.clone(),
                        compartment: c.compartment.to_string(),
                        asserted_truth: c.truth.to_string(),
                        author: writer.to_string(),
                        first_event: event_id.to_string(),
                        record_time: record_time.to_string(),
                        corroborated_by: Vec::new(),
                        parties: vec![w.party.clone()],
                        verifier_parties: Vec::new(),
                        supports: Vec::new(),
                        refutes: Vec::new(),
                        abstains: Vec::new(),
                        superseded_by: Vec::new(),
                        supersedes: Vec::new(),
                    },
                }),
            }
        }

        "verdict" => {
            need(w, "verify")?;
            let v = schema::verdict(body)?;
            let c = visible_claim(st, w, v.claim)?;
            // No self-certification: not the author, not a corroborator, and
            // not another identity of any of their parties.
            if c.author == writer || c.corroborated_by.iter().any(|x| x == writer) {
                return Err(err("E_SELF_VERIFY", "a writer cannot verify a claim it asserted"));
            }
            if c.parties.contains(&w.party) {
                return Err(err(
                    "E_SAME_PARTY_VERIFY",
                    "a claim cannot be verified by a party that asserted it",
                ));
            }
            if c.verifier_parties.contains(&w.party) {
                return Err(err("E_DUPLICATE", "this party already gave a verdict on this claim"));
            }
            done(Change::Verdict {
                event: event_id.to_string(),
                info: VerdictInfo {
                    claim: v.claim.to_string(),
                    result: v.result.to_string(),
                    writer: writer.to_string(),
                    seq: 0, // set at commit
                },
                party: w.party.clone(),
            })
        }

        "supersede" => {
            need(w, "write")?;
            let sp = schema::supersede(body)?;
            let old = visible_claim(st, w, sp.old)?;
            let new = visible_claim(st, w, sp.new)?;
            if old.compartment != new.compartment {
                return Err(err(
                    "E_COMPARTMENT",
                    "supersession must stay within one compartment",
                ));
            }
            // An open dispute is settled only by a neutral resolver, never by
            // stacking further supersessions on either side of it.
            // Evidence-backed refutation of a claim in an open dispute is
            // allowed and ends that dispute; anything else waits.
            let under_appeal =
                appeals_of(st, sp.old).chain(appeals_of(st, sp.new)).any(|(_, a)| a.is_open());
            if under_appeal {
                return Err(err(
                    "E_OPEN_DISPUTE",
                    "a claim under appeal cannot be superseded until the appeal is decided",
                ));
            }
            let open = involving(st, sp.old)
                .chain(involving(st, sp.new))
                .any(|(_, x)| x.is_open_dispute());
            let refutes_disputed_old =
                sp.kind == "refuted" && !involving(st, sp.new).any(|(_, x)| x.is_open_dispute());
            if open && !refutes_disputed_old {
                return Err(err(
                    "E_OPEN_DISPUTE",
                    "a claim in an open dispute cannot be superseded until the dispute is resolved",
                ));
            }
            let mut dispute_parties: Vec<String> = Vec::new();
            if sp.kind == "contested" {
                // A dispute needs two sides: no contesting your own claim,
                // and no dispute between claims that share a party.
                if old.parties.contains(&w.party) || old.parties.iter().any(|p| new.parties.contains(p)) {
                    return Err(err(
                        "E_SELF_DISPUTE",
                        "a party cannot contest its own claim or set up a dispute with itself",
                    ));
                }
                // Contests are argument, not evidence: a refuted or defeated
                // claim can't be revived through one, on either side. It can
                // only return as a new claim backed by evidence.
                // Consent: you can only put a claim on the new side if your
                // party asserted it; nobody is drawn into a dispute unasked.
                if !new.parties.contains(&w.party) {
                    return Err(err(
                        "E_CONSENT",
                        "the challenger's party must have asserted the new claim",
                    ));
                }
                if is_defeated(st, sp.old) || is_defeated(st, sp.new) {
                    return Err(err(
                        "E_FINAL",
                        "a refuted or defeated claim cannot enter a contest",
                    ));
                }
                dispute_parties.push(w.party.clone());
                for p in old.parties.iter().chain(&new.parties) {
                    if !dispute_parties.contains(p) {
                        dispute_parties.push(p.clone());
                    }
                }
                // One argument per party per claim: a party that has already
                // argued about the old claim in a ruled contest, had a stake in
                // a decided appeal on it, or ruled on it, can only challenge it
                // again with evidence (a refutation).
                if argued(st, sp.old).contains(&w.party) {
                    return Err(err(
                        "E_FINAL",
                        "a party that already challenged, refuted or ruled on this claim can only challenge it again with evidence",
                    ));
                }
                // A ruling on this pair is final for its parties, and a
                // withdrawal is final for the party that withdrew.
                let reargued = involving(st, sp.old).map(|(_, x)| x).any(|x| {
                    let same_pair = (x.old == sp.old && x.new == sp.new) || (x.old == sp.new && x.new == sp.old);
                    let withdrew = x.resolution.as_ref().map(|r| r.outcome == "withdrawn").unwrap_or(false)
                        && x.by_party == w.party;
                    same_pair && ((x.is_ruled() && x.dispute_parties.contains(&w.party)) || withdrew)
                });
                if reargued {
                    return Err(err(
                        "E_FINAL",
                        "a party to a ruling cannot re-argue the same pair of claims",
                    ));
                }
                // Liveness: never open a dispute nobody can resolve.
                let mut excluded = dispute_parties.clone();
                excluded.extend(verdict_parties(st, sp.old, None));
                excluded.extend(verdict_parties(st, sp.new, None));
                if !resolver_exists(st, &old.compartment, &excluded)
                    && !backstop_exists(st, &old.compartment, &dispute_parties)
                {
                    return Err(err(
                        "E_NO_RESOLVER",
                        "no registered writer could neutrally resolve this dispute",
                    ));
                }
            }
            for ev in &sp.evidence {
                if visible_item_compartment(st, w, ev)? != old.compartment {
                    return Err(err(
                        "E_COMPARTMENT",
                        format!("evidence '{ev}' is from another compartment"),
                    ));
                }
            }
            // Refutation must rest on consequence: a refuting verdict on the old claim.
            if sp.kind == "refuted" && !new.parties.contains(&w.party) {
                // Consent: you set your own claim against the old one; you
                // cannot conscript someone else's claim (or its parties) as
                // the opposing side.
                return Err(err(
                    "E_CONSENT",
                    "the refuter's party must have asserted the new claim",
                ));
            }
            if sp.kind == "refuted" {
                // After a ruling, the refuting verdict must also come from
                // outside that dispute.
                // After a ruling or appeal, the refuting verdict must come from
                // a party outside it, and after a reinstatement it must be new.
                let mut interested = interested_parties(st, sp.old);
                // A party to an open contest on the claim cannot win it with
                // its own verdict.
                for (_, x) in involving(st, sp.old).filter(|(_, x)| x.is_open_dispute()) {
                    interested.extend(x.dispute_parties.iter().cloned());
                }
                let reinstated_at = last_reinstatement(st, sp.old);
                let has_refutation = sp.evidence.iter().any(|ev| {
                    st.verdicts
                        .get(*ev)
                        .map(|v| {
                            v.claim == sp.old
                                && v.result == "refutes"
                                && !interested.contains(&st.writers[&v.writer].party)
                                && reinstated_at.map(|r| v.seq > r).unwrap_or(true)
                        })
                        .unwrap_or(false)
                });
                if !has_refutation {
                    return Err(err(
                        "E_EVIDENCE",
                        "'refuted' needs a refuting verdict on the old claim as evidence",
                    ));
                }
            }
            if sp.kind == "narrowed" && new.body.get("validity_domain").is_none() {
                return Err(err(
                    "E_EVIDENCE",
                    "'narrowed' needs the new claim to state its validity_domain",
                ));
            }
            // Contests are governed by the open-dispute and finality rules
            // (a withdrawn contest may be raised again); other kinds are
            // recorded once.
            let dup = sp.kind != "contested"
                && involving(st, sp.old).any(|(_, x)| x.old == sp.old && x.new == sp.new && x.kind == sp.kind);
            if dup {
                return Err(err("E_DUPLICATE", "this supersession is already recorded"));
            }
            let closes: Vec<(String, String)> = if sp.kind == "refuted" {
                involving(st, sp.old)
                    .filter(|(_, x)| x.is_open_dispute())
                    .map(|(id, x)| {
                        let outcome = if x.old == sp.old { "new" } else { "old" };
                        (id.clone(), outcome.to_string())
                    })
                    .collect()
            } else {
                Vec::new()
            };
            done(Change::Supersede {
                closes,
                event: event_id.to_string(),
                info: SupersessionInfo {
                    old: sp.old.to_string(),
                    new: sp.new.to_string(),
                    kind: sp.kind.to_string(),
                    compartment: old.compartment.clone(),
                    evidence: sp.evidence.iter().map(|x| x.to_string()).collect(),
                    by: writer.to_string(),
                    by_party: w.party.clone(),
                    record_time: record_time.to_string(),
                    seq: 0, // set at commit
                    dispute_parties,
                    resolution: None,
                },
            })
        }

        "resolve" => {
            let r = schema::resolve(body)?;
            // Withdrawing your own challenge needs only `write`; ruling needs `verify`.
            need(w, if r.outcome == "withdrawn" { "write" } else { "verify" })?;
            if let Some(a) = st
                .appeals
                .get(r.dispute)
                .filter(|a| w.in_compartment(&a.compartment))
            {
                if !matches!(r.outcome, "reinstated" | "upheld" | "withdrawn") {
                    return Err(err("E_RESOLVE", "an appeal is decided 'reinstated', 'upheld' or 'withdrawn'"));
                }
                if a.resolution.is_some() {
                    return Err(err("E_RESOLVE", "this appeal is already decided"));
                }
                if r.outcome == "withdrawn" {
                    if a.by_party != w.party {
                        return Err(err("E_RESOLVE_PARTY", "only the appellant's party may withdraw an appeal"));
                    }
                } else {
                    // Core stake: advocates and adversaries (frozen at filing,
                    // plus anyone who has since joined the other side).
                    let opp = opponents(st, &a.claim);
                    let verdicts = verdict_parties(st, &a.claim, None);
                    let core = |p: &str| a.appeal_parties.iter().any(|x| x == p) || opp.iter().any(|x| x == p);
                    let strict = |p: &str| !core(p) && !verdicts.iter().any(|x| x == p);
                    let ok = strict(&w.party)
                        || (w.can("govern")
                            && !core(&w.party)
                            && !st.writers.values().any(|x| {
                                x.can("verify") && x.in_compartment(&a.compartment) && strict(&x.party)
                            }));
                    if !ok {
                        return Err(err("E_RESOLVE_PARTY", "a party with a stake in an appeal cannot decide it"));
                    }
                }
                return done(Change::ResolveAppeal {
                    appeal: r.dispute.to_string(),
                    event: event_id.to_string(),
                    outcome: r.outcome.to_string(),
                    party: w.party.clone(),
                });
            }
            let sp = st
                .supersessions
                .get(r.dispute)
                .filter(|sp| w.in_compartment(&sp.compartment))
                .ok_or_else(|| not_found("dispute", r.dispute))?;
            if sp.kind != "contested" {
                return Err(err("E_RESOLVE", "only contests and appeals are resolved"));
            }
            if !matches!(r.outcome, "old" | "new" | "both" | "withdrawn") {
                return Err(err("E_RESOLVE", "a contest is decided 'old', 'new', 'both' or 'withdrawn'"));
            }
            if sp.resolution.is_some() {
                return Err(err("E_RESOLVE", "this dispute is already resolved"));
            }
            if r.outcome == "withdrawn" {
                // The way out that needs no third party: the challenger's
                // party may withdraw its own challenge.
                if sp.by_party != w.party {
                    return Err(err(
                        "E_RESOLVE_PARTY",
                        "only the challenger's party may withdraw a challenge",
                    ));
                }
            } else {
                // A party to the dispute (frozen when it opened), or one that
                // already gave a verdict on either claim, cannot rule on it;
                // if that leaves nobody, a `govern` writer outside the
                // dispute may rule as a last resort.
                let strict = |p: &str| {
                    !sp.dispute_parties.iter().any(|x| x == p)
                        && !verdict_parties(st, &sp.old, None).iter().any(|x| x == p)
                        && !verdict_parties(st, &sp.new, None).iter().any(|x| x == p)
                };
                let ok = strict(&w.party)
                    || (w.can("govern")
                        && !sp.dispute_parties.contains(&w.party)
                        && !st.writers.values().any(|x| {
                            x.can("verify") && x.in_compartment(&sp.compartment) && strict(&x.party)
                        }));
                if !ok {
                    return Err(err("E_RESOLVE_PARTY", "a party with a stake in a dispute cannot resolve it"));
                }
            }
            done(Change::Resolve {
                supersession: r.dispute.to_string(),
                event: event_id.to_string(),
                outcome: r.outcome.to_string(),
                party: w.party.clone(),
            })
        }

        "appeal" => {
            need(w, "write")?;
            let ap = schema::appeal(body)?;
            let c = visible_claim(st, w, ap.claim)?;
            let defeated_at = defeat_seq(st, ap.claim).ok_or_else(|| {
                err("E_APPEAL", "only a refuted or defeated claim can be appealed")
            })?;
            if appeals_of(st, ap.claim).any(|(_, a)| a.is_open()) {
                return Err(err("E_OPEN_DISPUTE", "this claim already has an open appeal"));
            }
            if appeals_of(st, ap.claim).any(|(_, a)| a.by_party == w.party) {
                return Err(err("E_FINAL", "a party may appeal a claim only once"));
            }
            // An opponent cannot appeal (no sham appeals that block the real
            // one and use up its evidence).
            let opp = opponents(st, ap.claim);
            if opp.contains(&w.party) {
                return Err(err("E_STAKE", "an opponent of a claim cannot appeal it"));
            }
            // Who has a stake: excluded from giving the evidence and from deciding.
            let mut stake: Vec<String> = vec![w.party.clone()];
            stake.extend(c.parties.iter().cloned());
            stake.extend(interested_parties(st, ap.claim));
            stake.extend(opp);
            let mut fresh = false;
            let mut evidence_parties = Vec::new();
            for ev in &ap.evidence {
                let v = st
                    .verdicts
                    .get(*ev)
                    .filter(|v| v.claim == ap.claim)
                    .ok_or_else(|| not_found("supporting verdict", ev))?;
                let party = st.writers[&v.writer].party.clone();
                if v.result != "supports" {
                    return Err(err("E_EVIDENCE", format!("'{ev}' does not support the claim")));
                }
                if stake.contains(&party) {
                    return Err(err(
                        "E_EVIDENCE",
                        format!("'{ev}' comes from a party with a stake in this claim"),
                    ));
                }
                fresh |= v.seq > defeated_at;
                evidence_parties.push(party);
            }
            // Eligible on new evidence (given after the defeat), or on the
            // balance of verdicts: more parties support than refute. Balance
            // only earns a hearing; a neutral resolver still decides.
            let balance_favours = c.supports.len() > c.refutes.len();
            if !fresh && !balance_favours {
                return Err(err(
                    "E_EVIDENCE",
                    "an appeal needs support given after the defeat, or more supporting than refuting parties",
                ));
            }
            stake.extend(evidence_parties);
            stake.sort();
            stake.dedup();
            // Ordinary judges must also not have given any verdict on it.
            let mut strict_excluded = stake.clone();
            strict_excluded.extend(verdict_parties(st, ap.claim, None));
            if !resolver_exists(st, &c.compartment, &strict_excluded)
                && !backstop_exists(st, &c.compartment, &stake)
            {
                return Err(err("E_NO_RESOLVER", "no registered writer could neutrally decide this appeal"));
            }
            done(Change::Appeal {
                event: event_id.to_string(),
                info: AppealInfo {
                    claim: ap.claim.to_string(),
                    compartment: c.compartment.clone(),
                    evidence: ap.evidence.iter().map(|x| x.to_string()).collect(),
                    by: writer.to_string(),
                    by_party: w.party.clone(),
                    record_time: record_time.to_string(),
                    seq: 0, // set at commit
                    appeal_parties: stake,
                    resolution: None,
                },
            })
        }

        "schema_change" => {
            need(w, "govern")?;
            let v = schema::schema_change(body)?;
            if v != st.schema_version + 1 {
                return Err(err("E_SCHEMA_VERSION", "schema version must advance by exactly one"));
            }
            done(Change::SchemaVersion(v))
        }

        _ => unreachable!("kind validated above"),
    }
}

fn writer_info(r: &schema::Register) -> WriterInfo {
    WriterInfo {
        party: r.party.to_string(),
        kind: r.kind.to_string(),
        capabilities: r.capabilities.iter().map(|c| c.to_string()).collect(),
        compartments: r.compartments.iter().map(|c| c.to_string()).collect(),
        last_seq: 0,
    }
}

// ------------------------------------------------------- derived views

impl ClaimInfo {
    /// Current truth, folded in log order over every supersession and
    /// resolution touching this claim (later events win):
    /// * refuted as the old claim: false
    /// * opened contested: contested
    /// * resolution: winner returns to its asserted truth, loser is false,
    ///   "both" returns both to their asserted truth
    /// * narrowed / generalised: no change (still true within its domain)
    pub fn effective_truth(&self, id: &str, st: &State) -> String {
        self.truth_and_cause(id, st).0
    }

    /// Effective truth and the events that decided it, enough to check it
    /// event by event:
    /// * nothing changed it: the claim's first event (its asserted truth)
    /// * refuted: the refutation
    /// * a contest ended: the contest and the event that ended it (a ruling,
    ///   or a refutation of the other side), plus the first event if the
    ///   claim won and so holds its asserted truth
    /// * reinstated on appeal: the appeal, the ruling, and the first event
    pub fn truth_and_cause(&self, id: &str, st: &State) -> (String, Vec<String>) {
        let mut actions: Vec<(usize, String, Vec<String>)> = Vec::new();
        let won = |cause: Vec<String>| {
            let mut c = cause;
            c.push(self.first_event.clone());
            c
        };
        for sid in self.superseded_by.iter().chain(&self.supersedes) {
            let sp = &st.supersessions[sid];
            let is_old = sp.old == id;
            if sp.kind == "refuted" && is_old {
                actions.push((sp.seq, "false".into(), vec![sid.clone()]));
            }
            // An open contest changes nothing (it shows as `disputed`); a
            // ruling makes the loser false and returns the winner to its
            // asserted truth; a withdrawal changes nothing.
            if let Some(r) = &sp.resolution {
                let wins = match r.outcome.as_str() {
                    "withdrawn" => continue,
                    "both" => true,
                    "old" => is_old,
                    _ => !is_old,
                };
                let cause = vec![sid.clone(), r.event.clone()];
                if wins {
                    actions.push((r.seq, self.asserted_truth.clone(), won(cause)));
                } else {
                    actions.push((r.seq, "false".into(), cause));
                }
            }
        }
        for (aid, a) in appeals_of(st, id) {
            if let Some(r) = &a.resolution {
                if r.outcome == "reinstated" {
                    actions.push((r.seq, self.asserted_truth.clone(), won(vec![aid.clone(), r.event.clone()])));
                }
            }
        }
        // Stable: of two actions in one event (a refutation that also ends
        // a contest), the later-listed wins, as in the original fold.
        actions.sort_by_key(|(seq, _, _)| *seq);
        actions
            .into_iter()
            .last()
            .map(|(_, t, e)| (t, e))
            .unwrap_or_else(|| (self.asserted_truth.clone(), vec![self.first_event.clone()]))
    }
}

impl State {
    /// State as a JSON value, restricted to `compartments` (None = all).
    /// A restricted view contains nothing from other compartments, not even
    /// their names.
    pub fn to_value(&self, compartments: Option<&[String]>) -> Value {
        let visible = |c: &str| compartments.map(|cs| cs.iter().any(|x| x == c)).unwrap_or(true);
        let strs = |v: &[String]| Value::Array(v.iter().map(|x| Value::str(x.clone())).collect());

        let claims: Vec<(String, Value)> = self
            .claims
            .iter()
            .filter(|(_, c)| visible(&c.compartment))
            .map(|(id, c)| {
                (
                    id.clone(),
                    Value::obj(vec![
                        ("body", c.body.clone()),
                        ("author", Value::str(c.author.clone())),
                        ("first_event", Value::str(c.first_event.clone())),
                        ("record_time", Value::str(c.record_time.clone())),
                        // Who recorded the source the claim quotes: turns are
                        // that writer's attribution, so readers see it.
                        (
                            "source_recorded_by",
                            c.body
                                .get("source_episode")
                                .and_then(|v| v.as_str())
                                .and_then(|e| self.episodes.get(e))
                                .map(|e| Value::str(e.writer.clone()))
                                .unwrap_or(Value::Null),
                        ),
                        ("effective_truth", Value::str(c.effective_truth(id, self))),
                        ("disputed", Value::Bool(is_disputed(self, id))),
                        ("defeated", Value::Bool(is_defeated(self, id))),
                        (
                            "balance",
                            Value::obj(vec![
                                ("supports", Value::Int(c.supports.len() as i64)),
                                ("refutes", Value::Int(c.refutes.len() as i64)),
                            ]),
                        ),
                        ("corroborated_by", strs(&c.corroborated_by)),
                        ("supports", strs(&c.supports)),
                        ("refutes", strs(&c.refutes)),
                        ("abstains", strs(&c.abstains)),
                        ("superseded_by", strs(&c.superseded_by)),
                        ("supersedes", strs(&c.supersedes)),
                    ]),
                )
            })
            .collect();

        let episodes: Vec<(String, Value)> = self
            .episodes
            .iter()
            .filter(|(_, e)| visible(&e.compartment))
            .map(|(id, e)| {
                (
                    id.clone(),
                    Value::obj(vec![
                        ("body", e.body.clone()),
                        ("writer", Value::str(e.writer.clone())),
                        ("record_time", Value::str(e.record_time.clone())),
                        ("hidden_chars", Value::Bool(e.hidden_chars)),
                    ]),
                )
            })
            .collect();

        let supersessions: Vec<(String, Value)> = self
            .supersessions
            .iter()
            .filter(|(_, sp)| visible(&sp.compartment))
            .map(|(id, sp)| {
                let resolution = match &sp.resolution {
                    None => Value::Null,
                    Some(r) => Value::obj(vec![
                        ("event", Value::str(r.event.clone())),
                        ("outcome", Value::str(r.outcome.clone())),
                    ]),
                };
                (
                    id.clone(),
                    Value::obj(vec![
                        ("old", Value::str(sp.old.clone())),
                        ("new", Value::str(sp.new.clone())),
                        ("kind", Value::str(sp.kind.clone())),
                        ("evidence", strs(&sp.evidence)),
                        ("by", Value::str(sp.by.clone())),
                        ("record_time", Value::str(sp.record_time.clone())),
                        ("resolution", resolution),
                    ]),
                )
            })
            .collect();

        let appeals: Vec<(String, Value)> = self
            .appeals
            .iter()
            .filter(|(_, a)| visible(&a.compartment))
            .map(|(id, a)| {
                let resolution = match &a.resolution {
                    None => Value::Null,
                    Some(r) => Value::obj(vec![
                        ("event", Value::str(r.event.clone())),
                        ("outcome", Value::str(r.outcome.clone())),
                    ]),
                };
                (
                    id.clone(),
                    Value::obj(vec![
                        ("claim", Value::str(a.claim.clone())),
                        ("evidence", strs(&a.evidence)),
                        ("by", Value::str(a.by.clone())),
                        ("record_time", Value::str(a.record_time.clone())),
                        ("resolution", resolution),
                    ]),
                )
            })
            .collect();

        let writers: Vec<(String, Value)> = self
            .writers
            .iter()
            .filter_map(|(id, w)| {
                let shown: Vec<String> = w
                    .compartments
                    .iter()
                    .filter(|c| visible(c))
                    .cloned()
                    .collect();
                if compartments.is_some() && shown.is_empty() {
                    return None;
                }
                let mut fields = vec![
                    ("party", Value::str(w.party.clone())),
                    ("kind", Value::str(w.kind.clone())),
                    ("capabilities", strs(&w.capabilities)),
                    ("compartments", strs(&shown)),
                ];
                // last_seq counts hidden events too, so only the full view shows it.
                if compartments.is_none() {
                    fields.push(("last_seq", Value::Int(w.last_seq)));
                }
                Some((id.clone(), Value::obj(fields)))
            })
            .collect();

        Value::obj(vec![
            ("schema_version", Value::Int(self.schema_version)),
            ("writers", Value::Object(writers)),
            ("episodes", Value::Object(episodes)),
            ("claims", Value::Object(claims)),
            ("supersessions", Value::Object(supersessions)),
            ("appeals", Value::Object(appeals)),
        ])
    }

    /// Is the claim in an open contest or appeal?
    pub fn disputed(&self, id: &str) -> bool {
        is_disputed(self, id)
    }

    /// The open contests and appeals that make `id` disputed (event ids).
    pub fn open_disputes(&self, id: &str) -> Vec<String> {
        let mut out: Vec<String> =
            involving(self, id).filter(|(_, sp)| sp.is_open_dispute()).map(|(k, _)| k.clone()).collect();
        out.extend(appeals_of(self, id).filter(|(_, a)| a.is_open()).map(|(k, _)| k.clone()));
        out
    }

    /// The events that establish `id` is currently defeated, if it is.
    pub fn defeated_by(&self, id: &str) -> Option<Vec<String>> {
        defeat(self, id).map(|(_, cause)| cause)
    }

    /// Effective truth of a claim by id.
    pub fn truth(&self, id: &str) -> Option<String> {
        self.claims.get(id).map(|c| c.effective_truth(id, self))
    }

    /// SHA-256 of the full canonical state: equal digests mean equal state.
    pub fn digest(&self) -> String {
        hex_digest(canonical(&self.to_value(None)).as_bytes())
    }
}
