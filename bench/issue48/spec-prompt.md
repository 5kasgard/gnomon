# Independent spec for Gnomon issue #48 (same prompt for every spec author)

You are writing a specification, not code. Work only from this file: do not look at any other
branch, file or prior fix. Be critical and concrete.

## The system

Gnomon is a hash-chained, append-only memory log with a Rust core (the "gate": every write is
checked by `check()` and then committed) and thin Python bindings. Writers are registered with
`kind`, `party`, optional `operator` (the human a model acts for), `session`, `surface`, `name`,
`capabilities` (owner, write, verify, spawn, schedule, govern) and `compartments`. A reader sees
only what is in compartments it holds. A writer with `spawn` may register instances of itself
(id `<parent>-...`, same identity, a subset of its compartments and capabilities). Owners can
do anything and are trusted; the threat is non-owner writers.

Event ids are `<writer>/<writer_seq>`; every event also has a global position `seq`. A query
or view returns `through` (the last event it reflects: id, kind, time, hash). `as_of(t)` views
the log as of a time; it relies on record times never going backwards.

## The problem

Information crosses between writers who share no compartment. Confirmed so far (each by a
two-world test: the same script with and without a hidden action in compartment A; any
difference in what a B-only party gets back = crosses):

1. **Operator:** a B reader sees a B writer's operator who exists only in A (views and Datalog
   `identity` facts).
2. **Session ids:** spawners of one party in A and B signal bits by choosing session ids; the
   duplicate-session refusal (`E_IDENTITY`) is checked across compartments.
3. **Writer ids:** parent `c` (A) registers `c-x-<i>`; parent `c-x` (B) decodes from
   `E_DUPLICATE`; also the reverse order, and a parent asking for another parent's id.
4. **Counts:** event ids, write returns (`event_id`, `seq`) and views' `seq` reveal how many
   hidden events a writer or the log holds.
5. **Leases:** whether a lease to a hidden writer is accepted or refused (`E_UNKNOWN_WRITER`);
   `E_LEASED` naming a hidden lease's holder and expiry; a hidden live lease on a frame name
   blocking a B lease on that name.
6. **`through`:** lease, release and revoke events count for every reader.
7. **Clock:** a later-dated event in A makes a B write at an earlier time fail (`E_CLOCK`);
   leases advance it too. A known pitfall: per-compartment clocks break `as_of`'s prefix
   assumption (a later-dated A event can hide earlier-dated B events, for every reader).

Constraints: old logs must replay to the identical state (stricter rules are gated on a new
protocol version; check `st.schema_version`); the existing test suites (198 Rust, 285 Python)
should keep passing; additions should be small (the project is fighting complexity). Some
channels may be better left as a stated limit; say which and why.

## What to deliver (under 1,500 words plus probes)

1. **Rules:** for each channel, the behaviour you specify (what the gate refuses or a view
   shows, and from which protocol version), or "limit" with the reason. Precise enough that a
   builder who has never seen this discussion can implement it.
2. **Pitfalls:** what a builder is likely to get wrong (races, old logs, other paths).
3. **Probes:** two-world tests in Python against the API below, one function per probe,
   each building two fresh logs that differ only in the hidden action and returning what the
   B side gets back (catch `GateError` and return `e.code` and `str(e)`). Include probes for
   the side effects you fear, not only the channels. They will be run against every build,
   including builds of other specs.

## Python API (gnomon)

```python
from gnomon import GateError, Memory, claim
from gnomon.session import instance_for   # (memory, parent, session=None, surface=None, compartments=None) -> writer id
m = Memory(path)                          # a log file in a temp dir
m.register(by, writer, *, party, kind, capabilities, compartments, model=None, operator=None,
           name=None, surface=None, session=None, record_time=None)
m.remember(writer, claim(statement, speaker=..., frame=..., compartment=...), record_time) -> claim id
m.append(writer, kind, body, record_time) -> dict (event_id, seq, ...)
m.episode(writer, content, *, compartment, participants, bindings=None, record_time=None)
m.lease(by, target, frame, expires, reason=None, record_time=None) -> lease event id
m.release(by, lease, reason=None, record_time=None); m.revoke(by, target, compartments, reason, record_time=None)
m.state(as_of=None, compartments=None); m.writers(compartments=None)
m.query(reader, program, proofs=0)        # Datalog, e.g. '?- identity("bot", "operator", V).'
```
Times are `YYYY-MM-DDTHH:MM:SSZ`. The first registration must be an owner registering itself.

## Code on main (excerpts)

