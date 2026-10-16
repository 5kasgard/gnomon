//! Independent review round 4: bugs and the clear-cut design gaps.
//! Open design questions (appeals, relitigation through reworded claims,
//! mass contests, owner-minted neutrality) are listed in the spec, not here.

mod common;
use common::*;

use gnomon_core::json::Value;

fn verdict(claim: &str, result: &str) -> Value {
    Value::obj(vec![
        ("claim", Value::str(claim)),
        ("result", Value::str(result)),
        ("method", Value::str("test")),
    ])
}

fn sup(old: &str, new: &str, kind: &str, evidence: Vec<&str>) -> Value {
    Value::obj(vec![
        ("old", Value::str(old)),
        ("new", Value::str(new)),
        ("kind", Value::str(kind)),
        ("evidence", Value::Array(evidence.into_iter().map(Value::str).collect())),
        ("reason", Value::str("because")),
    ])
}

fn resolve(sup_id: &str, outcome: &str) -> Value {
    Value::obj(vec![
        ("dispute", Value::str(sup_id)),
        ("outcome", Value::str(outcome)),
        ("reason", Value::str("ruling")),
    ])
}

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

/// B1. Entering a dispute shielded a wrong claim from refutation.
#[test]
fn b1_refutation_reaches_a_disputed_claim_and_ends_the_dispute() {
    let mut l = lab();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let m = add_claim(&mut l, "gpt-g1", 11, claim_body("M", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 12, "verdict", verdict(&m, "refutes"))).unwrap();
    let s = l.append(draft("gpt-g1", 13, "supersede", sup(&t, &m, "contested", vec![]))).unwrap();
    let k = add_claim(&mut l, "kimi-k1", 14, claim_body("K", "asserted", "true"));
    assert_eq!(code(l.append(draft("kimi-k1", 15, "supersede", sup(&m, &k, "refuted", vec![&eid(&v)])))), "OK");
    assert_eq!(l.state.truth(&m).unwrap(), "false");
    assert_eq!(l.state.truth(&t).unwrap(), "true", "the dispute ends: M's side lost");
    let dispute = &l.state.supersessions[&eid(&s)];
    assert!(dispute.resolution.is_some());
    // A later 'resolution' of that dispute is no longer possible.
    assert_eq!(code(l.append(draft("john", 16, "resolve", resolve(&eid(&s), "new")))), "E_RESOLVE");
}

/// B2. A challenger with only `write` could not withdraw.
#[test]
fn b2_write_only_challenger_can_withdraw() {
    let mut l = lab();
    l.append(draft("john", 6, "register", register_body("scribe", "scribe-co", "model", &["write"], &["lab"])))
        .unwrap();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "scribe", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("scribe", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    assert_eq!(code(l.append(draft("scribe", 13, "resolve", resolve(&eid(&s), "withdrawn")))), "OK");
    // But ruling still needs `verify`.
    let c = add_claim(&mut l, "gpt-g1", 14, claim_body("C", "asserted", "true"));
    let s2 = l.append(draft("gpt-g1", 15, "supersede", sup(&a, &c, "contested", vec![]))).unwrap();
    assert_eq!(code(l.append(draft("scribe", 16, "resolve", resolve(&eid(&s2), "old")))), "E_CAPABILITY");
}

/// B5. Withdrawing counted as resolving, blocking the challenger later.
#[test]
fn b5_withdrawal_is_not_a_resolution() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    l.append(draft("gpt-g1", 13, "resolve", resolve(&eid(&s), "withdrawn"))).unwrap();
    // The other gpt identity may still corroborate B after the withdrawal.
    l.append(draft("john", 14, "register", register_body("gpt-g2", "openai", "model", &["write"], &["lab"])))
        .unwrap();
    assert_eq!(code(l.append(draft("gpt-g2", 15, "claim", claim_body("B", "asserted", "true")))), "OK");
}

/// D2. Withdraw-and-reraise kept a claim contested forever.
#[test]
fn d2_no_withdraw_loops() {
    let mut l = lab();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&t, &b, "contested", vec![]))).unwrap();
    l.append(draft("gpt-g1", 13, "resolve", resolve(&eid(&s), "withdrawn"))).unwrap();
    assert_eq!(code(l.append(draft("gpt-g1", 14, "supersede", sup(&t, &b, "contested", vec![])))), "E_FINAL");
    assert_eq!(l.state.truth(&t).unwrap(), "true");
}

/// D3. A third party dragged two others into a dispute.
#[test]
fn d3_consent_of_the_new_side() {
    let mut l = lab();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    assert_eq!(code(l.append(draft("kimi-k1", 12, "supersede", sup(&t, &b, "contested", vec![])))), "E_CONSENT");
    assert_eq!(l.state.truth(&b).unwrap(), "true");
}

/// D6. A resolver overturned its own ruling with its own verdict.
#[test]
fn d6_resolver_cannot_overturn_its_own_ruling() {
    let mut l = lab();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&t, &b, "contested", vec![]))).unwrap();
    l.append(draft("kimi-k1", 13, "resolve", resolve(&eid(&s), "old"))).unwrap();
    let v = l.append(draft("kimi-k1", 14, "verdict", verdict(&t, "refutes"))).unwrap();
    let k = add_claim(&mut l, "kimi-k1", 15, claim_body("K", "asserted", "true"));
    assert_eq!(code(l.append(draft("kimi-k1", 16, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)])))), "E_EVIDENCE");
    assert_eq!(l.state.truth(&t).unwrap(), "true");
}
