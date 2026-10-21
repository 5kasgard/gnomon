//! Helpers for integration tests. Integration tests can only reach the
//! public API, so a test can never supply wiring the code itself lacks
//! (the RabbitHole WAL-gate lesson).

#![allow(dead_code)]

use gnomon_core::json::{self, Value};
use gnomon_core::ledger::{Draft, Ledger};
use gnomon_core::schema::GateError;

pub fn j(s: &str) -> Value {
    json::parse(s).expect("test JSON must parse")
}

pub fn t(minute: u32) -> String {
    format!("2026-10-14T{:02}:{:02}:00Z", 10 + minute / 60, minute % 60)
}

pub fn draft(writer: &str, minute: u32, kind: &str, body: Value) -> Draft {
    Draft {
        writer: writer.to_string(),
        record_time: t(minute),
        kind: kind.to_string(),
        body,
    }
}

/// Models and tools act for john; each model instance runs its own model
/// (by its writer id) unless a test says otherwise.
pub fn register_body(
    writer: &str,
    party: &str,
    kind: &str,
    caps: &[&str],
    comps: &[&str],
) -> Value {
    let mut v = register_plain(writer, party, kind, caps, comps);
    if kind != "human" {
        if let Value::Object(o) = &mut v {
            o.push(("operator".to_string(), Value::str("john")));
            if kind == "model" {
                o.push(("model".to_string(), Value::str(writer)));
            }
        }
    }
    v
}

pub fn register_plain(
    writer: &str,
    party: &str,
    kind: &str,
    caps: &[&str],
    comps: &[&str],
) -> Value {
    Value::obj(vec![
        ("writer", Value::str(writer)),
        ("party", Value::str(party)),
        ("kind", Value::str(kind)),
        (
            "capabilities",
            Value::Array(caps.iter().map(|c| Value::str(*c)).collect()),
        ),
        (
            "compartments",
            Value::Array(comps.iter().map(|c| Value::str(*c)).collect()),
        ),
    ])
}

/// A ledger with: john (human owner), claude-c1 (party anthropic),
/// claude-c2 (second identity, party anthropic), gpt-g1 (party openai),
/// kimi-k1 (party moonshot). All write and verify in "lab".
pub fn lab() -> Ledger {
    let mut l = Ledger::new();
    l.append(draft(
        "john",
        0,
        "register",
        register_body("john", "john", "human", &["owner"], &["lab", "private"])
            .with("schema_version", Value::Int(2)),
    ))
    .unwrap();
    for (i, (w, p)) in [
        ("claude-c1", "anthropic"),
        ("claude-c2", "anthropic"),
        ("gpt-g1", "openai"),
        ("kimi-k1", "moonshot"),
    ]
    .iter()
    .enumerate()
    {
        l.append(draft(
            "john",
            1 + i as u32,
            "register",
            register_body(w, p, "model", &["write", "verify"], &["lab"]),
        ))
        .unwrap();
    }
    l
}

pub fn claim_body(statement: &str, stance: &str, truth: &str) -> Value {
    Value::obj(vec![
        ("statement", Value::str(statement)),
        ("statement_kind", Value::str("describe")),
        ("origin", Value::str("derived")),
        ("stance", Value::str(stance)),
        ("speaker", Value::str("claude-c1")),
        ("truth", Value::str(truth)),
        ("confidence", Value::Int(7000)),
        ("frame", Value::str("substrate design")),
        ("compartment", Value::str("lab")),
    ])
}

pub fn add_claim(l: &mut Ledger, writer: &str, minute: u32, body: Value) -> String {
    l.append(draft(writer, minute, "claim", body.clone()))
        .unwrap();
    gnomon_core::ledger::claim_id(&body)
}

pub fn code(r: Result<Value, GateError>) -> &'static str {
    match r {
        Ok(_) => "OK",
        Err(e) => e.code,
    }
}
