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

#[derive(Clone, Debug, PartialEq)]
pub struct WriterInfo {
    /// Position of its registration in the log.
    pub seq: usize,
    pub party: String,
    pub kind: String,
    pub capabilities: Vec<String>,
    pub compartments: Vec<String>,
    pub last_seq: i64,
    pub name: Option<String>,
    pub model: Option<String>,
    pub operator: Option<String>,
    pub surface: Option<String>,
    pub session: Option<String>,
    /// The writer that started this instance, when it was not an owner.
    pub instance_of: Option<String>,
}

impl WriterInfo {
    /// What its mistakes are shared with: every instance of one model is in
    /// one group (same weights, correlated errors); each person, and each
    /// tool without a version, is a group of its own. Agreement counts as
    /// independent only across groups.
    /// (An instance is in its parent's group; see State::group.)
    fn own_group(&self, id: &str) -> String {
        match (self.kind.as_str(), &self.model) {
            ("model", Some(m)) => format!("model:{}", m.to_ascii_lowercase()),
            ("tool", Some(m)) => format!("tool:{}", m.to_ascii_lowercase()),
            (k, _) => format!("{k}:{id}"),
        }
    }
}

impl WriterInfo {
    fn can(&self, cap: &str) -> bool {
        self.capabilities.iter().any(|c| c == cap || c == "owner")
    }
    fn in_compartment(&self, c: &str) -> bool {
        self.compartments.iter().any(|x| x == c)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct EpisodeInfo {
    /// Position in the log.
    pub seq: usize,
    pub compartment: String,
    pub writer: String,
    pub record_time: String,
    pub hidden_chars: bool,
    pub body: Value,
    pub participants: Vec<String>,
    /// (speaker, start, end) in code points; empty if not recorded.
    pub turns: Vec<(String, usize, usize)>,
    /// Name -> registered writer, as the recorder said.
    pub bindings: Vec<(String, String)>,
}

impl EpisodeInfo {
    pub fn bound(&self, label: &str) -> Option<&str> {
        self.bindings
            .iter()
            .find(|(l, _)| l == label)
            .map(|(_, w)| w.as_str())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ClaimInfo {
    pub body: Value,
    pub compartment: String,
    pub asserted_truth: String,
    pub author: String,
    /// The writer the speaker is, settled when it was first recorded (see
    /// State::speaker_of); None if the speaker is only a name.
    pub speaker_is: Option<String>,
    pub first_event: String,
    /// Position of its first event in the log.
    pub seq: usize,
    /// Every event that asserted it (author first, then corroborators):
    /// (writer, position in the log).
    pub assertions: Vec<(String, usize)>,
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

#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Debug, PartialEq)]
pub struct VerdictInfo {
    pub claim: String,
    pub result: String,
    pub writer: String,
    pub seq: usize,
}

/// A part of the state (see State::resume_selection).
#[derive(Default, Debug)]
pub struct Selection {
    pub claims: std::collections::HashSet<String>,
    pub episodes: std::collections::HashSet<String>,
    pub handoffs: std::collections::HashSet<String>,
}

fn c_later<T>(seq: usize, cur: Option<(usize, T)>) -> bool {
    cur.map(|(s, _)| seq > s).unwrap_or(true)
}

#[derive(Clone, Debug, PartialEq)]
pub struct HandoffInfo {
    pub seq: usize,
    pub compartment: String,
    pub frame: String,
    pub writer: String,
    pub record_time: String,
    pub body: Value,
}

/// A position in an organisation (the latest definition of its title).
#[derive(Clone, Debug, PartialEq)]
pub struct PositionInfo {
    pub compartment: String,
    pub org: String,
    pub title: String,
    pub reports_to: Option<String>,
    pub duties: String,
    /// Who defined it last, and the event that did.
    pub by: String,
    pub event: String,
    pub seq: usize,
}

/// A writer appointed to a position; `ended` once dismissed or resigned:
/// (event, by whom, reason, when).
#[derive(Clone, Debug, PartialEq)]
pub struct AppointmentInfo {
    pub compartment: String,
    pub org: String,
    pub title: String,
    pub holder: String,
    pub by: String,
    pub record_time: String,
    pub seq: usize,
    pub ended: Option<(String, String, String, String)>,
}

/// The key of a position: compartment, organisation and title. An
/// organisation belongs to its compartment: the same name in two
/// compartments is two organisations, and neither can learn of the other.
pub fn position_key(compartment: &str, org: &str, title: &str) -> String {
    format!("{compartment}\u{1f}{org}\u{1f}{title}")
}

#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Debug, Default, PartialEq)]
pub struct State {
    pub writers: BTreeMap<String, WriterInfo>,
    pub episodes: BTreeMap<String, EpisodeInfo>,
    pub claims: BTreeMap<String, ClaimInfo>,
    pub supersessions: BTreeMap<String, SupersessionInfo>,
    pub verdicts: BTreeMap<String, VerdictInfo>,
    pub appeals: BTreeMap<String, AppealInfo>,
    pub handoffs: BTreeMap<String, HandoffInfo>,
    /// Positions by "org/title".
    pub positions: BTreeMap<String, PositionInfo>,
    /// Appointments by event id.
    pub appointments: BTreeMap<String, AppointmentInfo>,
    /// Appeal ids by claim, in log order (an index; not part of the view).
    pub appeals_by_claim: BTreeMap<String, Vec<String>>,
    /// Handoff id -> the handoff that follows it (issue #53; derived, like
    /// `appeals_by_claim`, and not part of the state digest).
    pub successors: BTreeMap<String, String>,
    pub schema_version: i64,
}

/// What a caller submits; the ledger assigns sequence numbers and hashes.
pub struct Draft {
    pub writer: String,
    pub record_time: String,
    pub kind: String,
    pub body: Value,
}

/// How far ahead of the gate's clock a fresh write may be dated (issue #52).
pub const MAX_FUTURE_SECS: i64 = 300;

/// The gate's clock plus `secs`, as a record-time string (UTC, whole seconds).
fn timestamp_after_now(secs: i64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
        + secs;
    let (days, rem) = (now.div_euclid(86_400), now.rem_euclid(86_400));
    let (y, m, d) = crate::recall::civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

#[derive(Default, Debug, PartialEq)]
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
            // A log is at version 1 unless its genesis says otherwise.
            state: State {
                schema_version: 1,
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
    ///
    /// A fresh write may not be dated more than `MAX_FUTURE_SECS` ahead of the
    /// gate's own clock (issue #52: one far-future event would otherwise lock
    /// the log, since record time never runs backwards). Only fresh writes
    /// are checked against the clock: replaying a log stays deterministic,
    /// and a log that already holds such an event still verifies.
    pub fn append(&mut self, draft: Draft) -> GateResult<Value> {
        if schema::is_timestamp(&draft.record_time) {
            let limit = timestamp_after_now(MAX_FUTURE_SECS);
            if draft.record_time.as_str() > limit.as_str() {
                return Err(err(
                    "E_FUTURE",
                    format!("record_time is more than {MAX_FUTURE_SECS} seconds ahead of the gate's clock"),
                ));
            }
        }
        let next_writer_seq = self
            .state
            .writers
            .get(&draft.writer)
            .map(|w| w.last_seq + 1)
            .unwrap_or(1);
        let env = Value::obj(vec![
            ("seq", Value::Int(self.events.len() as i64)),
            (
                "event_id",
                Value::str(format!("{}/{}", draft.writer, next_writer_seq)),
            ),
            ("writer", Value::str(draft.writer)),
            ("writer_seq", Value::Int(next_writer_seq)),
            ("record_time", Value::str(draft.record_time)),
            ("prev", Value::str(self.head())),
            ("kind", Value::str(draft.kind)),
            ("body", draft.body),
        ]);
        if let Some(k) = first_duplicate_key(&env) {
            return Err(err("E_ENVELOPE", format!("duplicate key '{k}'")));
        }
        let hash = envelope_hash(&env);
        let line = canonical(&env.with("hash", Value::str(hash.clone())));
        // Exactly the value a replay of the written line gives (members in
        // canonical order): the state never depends on how a draft was
        // built, only on the log.
        let env = crate::json::parse(&line)
            .map_err(|e| err("E_BODY", format!("event is not valid JSON: {e}")))?;
        self.apply_hashed(env.clone(), Some(hash))?;
        Ok(env)
    }

    /// The write gate: check every rule, then commit. State is untouched on
    /// any error.
    pub fn apply(&mut self, env: Value) -> GateResult<()> {
        self.apply_hashed(env, None)
    }

    /// `apply`, given the envelope hash already worked out from the exact
    /// canonical line (see store::parse_events): the same check, done once.
    pub fn apply_hashed(&mut self, env: Value, computed: Option<String>) -> GateResult<()> {
        let checked = check(
            &self.state,
            &env,
            self.events.len(),
            &self.head(),
            self.events.last(),
            computed,
        )?;
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
            let rt = env
                .get("record_time")
                .and_then(|v| v.as_str())
                .unwrap_or("");
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
    Register {
        writer: String,
        info: WriterInfo,
        version: Option<i64>,
    },
    Episode {
        id: String,
        info: EpisodeInfo,
    },
    NewClaim {
        id: String,
        info: ClaimInfo,
    },
    Corroborate {
        id: String,
        writer: String,
        party: String,
    },
    Verdict {
        event: String,
        info: VerdictInfo,
        party: String,
    },
    /// `closes`: open disputes this refutation ends, with the outcome each
    /// takes (the refuted claim loses).
    Supersede {
        event: String,
        info: SupersessionInfo,
        closes: Vec<(String, String)>,
    },
    Resolve {
        supersession: String,
        event: String,
        outcome: String,
        party: String,
    },
    Appeal {
        event: String,
        info: AppealInfo,
    },
    Handoff {
        event: String,
        info: HandoffInfo,
    },
    Position {
        info: PositionInfo,
    },
    Appoint {
        event: String,
        info: AppointmentInfo,
    },
    Dismiss {
        appointment: String,
        event: String,
        by: String,
        reason: String,
        time: String,
    },
    ResolveAppeal {
        appeal: String,
        event: String,
        outcome: String,
        party: String,
    },
    SchemaVersion(i64),
    /// Compartments taken from these writers (one revoked writer and every
    /// instance started from it).
    Revoke {
        writers: Vec<String>,
        compartments: Vec<String>,
    },
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
    err(
        "E_REF",
        format!("{what} '{id}' does not exist or is not accessible"),
    )
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

/// From protocol version 4 (issue #55): who may write into the frames a
/// resident's start packet is built from. `self/<w>` (who the resident is)
/// and `standing/<w>` (instructions for every future run) are written by
/// the owner only; `run/<w>/...` (the resident's own work: handoffs,
/// lessons, journal) by the resident, its own instances, or the owner.
/// Every other frame is free, as before. Measured in Polymechania (T12b-d):
/// a label on another writer's request protects the self far less than
/// keeping the request out of what the next run reads.
fn frame_gate(st: &State, writer: &str, w: &WriterInfo, frame: Option<&str>) -> GateResult<()> {
    if st.schema_version < 4 {
        return Ok(());
    }
    let Some((family, rest)) = frame.and_then(|f| f.split_once('/')) else {
        return Ok(());
    };
    if !matches!(family, "self" | "standing" | "run") {
        return Ok(());
    }
    if w.capabilities.iter().any(|c| c == "owner") {
        return Ok(());
    }
    let resident = rest.split('/').next().unwrap_or("");
    if family == "run" {
        // The writer itself, or the writer it is an instance of, and so on up.
        let mut cur = Some(writer);
        while let Some(id) = cur {
            if id == resident {
                return Ok(());
            }
            cur = st.writers.get(id).and_then(|x| x.instance_of.as_deref());
        }
        return Err(err(
            "E_CAPABILITY",
            format!("only '{resident}', its instances or the owner write in 'run/{resident}'"),
        ));
    }
    Err(err(
        "E_CAPABILITY",
        format!("only the owner writes '{family}/{resident}'"),
    ))
}

fn need_compartment(w: &WriterInfo, c: &str) -> GateResult<()> {
    if w.in_compartment(c) {
        Ok(())
    } else {
        Err(err(
            "E_COMPARTMENT",
            format!("no access to compartment '{c}'"),
        ))
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
        Change::Register {
            writer,
            mut info,
            version,
        } => {
            info.seq = seq;
            st.writers.insert(writer, info);
            if let Some(v) = version {
                st.schema_version = v;
            }
        }
        Change::Episode { id, mut info } => {
            info.seq = seq;
            st.episodes.insert(id, info);
        }
        Change::NewClaim { id, mut info } => {
            info.seq = seq;
            info.assertions.push((info.author.clone(), seq));
            st.claims.insert(id, info);
        }
        Change::Corroborate { id, writer, party } => {
            let c = st.claims.get_mut(&id).expect("checked");
            c.assertions.push((writer.clone(), seq));
            c.corroborated_by.push(writer);
            if !c.parties.contains(&party) {
                c.parties.push(party);
            }
        }
        Change::Verdict {
            event,
            mut info,
            party,
        } => {
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
        Change::Supersede {
            event,
            mut info,
            closes,
        } => {
            info.seq = seq;
            for (dispute, outcome) in closes {
                st.supersessions
                    .get_mut(&dispute)
                    .expect("checked")
                    .resolution = Some(Resolution {
                    event: event.clone(),
                    outcome,
                    party: info.by_party.clone(),
                    seq,
                    by_refutation: true,
                });
            }
            st.claims
                .get_mut(&info.old)
                .expect("checked")
                .superseded_by
                .push(event.clone());
            st.claims
                .get_mut(&info.new)
                .expect("checked")
                .supersedes
                .push(event.clone());
            st.supersessions.insert(event, info);
        }
        Change::Resolve {
            supersession,
            event,
            outcome,
            party,
        } => {
            st.supersessions
                .get_mut(&supersession)
                .expect("checked")
                .resolution = Some(Resolution {
                event,
                outcome,
                party,
                seq,
                by_refutation: false,
            });
        }
        Change::Handoff { event, mut info } => {
            info.seq = seq;
            if let Some(prev) = info.body.get("follows").and_then(|v| v.as_str()) {
                st.successors
                    .entry(prev.to_string())
                    .or_insert_with(|| event.clone());
            }
            st.handoffs.insert(event, info);
        }
        Change::Position { mut info } => {
            info.seq = seq;
            st.positions.insert(
                position_key(&info.compartment, &info.org, &info.title),
                info,
            );
        }
        Change::Appoint { event, mut info } => {
            info.seq = seq;
            st.appointments.insert(event, info);
        }
        Change::Dismiss {
            appointment,
            event,
            by,
            reason,
            time,
        } => {
            st.appointments
                .get_mut(&appointment)
                .expect("checked")
                .ended = Some((event, by, reason, time));
        }
        Change::Appeal { event, mut info } => {
            info.seq = seq;
            st.appeals_by_claim
                .entry(info.claim.clone())
                .or_default()
                .push(event.clone());
            st.appeals.insert(event, info);
        }
        Change::ResolveAppeal {
            appeal,
            event,
            outcome,
            party,
        } => {
            st.appeals.get_mut(&appeal).expect("checked").resolution = Some(Resolution {
                event,
                outcome,
                party,
                seq,
                by_refutation: false,
            });
        }
        Change::SchemaVersion(v) => st.schema_version = v,
        Change::Revoke { writers, compartments } => {
            for id in writers {
                if let Some(w) = st.writers.get_mut(&id) {
                    w.compartments.retain(|c| !compartments.contains(c));
                }
            }
        }
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
            if sp.old == claim {
                &sp.new
            } else {
                &sp.old
            }
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
        w.can("govern")
            && w.can("verify")
            && w.in_compartment(compartment)
            && !core.contains(&w.party)
    })
}

/// Open contest or appeal involving `claim`?
fn is_disputed(st: &State, claim: &str) -> bool {
    involving(st, claim).any(|(_, sp)| sp.is_open_dispute())
        || appeals_of(st, claim).any(|(_, a)| a.is_open())
}

/// Supersessions involving `claim` (as old or new), from its own lists:
/// cost proportional to the claim's history, not the whole log.
fn involving<'a>(
    st: &'a State,
    claim: &str,
) -> impl Iterator<Item = (&'a String, &'a SupersessionInfo)> + 'a {
    st.claims
        .get(claim)
        .into_iter()
        .flat_map(|c| c.superseded_by.iter().chain(&c.supersedes))
        .map(move |sid| (sid, &st.supersessions[sid]))
}

/// Appeals of `claim`, in log order.
fn appeals_of<'a>(
    st: &'a State,
    claim: &str,
) -> impl Iterator<Item = (&'a String, &'a AppealInfo)> + 'a {
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
            let lost =
                (r.outcome == "new" && sp.old == claim) || (r.outcome == "old" && sp.new == claim);
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
    computed: Option<String>,
) -> GateResult<Checked> {
    check_envelope_shape(env)?;

    // --- chain integrity
    let hash = s(env, "hash")?;
    if !schema::is_hex64(hash) || computed.unwrap_or_else(|| envelope_hash(env)) != hash {
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
        return Err(err(
            "E_TIME_FORMAT",
            "record_time must be a real YYYY-MM-DDTHH:MM:SSZ time",
        ));
    }
    if let Some(prev_rt) = last
        .and_then(|e| e.get("record_time"))
        .and_then(|v| v.as_str())
    {
        if record_time < prev_rt {
            return Err(err(
                "E_CLOCK",
                "record_time is earlier than the previous event",
            ));
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
        return Err(err(
            "E_EVENT_ID",
            "event_id must be '<writer>/<writer_seq>'",
        ));
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
        let r = schema::register(body, false)?;
        if r.writer != writer || !r.capabilities.contains(&"owner") {
            return Err(err(
                "E_GENESIS",
                "genesis must be an owner registering itself",
            ));
        }
        if writer_seq != 1 {
            return Err(err(
                "E_WRITER_SEQ",
                "first event of a writer must have writer_seq 1",
            ));
        }
        return done(Change::Register {
            writer: writer.to_string(),
            info: writer_info(&r),
            version: r.schema_version,
        });
    }

    let w = st.writers.get(writer).ok_or_else(|| {
        err(
            "E_UNKNOWN_WRITER",
            format!("writer '{writer}' is not registered"),
        )
    })?;
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
            // Owners register anyone. A writer with 'spawn' may start new
            // instances of itself (a session, an agent): same kind, model,
            // party and operator, within its own compartments, with no more
            // than its own capabilities and never owner, govern or spawn.
            let by_owner = w.capabilities.iter().any(|c| c == "owner");
            if !by_owner {
                need(w, "spawn")?;
            }
            let strict = st.schema_version >= 2;
            let r = schema::register(body, strict)?;
            if r.schema_version.is_some() {
                return Err(err(
                    "E_BODY",
                    "register: 'schema_version' belongs to the genesis registration only",
                ));
            }
            if !by_owner {
                // Everything that says who it is comes from its parent: an
                // instance cannot take another name, model or principal.
                let same = r.kind == w.kind
                    && r.model == w.model.as_deref()
                    && r.party == w.party
                    && r.operator == w.operator.as_deref()
                    && r.name == w.name.as_deref()
                    && (w.surface.is_none() || r.surface == w.surface.as_deref());
                if !same {
                    return Err(err(
                        "E_SPAWN",
                        "an instance has the kind, model, party, operator, name and surface of the writer that starts it",
                    ));
                }
                // Its id is its parent's, extended: an instance can never
                // take, or probe for, an id outside its parent's namespace.
                if !r.writer.starts_with(&format!("{writer}-")) {
                    return Err(err(
                        "E_SPAWN",
                        format!("an instance's id starts with '{writer}-'"),
                    ));
                }
                if r.compartments.iter().any(|c| !w.in_compartment(c)) {
                    return Err(err(
                        "E_SPAWN",
                        "an instance cannot enter a compartment its parent is not in",
                    ));
                }
                if r.capabilities.iter().any(|c| {
                    matches!(*c, "owner" | "govern" | "spawn")
                        || !w.capabilities.iter().any(|x| x == c)
                }) {
                    return Err(err("E_SPAWN", "an instance gets a subset of its parent's capabilities, never owner, govern or spawn"));
                }
            }
            if st.writers.contains_key(r.writer) {
                return Err(err(
                    "E_DUPLICATE",
                    format!("writer '{}' already registered", r.writer),
                ));
            }
            // An instance acts for a person who is already on record.
            if let Some(op) = r.operator {
                match st.writers.get(op) {
                    Some(o) if o.kind == "human" => {}
                    Some(_) => {
                        return Err(err("E_IDENTITY", format!("operator '{op}' is not a human")))
                    }
                    None => {
                        return Err(err(
                            "E_IDENTITY",
                            format!("operator '{op}' is not registered"),
                        ))
                    }
                }
            }
            // One session, one writer: otherwise its words could be split
            // between names, or two sessions' words merged under one.
            // (Checked within the party: a session id is the party's.)
            if let Some(sess) = r.session {
                if st.writers.values().any(|x| {
                    x.party == r.party
                        && x.session.as_deref() == Some(sess)
                        && x.surface.as_deref() == r.surface
                }) {
                    return Err(err(
                        "E_IDENTITY",
                        format!("session '{sess}' is already registered"),
                    ));
                }
            }
            // A name says who someone is: no party may take a name another
            // party's writer already has.
            if let Some(n) = r.name {
                if st
                    .writers
                    .values()
                    .any(|x| x.name.as_deref() == Some(n) && x.party != r.party)
                {
                    return Err(err(
                        "E_IDENTITY",
                        format!("the name '{n}' belongs to another party's writer"),
                    ));
                }
            }
            let mut info = writer_info(&r);
            if !by_owner {
                info.instance_of = Some(writer.to_string());
            }
            done(Change::Register {
                writer: r.writer.to_string(),
                info,
                version: None,
            })
        }

        "episode" => {
            need(w, "write")?;
            let e = schema::episode(body)?;
            need_compartment(w, e.compartment)?;
            for (label, x) in &e.bindings {
                // Only to writers in this compartment, with one answer for
                // "no such writer" and "not here", so nothing is learnt.
                if !st
                    .writers
                    .get(*x)
                    .map(|o| o.in_compartment(e.compartment))
                    .unwrap_or(false)
                {
                    return Err(err(
                        "E_IDENTITY",
                        format!(
                            "'{label}' is bound to '{x}', who is not a writer in this compartment"
                        ),
                    ));
                }
                // To bind a name to yourself is to claim those words as your
                // own: only your id or your registered name will do.
                if *x == writer && *label != writer && Some(*label) != w.name.as_deref() {
                    return Err(err(
                        "E_IDENTITY",
                        format!("'{label}' is not your id or registered name, so it cannot be bound to you"),
                    ));
                }
            }
            done(Change::Episode {
                id: event_id.to_string(),
                info: EpisodeInfo {
                    seq: 0,
                    compartment: e.compartment.to_string(),
                    writer: writer.to_string(),
                    record_time: record_time.to_string(),
                    hidden_chars: schema::has_hidden_chars(e.content),
                    body: body.clone(),
                    participants: e.participants.iter().map(|p| p.to_string()).collect(),
                    turns: e
                        .turns
                        .iter()
                        .map(|(sp, a, b)| (sp.to_string(), *a, *b))
                        .collect(),
                    bindings: e
                        .bindings
                        .iter()
                        .map(|(l, x)| (l.to_string(), x.to_string()))
                        .collect(),
                },
            })
        }

        "claim" => {
            need(w, "write")?;
            let c = schema::claim(body)?;
            need_compartment(w, c.compartment)?;
            frame_gate(st, writer, w, body.get("frame").and_then(|v| v.as_str()))?;
            if st.schema_version >= 2 {
                if let Some(fr) = body.get("frame").and_then(|v| v.as_str()) {
                    schema::one_line(fr, "claim: 'frame'")?;
                }
            }
            // References point at claims the writer can see, in the same
            // compartment (a thread never crosses one); "answers" points at
            // a question. Missing and elsewhere are refused alike.
            for (target, relation) in &c.about {
                let t = visible_claim(st, w, target)?;
                if t.compartment != c.compartment {
                    return Err(not_found("claim", target));
                }
                if *relation == "answers"
                    && t.body.get("stance").and_then(|v| v.as_str()) != Some("asked")
                {
                    return Err(err(
                        "E_REFERENCE",
                        format!("claim '{target}' is not a question, so nothing answers it"),
                    ));
                }
            }
            // Did the writer's own party record the episode it quotes?
            let mut self_recorded = false;
            // Who the speaker is. Quoting an episode: whoever the recorder
            // bound that name to, and nobody otherwise (a name in a
            // recording is only a name). Not quoting: a writer id given as
            // the speaker, or the writer's own registered name.
            let mut speaker_is: Option<String> = if c.source_episode.is_none() {
                st.writers
                    .contains_key(c.speaker)
                    .then(|| c.speaker.to_string())
            } else {
                None
            };
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
                self_recorded = st
                    .writers
                    .get(&e.writer)
                    .map(|x| x.party == w.party)
                    .unwrap_or(false);
                if let Some(b) = e.bound(c.speaker) {
                    speaker_is = Some(b.to_string());
                }
                // The quote must be exactly what the episode says at that
                // position: an extractor cannot put words in anyone's mouth.
                if let Some((quote, start)) = c.quote {
                    let content = e.body.get("content").and_then(|v| v.as_str()).unwrap_or("");
                    let found: String = content
                        .chars()
                        .skip(start as usize)
                        .take(quote.chars().count())
                        .collect();
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
                        e.turns
                            .iter()
                            .any(|(sp, ts, te)| *ts <= a && b <= *te && sp == c.speaker)
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
            if speaker_is.is_none()
                && c.source_episode.is_none()
                && w.name.as_deref() == Some(c.speaker)
            {
                speaker_is = Some(writer.to_string());
            }
            let id = claim_id(body);
            // Who the speaker is was settled when the claim was first
            // recorded; another writer repeating it cannot change that.
            if let Some(existing) = st.claims.get(&id) {
                speaker_is = existing.speaker_is.clone();
            }
            // Only the writer itself is "own": not another session of the
            // same model, not its party speaking in general, and not a name
            // that merely looks like it. (Version 1 logs keep their rule:
            // the writer's id or party, in any letter case.)
            let own = speaker_is.as_deref() == Some(writer)
                || (st.schema_version < 2
                    && (c.speaker.to_lowercase() == writer.to_lowercase()
                        || c.speaker.to_lowercase() == w.party.to_lowercase()));
            if c.origin != "direct" && !own && c.confidence > schema::PARAPHRASE_MAX_CONFIDENCE {
                return Err(err(
                    "E_ATTRIBUTION",
                    format!(
                        "a claim about what '{}' said, by a writer who is not them and without quoting them, cannot carry confidence above {}",
                        c.speaker,
                        schema::PARAPHRASE_MAX_CONFIDENCE
                    ),
                ));
            }
            // A quote is only as good as its recording. Quoting someone else
            // from an episode the writer's own party recorded is still that
            // party's account of what was said: the same cap applies.
            if c.quote.is_some()
                && self_recorded
                && !own
                && c.confidence > schema::PARAPHRASE_MAX_CONFIDENCE
            {
                return Err(err(
                    "E_ATTRIBUTION",
                    format!(
                        "quoting '{}' from an episode the writer's own party recorded cannot carry confidence above {}",
                        c.speaker,
                        schema::PARAPHRASE_MAX_CONFIDENCE
                    ),
                ));
            }
            match st.claims.get(&id) {
                // Identical content from another writer corroborates; it never overwrites.
                Some(existing) => {
                    if existing.author == writer
                        || existing.corroborated_by.iter().any(|x| x == writer)
                    {
                        return Err(err(
                            "E_DUPLICATE",
                            "this writer already recorded this claim",
                        ));
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
                        speaker_is,
                        first_event: event_id.to_string(),
                        seq: 0,
                        assertions: Vec::new(),
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
                return Err(err(
                    "E_SELF_VERIFY",
                    "a writer cannot verify a claim it asserted",
                ));
            }
            if c.parties.contains(&w.party) {
                return Err(err(
                    "E_SAME_PARTY_VERIFY",
                    "a claim cannot be verified by a party that asserted it",
                ));
            }
            if c.verifier_parties.contains(&w.party) {
                return Err(err(
                    "E_DUPLICATE",
                    "this party already gave a verdict on this claim",
                ));
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
            let under_appeal = appeals_of(st, sp.old)
                .chain(appeals_of(st, sp.new))
                .any(|(_, a)| a.is_open());
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
                if old.parties.contains(&w.party)
                    || old.parties.iter().any(|p| new.parties.contains(p))
                {
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
                    let same_pair = (x.old == sp.old && x.new == sp.new)
                        || (x.old == sp.new && x.new == sp.old);
                    let withdrew = x
                        .resolution
                        .as_ref()
                        .map(|r| r.outcome == "withdrawn")
                        .unwrap_or(false)
                        && x.by_party == w.party;
                    same_pair
                        && ((x.is_ruled() && x.dispute_parties.contains(&w.party)) || withdrew)
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
                && involving(st, sp.old)
                    .any(|(_, x)| x.old == sp.old && x.new == sp.new && x.kind == sp.kind);
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
            need(
                w,
                if r.outcome == "withdrawn" {
                    "write"
                } else {
                    "verify"
                },
            )?;
            if let Some(a) = st
                .appeals
                .get(r.dispute)
                .filter(|a| w.in_compartment(&a.compartment))
            {
                if !matches!(r.outcome, "reinstated" | "upheld" | "withdrawn") {
                    return Err(err(
                        "E_RESOLVE",
                        "an appeal is decided 'reinstated', 'upheld' or 'withdrawn'",
                    ));
                }
                if a.resolution.is_some() {
                    return Err(err("E_RESOLVE", "this appeal is already decided"));
                }
                if r.outcome == "withdrawn" {
                    if a.by_party != w.party {
                        return Err(err(
                            "E_RESOLVE_PARTY",
                            "only the appellant's party may withdraw an appeal",
                        ));
                    }
                } else {
                    // Core stake: advocates and adversaries (frozen at filing,
                    // plus anyone who has since joined the other side).
                    let opp = opponents(st, &a.claim);
                    let verdicts = verdict_parties(st, &a.claim, None);
                    let core = |p: &str| {
                        a.appeal_parties.iter().any(|x| x == p) || opp.iter().any(|x| x == p)
                    };
                    let strict = |p: &str| !core(p) && !verdicts.iter().any(|x| x == p);
                    let ok = strict(&w.party)
                        || (w.can("govern")
                            && !core(&w.party)
                            && !st.writers.values().any(|x| {
                                x.can("verify")
                                    && x.in_compartment(&a.compartment)
                                    && strict(&x.party)
                            }));
                    if !ok {
                        return Err(err(
                            "E_RESOLVE_PARTY",
                            "a party with a stake in an appeal cannot decide it",
                        ));
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
                return Err(err(
                    "E_RESOLVE",
                    "a contest is decided 'old', 'new', 'both' or 'withdrawn'",
                ));
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
                    return Err(err(
                        "E_RESOLVE_PARTY",
                        "a party with a stake in a dispute cannot resolve it",
                    ));
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
                err(
                    "E_APPEAL",
                    "only a refuted or defeated claim can be appealed",
                )
            })?;
            if appeals_of(st, ap.claim).any(|(_, a)| a.is_open()) {
                return Err(err(
                    "E_OPEN_DISPUTE",
                    "this claim already has an open appeal",
                ));
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
                    return Err(err(
                        "E_EVIDENCE",
                        format!("'{ev}' does not support the claim"),
                    ));
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
                return Err(err(
                    "E_NO_RESOLVER",
                    "no registered writer could neutrally decide this appeal",
                ));
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

        "handoff" => {
            need(w, "write")?;
            let c = schema::handoff(body)?;
            need_compartment(w, c.compartment)?;
            frame_gate(st, writer, w, Some(c.frame))?;
            // What it rests on is in its own compartment; missing and
            // elsewhere are refused alike.
            for id in &c.refs {
                let comp = st
                    .claims
                    .get(*id)
                    .map(|x| x.compartment.as_str())
                    .or_else(|| st.episodes.get(*id).map(|x| x.compartment.as_str()))
                    .or_else(|| st.handoffs.get(*id).map(|x| x.compartment.as_str()));
                if comp != Some(c.compartment) {
                    return Err(not_found("claim, episode or handoff", id));
                }
            }
            if let Some(prev) = c.follows {
                match st.handoffs.get(prev) {
                    Some(p) if p.compartment == c.compartment => {
                        if p.frame != c.frame {
                            return Err(err(
                                "E_REFERENCE",
                                "a handoff follows one in the same frame",
                            ));
                        }
                        // From protocol version 3, a handoff has at most one
                        // successor (issue #53): a second run continuing the
                        // same work is refused instead of forking silently.
                        if st.schema_version >= 3 && st.successors.contains_key(prev) {
                            return Err(err(
                                "E_FORK",
                                "another handoff already follows this one; follow the latest",
                            ));
                        }
                    }
                    _ => return Err(not_found("handoff", prev)),
                }
            }
            done(Change::Handoff {
                event: event_id.to_string(),
                info: HandoffInfo {
                    seq: 0,
                    compartment: c.compartment.to_string(),
                    frame: c.frame.to_string(),
                    writer: writer.to_string(),
                    record_time: record_time.to_string(),
                    body: body.clone(),
                },
            })
        }

        "position" => {
            need(w, "write")?;
            let p = schema::position(body)?;
            need_compartment(w, p.compartment)?;
            let (c, o) = (p.compartment, p.org);
            let key = position_key(c, o, p.title);
            if let Some(up) = p.reports_to {
                if !st.positions.contains_key(&position_key(c, o, up)) {
                    return Err(not_found("position", up));
                }
                // No cycles: the title must not be above the one it reports to.
                if up == p.title || st.chain_above(c, o, up).iter().any(|t| t == p.title) {
                    return Err(err("E_ORG", "a position cannot report to one below it"));
                }
            }
            // Who may define it: an owner or governor, or the holder of a
            // position it reports to, directly or through others. For an
            // existing title the same must hold of where it reported before.
            let governs = st.governs_org(w, c, o);
            let may = |up: Option<&str>| {
                governs || up.is_some_and(|u| st.holds_at_or_above(writer, c, o, u))
            };
            if !may(p.reports_to) {
                return Err(err(
                    "E_ORG",
                    "only an owner, a governor or a holder of a position above may define it",
                ));
            }
            if let Some(old) = st.positions.get(&key) {
                if !may(old.reports_to.as_deref()) {
                    return Err(err(
                        "E_ORG",
                        "only an owner, a governor or a holder of a position above may redefine it",
                    ));
                }
            }
            done(Change::Position {
                info: PositionInfo {
                    compartment: p.compartment.to_string(),
                    org: p.org.to_string(),
                    title: p.title.to_string(),
                    reports_to: p.reports_to.map(str::to_string),
                    duties: p.duties.to_string(),
                    by: writer.to_string(),
                    event: event_id.to_string(),
                    seq: 0,
                },
            })
        }

        "appoint" => {
            need(w, "write")?;
            let a = schema::appoint(body)?;
            need_compartment(w, a.compartment)?;
            let pos = st
                .positions
                .get(&position_key(a.compartment, a.org, a.title))
                .ok_or_else(|| not_found("position", a.title))?;
            if !st
                .writers
                .get(a.holder)
                .map(|h| h.in_compartment(a.compartment))
                .unwrap_or(false)
            {
                return Err(not_found("writer", a.holder));
            }
            let governs = st.governs_org(w, a.compartment, a.org);
            if a.holder == writer && !governs {
                return Err(err("E_ORG", "no one appoints themselves"));
            }
            if !governs
                && !pos
                    .reports_to
                    .as_deref()
                    .is_some_and(|u| st.holds_at_or_above(writer, a.compartment, a.org, u))
            {
                return Err(err(
                    "E_ORG",
                    "only an owner, a governor or a holder of a position above may appoint to it",
                ));
            }
            if st.appointments.values().any(|x| {
                x.ended.is_none()
                    && x.compartment == a.compartment
                    && x.org == a.org
                    && x.title == a.title
                    && x.holder == a.holder
            }) {
                return Err(err("E_DUPLICATE", "already holds this position"));
            }
            done(Change::Appoint {
                event: event_id.to_string(),
                info: AppointmentInfo {
                    compartment: a.compartment.to_string(),
                    org: a.org.to_string(),
                    title: a.title.to_string(),
                    holder: a.holder.to_string(),
                    by: writer.to_string(),
                    record_time: record_time.to_string(),
                    seq: 0,
                    ended: None,
                },
            })
        }

        "dismiss" => {
            need(w, "write")?;
            let d = schema::dismiss(body)?;
            let ap = st
                .appointments
                .get(d.appointment)
                .filter(|x| w.in_compartment(&x.compartment))
                .ok_or_else(|| not_found("appointment", d.appointment))?;
            if ap.ended.is_some() {
                return Err(err("E_ORG", "that appointment has already ended"));
            }
            let governs = st.governs_org(w, &ap.compartment, &ap.org);
            let above = st
                .positions
                .get(&position_key(&ap.compartment, &ap.org, &ap.title))
                .and_then(|p| p.reports_to.as_deref())
                .is_some_and(|u| st.holds_at_or_above(writer, &ap.compartment, &ap.org, u));
            // Resigning is always allowed.
            if !(governs || above || ap.holder == writer) {
                return Err(err("E_ORG", "only an owner, a governor, a holder of a position above, or the holder, may end it"));
            }
            done(Change::Dismiss {
                appointment: d.appointment.to_string(),
                event: event_id.to_string(),
                by: writer.to_string(),
                reason: d.reason.to_string(),
                time: record_time.to_string(),
            })
        }

        "revoke" => {
            // Issue #49: access granted by mistake, or to a writer that later
            // misbehaves, can be withdrawn. From then on the writer neither
            // reads nor writes those compartments (reads follow a writer's
            // current compartments); everything it wrote before stays.
            if st.schema_version < 5 {
                return Err(err("E_SCHEMA_VERSION", "revoke needs protocol version 5"));
            }
            if !w.capabilities.iter().any(|c| c == "owner") {
                return Err(err("E_CAPABILITY", "only the owner revokes"));
            }
            let r = schema::revoke(body)?;
            let target = st
                .writers
                .get(r.writer)
                .ok_or_else(|| err("E_UNKNOWN_WRITER", format!("writer '{}' is not registered", r.writer)))?;
            if target.capabilities.iter().any(|c| c == "owner") {
                return Err(err("E_REVOKE", "an owner's access is not revoked (it could lock the record)"));
            }
            for c in &r.compartments {
                if !target.in_compartment(c) {
                    return Err(err(
                        "E_REVOKE",
                        format!("'{}' is not in compartment '{c}'", r.writer),
                    ));
                }
            }
            // The writer and every instance started from it, at any depth.
            let mut writers = vec![r.writer.to_string()];
            let mut grew = true;
            while grew {
                grew = false;
                for (id, x) in &st.writers {
                    if let Some(parent) = &x.instance_of {
                        if writers.contains(parent) && !writers.contains(id) {
                            writers.push(id.clone());
                            grew = true;
                        }
                    }
                }
            }
            done(Change::Revoke {
                writers,
                compartments: r.compartments.iter().map(|c| c.to_string()).collect(),
            })
        }

        "schema_change" => {
            need(w, "govern")?;
            let v = schema::schema_change(body)?;
            if v != st.schema_version + 1 {
                return Err(err(
                    "E_SCHEMA_VERSION",
                    "schema version must advance by exactly one",
                ));
            }
            done(Change::SchemaVersion(v))
        }

        _ => unreachable!("kind validated above"),
    }
}

fn writer_info(r: &schema::Register) -> WriterInfo {
    let own = |x: Option<&str>| x.map(|s| s.to_string());
    WriterInfo {
        seq: 0,
        party: r.party.to_string(),
        kind: r.kind.to_string(),
        capabilities: r.capabilities.iter().map(|c| c.to_string()).collect(),
        compartments: r.compartments.iter().map(|c| c.to_string()).collect(),
        last_seq: 0,
        name: own(r.name),
        model: own(r.model),
        operator: own(r.operator),
        surface: own(r.surface),
        session: own(r.session),
        instance_of: None,
    }
}

// ------------------------------------------------------- derived views

impl State {
    /// A writer's independence group (see WriterInfo::own_group); an
    /// instance is always in its parent's group, so starting instances
    /// never makes agreement look independent.
    pub fn group(&self, id: &str) -> String {
        match self.writers.get(id) {
            Some(w) => match &w.instance_of {
                Some(p) if p != id => self.group(p),
                _ => w.own_group(id),
            },
            None => format!("unknown:{id}"),
        }
    }

    /// Who a claim's speaker is, as the record says (as the gate decides
    /// it for the author): quoting an episode, the writer the name is bound
    /// to there; otherwise a writer id given as the speaker, or the author
    /// if the speaker is the author's registered name. None: only a name.
    pub fn speaker_of(&self, c: &ClaimInfo) -> Option<String> {
        c.speaker_is.clone()
    }
}

impl ClaimInfo {
    /// The distinct groups (see WriterInfo::group) of everyone who asserted
    /// it, in order: two sessions of one model asserting it are one group.
    pub fn groups(&self, st: &State) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        for w in std::iter::once(&self.author).chain(self.corroborated_by.iter()) {
            if st.writers.contains_key(w) {
                let g = st.group(w);
                if !out.contains(&g) {
                    out.push(g);
                }
            }
        }
        out
    }

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
                    actions.push((
                        r.seq,
                        self.asserted_truth.clone(),
                        won(vec![aid.clone(), r.event.clone()]),
                    ));
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
    /// The titles above `title` in organisation `org` of `compartment`,
    /// nearest first (its own not included).
    pub fn chain_above(&self, compartment: &str, org: &str, title: &str) -> Vec<String> {
        let mut out = Vec::new();
        let mut cur = self
            .positions
            .get(&position_key(compartment, org, title))
            .and_then(|p| p.reports_to.clone());
        while let Some(t) = cur {
            if out.contains(&t) || out.len() > self.positions.len() {
                break; // defensive: the gate refuses cycles
            }
            cur = self
                .positions
                .get(&position_key(compartment, org, &t))
                .and_then(|p| p.reports_to.clone());
            out.push(t);
        }
        out
    }

    /// Whether writer `w` governs organisation `org` of `compartment`: it
    /// is an owner or governor of the party that founded it (the party of
    /// whoever defined its first position), or of any party if it has no
    /// positions yet. Other parties' owners act in it only through
    /// positions they hold.
    pub fn governs_org(&self, w: &WriterInfo, compartment: &str, org: &str) -> bool {
        if !w.capabilities.iter().any(|c| c == "owner" || c == "govern") {
            return false;
        }
        let founder = self
            .positions
            .values()
            .filter(|p| p.compartment == compartment && p.org == org)
            .min_by_key(|p| p.seq)
            .and_then(|p| self.writers.get(&p.by))
            .map(|f| f.party.as_str());
        founder.map_or(true, |f| f == w.party)
    }

    /// Whether `writer` currently holds `title` in organisation `org` of
    /// `compartment`, or a position above it.
    pub fn holds_at_or_above(
        &self,
        writer: &str,
        compartment: &str,
        org: &str,
        title: &str,
    ) -> bool {
        let mut line = vec![title.to_string()];
        line.extend(self.chain_above(compartment, org, title));
        self.appointments.values().any(|a| {
            a.ended.is_none()
                && a.holder == writer
                && a.compartment == compartment
                && a.org == org
                && line.contains(&a.title)
        })
    }

    /// State as a JSON value, restricted to `compartments` (None = all).
    /// A restricted view contains nothing from other compartments, not even
    /// their names.
    pub fn to_value(&self, compartments: Option<&[String]>) -> Value {
        self.to_value_sel(compartments, None)
    }

    /// The pieces of work (frames) visible through `compartments`, most
    /// recently active first: last activity, counts, latest handoff.
    pub fn frames(&self, compartments: &[String]) -> Value {
        let seen = |c: &str| compartments.iter().any(|x| x == c);
        // frame -> (last seq, last time, claims, handoffs, latest handoff)
        let mut f: BTreeMap<&str, (usize, &str, i64, i64, Option<(usize, &str)>)> = BTreeMap::new();
        for c in self.claims.values().filter(|c| seen(&c.compartment)) {
            let fr = c.body.get("frame").and_then(|v| v.as_str()).unwrap_or("");
            let e = f.entry(fr).or_insert((0, "", 0, 0, None));
            e.2 += 1;
            if c.seq >= e.0 {
                e.0 = c.seq;
                e.1 = &c.record_time;
            }
        }
        for (id, h) in self.handoffs.iter().filter(|(_, h)| seen(&h.compartment)) {
            let e = f.entry(h.frame.as_str()).or_insert((0, "", 0, 0, None));
            e.3 += 1;
            if c_later(h.seq, e.4) {
                e.4 = Some((h.seq, id.as_str()));
            }
            if h.seq >= e.0 {
                e.0 = h.seq;
                e.1 = &h.record_time;
            }
        }
        let mut rows: Vec<_> = f.into_iter().collect();
        rows.sort_by(|a, b| b.1 .0.cmp(&a.1 .0));
        Value::Array(
            rows.into_iter()
                .map(|(fr, (_, t, nc, nh, lh))| {
                    Value::obj(vec![
                        ("frame", Value::str(fr)),
                        ("last_activity", Value::str(t)),
                        ("claims", Value::Int(nc)),
                        ("handoffs", Value::Int(nh)),
                        (
                            "latest_handoff",
                            lh.map(|(_, id)| Value::str(id)).unwrap_or(Value::Null),
                        ),
                    ])
                })
                .collect(),
        )
    }

    /// What resuming a frame needs, and only that: its handoffs and claims,
    /// what its handoffs rest on, and the latest conversation (that of the
    /// latest handoff's writer, else of the latest conversation's), enough
    /// episodes for `turns` turns. Returns the selection and the frame (the
    /// most recently active one if none is given).
    pub fn resume_selection(
        &self,
        compartments: &[String],
        frame: Option<&str>,
        turns: usize,
    ) -> (Selection, Option<String>) {
        let seen = |c: &str| compartments.iter().any(|x| x == c);
        let frame: Option<String> = match frame {
            Some(f) => Some(f.to_string()),
            None => {
                let a = self
                    .claims
                    .values()
                    .filter(|c| seen(&c.compartment))
                    .map(|c| {
                        (
                            c.seq,
                            c.body
                                .get("frame")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string(),
                        )
                    });
                let b = self
                    .handoffs
                    .values()
                    .filter(|h| seen(&h.compartment))
                    .map(|h| (h.seq, h.frame.clone()));
                a.chain(b).max_by_key(|x| x.0).map(|x| x.1)
            }
        };
        let mut sel = Selection::default();
        let mut latest: Option<(usize, &str)> = None;
        if let Some(fr) = frame.as_deref() {
            for (id, h) in self
                .handoffs
                .iter()
                .filter(|(_, h)| seen(&h.compartment) && h.frame == fr)
            {
                sel.handoffs.insert(id.clone());
                if c_later(h.seq, latest) {
                    latest = Some((h.seq, h.writer.as_str()));
                }
                for r in h.body.get("refs").and_then(|v| v.as_array()).unwrap_or(&[]) {
                    if let Some(r) = r.as_str() {
                        sel.claims.insert(r.to_string());
                        sel.episodes.insert(r.to_string());
                        sel.handoffs.insert(r.to_string());
                    }
                }
            }
            for (id, c) in self.claims.iter().filter(|(_, c)| seen(&c.compartment)) {
                if c.body.get("frame").and_then(|v| v.as_str()) == Some(fr) {
                    sel.claims.insert(id.clone());
                }
            }
            // And what refers to them from elsewhere (a critique, an answer
            // filed under another frame).
            for (id, c) in self.claims.iter().filter(|(_, c)| seen(&c.compartment)) {
                let refs = c
                    .body
                    .get("about")
                    .and_then(|v| v.as_array())
                    .unwrap_or(&[]);
                if refs.iter().any(|r| {
                    r.get("claim")
                        .and_then(|v| v.as_str())
                        .is_some_and(|t| sel.claims.contains(t))
                }) {
                    sel.claims.insert(id.clone());
                }
            }
        }
        let mut eps: Vec<(&String, &EpisodeInfo)> = self
            .episodes
            .iter()
            .filter(|(_, e)| seen(&e.compartment))
            .collect();
        eps.sort_by_key(|(_, e)| std::cmp::Reverse(e.seq));
        let writer = latest
            .map(|x| x.1.to_string())
            .or_else(|| eps.first().map(|(_, e)| e.writer.clone()));
        let mut got = 0;
        for (id, e) in eps {
            if got >= turns {
                break;
            }
            if Some(&e.writer) == writer.as_ref() {
                sel.episodes.insert(id.clone());
                got += e.turns.len().max(1);
            }
        }
        (sel, frame)
    }

    pub fn to_value_sel(&self, compartments: Option<&[String]>, sel: Option<&Selection>) -> Value {
        // The order in which visible claims and handoffs were recorded, as a
        // rank among them: it says what came first without saying how many
        // hidden events lie between.
        let order: BTreeMap<usize, i64> = {
            let seen = |c: &str| {
                compartments
                    .map(|cs| cs.iter().any(|x| x == c))
                    .unwrap_or(true)
            };
            let mut seqs: Vec<usize> = self
                .claims
                .values()
                .filter(|c| seen(&c.compartment))
                .map(|c| c.seq)
                .chain(
                    self.handoffs
                        .values()
                        .filter(|h| seen(&h.compartment))
                        .map(|h| h.seq),
                )
                .chain(
                    self.episodes
                        .values()
                        .filter(|e| seen(&e.compartment))
                        .map(|e| e.seq),
                )
                .collect();
            seqs.sort_unstable();
            seqs.into_iter()
                .enumerate()
                .map(|(i, s)| (s, i as i64))
                .collect()
        };
        let visible = |c: &str| {
            compartments
                .map(|cs| cs.iter().any(|x| x == c))
                .unwrap_or(true)
        };
        let strs = |v: &[String]| Value::Array(v.iter().map(|x| Value::str(x.clone())).collect());

        let claims: Vec<(String, Value)> = self
            .claims
            .iter()
            .filter(|(id, c)| {
                visible(&c.compartment) && sel.map_or(true, |s| s.claims.contains(*id))
            })
            .map(|(id, c)| {
                (
                    id.clone(),
                    Value::obj(vec![
                        ("body", c.body.clone()),
                        ("author", Value::str(c.author.clone())),
                        ("first_event", Value::str(c.first_event.clone())),
                        ("order", Value::Int(order[&c.seq])),
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
                        ("independent_groups", strs(&c.groups(self))),
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
            .filter(|(id, e)| {
                visible(&e.compartment) && sel.map_or(true, |s| s.episodes.contains(*id))
            })
            .map(|(id, e)| {
                (
                    id.clone(),
                    Value::obj(vec![
                        ("body", e.body.clone()),
                        ("writer", Value::str(e.writer.clone())),
                        ("record_time", Value::str(e.record_time.clone())),
                        ("hidden_chars", Value::Bool(e.hidden_chars)),
                        ("order", Value::Int(order[&e.seq])),
                    ]),
                )
            })
            .collect();

        let supersessions: Vec<(String, Value)> = self
            .supersessions
            .iter()
            .filter(|(_, sp)| visible(&sp.compartment) && sel.is_none())
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

        // Organisations: whole (not part of a selection), as far as seen.
        let positions: Vec<(String, Value)> = self
            .positions
            .iter()
            .filter(|(_, p)| visible(&p.compartment) && sel.is_none())
            .map(|(k, p)| {
                (
                    k.clone(),
                    Value::obj(vec![
                        ("compartment", Value::str(p.compartment.clone())),
                        ("org", Value::str(p.org.clone())),
                        ("title", Value::str(p.title.clone())),
                        (
                            "reports_to",
                            p.reports_to.clone().map(Value::str).unwrap_or(Value::Null),
                        ),
                        ("duties", Value::str(p.duties.clone())),
                        ("by", Value::str(p.by.clone())),
                        ("event", Value::str(p.event.clone())),
                    ]),
                )
            })
            .collect();
        let appointments: Vec<(String, Value)> = self
            .appointments
            .iter()
            .filter(|(_, a)| visible(&a.compartment) && sel.is_none())
            .map(|(id, a)| {
                let ended = a.ended.as_ref().map(|(e, by, reason, t)| {
                    Value::obj(vec![
                        ("event", Value::str(e.clone())),
                        ("by", Value::str(by.clone())),
                        ("reason", Value::str(reason.clone())),
                        ("record_time", Value::str(t.clone())),
                    ])
                });
                (
                    id.clone(),
                    Value::obj(vec![
                        ("org", Value::str(a.org.clone())),
                        ("title", Value::str(a.title.clone())),
                        ("holder", Value::str(a.holder.clone())),
                        ("by", Value::str(a.by.clone())),
                        ("record_time", Value::str(a.record_time.clone())),
                        ("ended", ended.unwrap_or(Value::Null)),
                    ]),
                )
            })
            .collect();
        let handoffs: Vec<(String, Value)> = self
            .handoffs
            .iter()
            .filter(|(id, c)| {
                visible(&c.compartment) && sel.map_or(true, |s| s.handoffs.contains(*id))
            })
            .map(|(id, c)| {
                (
                    id.clone(),
                    Value::obj(vec![
                        ("body", c.body.clone()),
                        ("writer", Value::str(c.writer.clone())),
                        ("record_time", Value::str(c.record_time.clone())),
                        ("order", Value::Int(order[&c.seq])),
                    ]),
                )
            })
            .collect();

        let appeals: Vec<(String, Value)> = self
            .appeals
            .iter()
            .filter(|(_, a)| visible(&a.compartment) && sel.is_none())
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
                    ("group", Value::str(self.group(id))),
                ];
                for (k, v) in [
                    ("name", &w.name),
                    ("model", &w.model),
                    ("operator", &w.operator),
                    ("surface", &w.surface),
                    ("session", &w.session),
                    ("instance_of", &w.instance_of),
                ] {
                    if let Some(v) = v {
                        fields.push((k, Value::str(v.clone())));
                    }
                }
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
            ("handoffs", Value::Object(handoffs)),
            ("positions", Value::Object(positions)),
            ("appointments", Value::Object(appointments)),
        ])
    }

    /// Is the claim in an open contest or appeal?
    pub fn disputed(&self, id: &str) -> bool {
        is_disputed(self, id)
    }

    /// The open contests and appeals that make `id` disputed (event ids).
    pub fn open_disputes(&self, id: &str) -> Vec<String> {
        let mut out: Vec<String> = involving(self, id)
            .filter(|(_, sp)| sp.is_open_dispute())
            .map(|(k, _)| k.clone())
            .collect();
        out.extend(
            appeals_of(self, id)
                .filter(|(_, a)| a.is_open())
                .map(|(k, _)| k.clone()),
        );
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