### ledger.rs: as_of
```rust
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
```
### ledger.rs: check(): clock and writer namespace
```rust
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
```
### ledger.rs: register
```rust
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

```
### ledger.rs: lease and release
```rust
        "lease" => {
            if st.schema_version < 6 {
                return Err(err("E_SCHEMA_VERSION", "lease needs protocol version 6"));
            }
            need(w, "schedule")?;
            let l = schema::lease(body)?;
            let target = st
                .writers
                .get(l.writer)
                .ok_or_else(|| err("E_UNKNOWN_WRITER", format!("writer '{}' is not registered", l.writer)))?;
            if acts_for(st, writer, l.writer) {
                return Err(err("E_LEASE", "a writer never leases to itself"));
            }
            if target.capabilities.iter().any(|c| c == "owner") {
                return Err(err("E_LEASE", "an owner needs no lease"));
            }
            if l.expires <= record_time {
                return Err(err("E_LEASE", "a lease must expire after it is granted"));
            }
            if let Some((id, held)) = live_lease(st, l.frame, record_time) {
                return Err(err(
                    "E_LEASED",
                    format!("frame '{}' is already leased ('{id}', to '{}' until {})", l.frame, held.writer, held.expires),
                ));
            }
            done(Change::Lease {
                event: event_id.to_string(),
                info: LeaseInfo {
                    writer: l.writer.to_string(),
                    frame: l.frame.to_string(),
                    expires: l.expires.to_string(),
                    by: writer.to_string(),
                    record_time: record_time.to_string(),
                    ended: None,
                },
            })
        }

        "release" => {
            if st.schema_version < 6 {
                return Err(err("E_SCHEMA_VERSION", "release needs protocol version 6"));
            }
            let id = schema::release(body)?;
            let l = st.leases.get(id).ok_or_else(|| not_found("lease", id))?;
            let owner = w.capabilities.iter().any(|c| c == "owner");
            if !(owner || l.by == writer || acts_for(st, writer, &l.writer)) {
                return Err(err("E_CAPABILITY", "a lease is released by its holder, whoever granted it, or the owner"));
            }
            if l.ended.is_some() {
                return Err(err("E_LEASE", format!("lease '{id}' was already released")));
            }
            done(Change::Release { lease: id.to_string(), event: event_id.to_string() })
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
```
### ledger.rs: restricted writer view
```rust
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
```
### query.rs: identity facts
```rust
            .collect();
        for (wid, w) in &st.writers {
            let shared: Vec<&String> = w.compartments.iter().filter(|c| self.sees(c)).collect();
            if shared.is_empty() {
                continue;
            }
            let src = self.src_seq(registered[wid.as_str()]);
            self.add(eng, "writer", vec![s(wid), s(&w.party), s(&w.kind)], src)?;
            for (k, v) in [
                ("name", &w.name),
                ("model", &w.model),
                ("operator", &w.operator),
                ("surface", &w.surface),
                ("session", &w.session),
            ] {
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
```
### query.rs: event_visible
```rust
pub fn event_visible(ledger: &Ledger, visible: &[String], e: &Value) -> bool {
    let st = &ledger.state;
    let sees = |c: &str| visible.iter().any(|x| x == c);
    let id = e.get("event_id").and_then(|v| v.as_str()).unwrap_or("");
    let body = e.get("body");
    let field = |k: &str| {
        body.and_then(|b| b.get(k))
            .and_then(|v| v.as_str())
            .unwrap_or("")
    };
    let claim_seen = |c: &str| {
        st.claims
            .get(c)
            .map(|c| sees(&c.compartment))
            .unwrap_or(false)
    };
    match e.get("kind").and_then(|v| v.as_str()).unwrap_or("") {
        "claim" => body.map(|b| claim_seen(&claim_id(b))).unwrap_or(false),
        "episode" => st
            .episodes
            .get(id)
            .map(|x| sees(&x.compartment))
            .unwrap_or(false),
        "verdict" => st
            .verdicts
            .get(id)
            .map(|v| claim_seen(&v.claim))
            .unwrap_or(false),
        "supersede" => st
            .supersessions
            .get(id)
            .map(|x| sees(&x.compartment))
            .unwrap_or(false),
        "appeal" => st
            .appeals
            .get(id)
            .map(|x| sees(&x.compartment))
            .unwrap_or(false),
        "handoff" => st
            .handoffs
            .get(id)
            .map(|x| sees(&x.compartment))
            .unwrap_or(false),
        "position" => sees(field("compartment")),
        "appoint" => st
            .appointments
            .get(id)
            .map(|x| sees(&x.compartment))
            .unwrap_or(false),
        "dismiss" => st
            .appointments
            .get(field("appointment"))
            .map(|x| sees(&x.compartment))
            .unwrap_or(false),
        "resolve" => {
            let d = field("dispute");
            st.supersessions
                .get(d)
                .map(|x| sees(&x.compartment))
                .unwrap_or(false)
                || st
                    .appeals
                    .get(d)
                    .map(|x| sees(&x.compartment))
                    .unwrap_or(false)
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
    (0..ledger.events.len())
        .rev()
        .find(|&i| event_visible(ledger, visible, &ledger.events[i]))
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
```
