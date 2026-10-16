//! Independent review round 6: over-correction fixes. Consent for
//! refutations, the one-argument rule aimed at challengers and judges only,
//! and a last-resort judge so that nothing can be made unjudgeable.

mod common;
use common::*;

use gnomon_core::json::Value;
use gnomon_core::ledger::Ledger;

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

fn decide(dispute: &str, outcome: &str) -> Value {
    Value::obj(vec![
        ("dispute", Value::str(dispute)),
        ("outcome", Value::str(outcome)),
        ("reason", Value::str("decision")),
    ])
}

fn appeal(claim: &str, evidence: Vec<&str>) -> Value {
    Value::obj(vec![
        ("claim", Value::str(claim)),
        ("evidence", Value::Array(evidence.into_iter().map(Value::str).collect())),
        ("reason", Value::str("appeal")),
    ])
}

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

const MODELS: &[(&str, &str)] = &[
    ("mistral-m1", "mistral"),
    ("qwen-q1", "qwen"),
    ("deepseek-d1", "deepseek"),
    ("meta-l1", "meta"),
    ("grok-x1", "xai"),
];

fn world() -> Ledger {
    let mut l = lab();
    for (i, (w, p)) in MODELS.iter().enumerate() {
        l.append(draft("john", 6 + i as u32, "register", register_body(w, p, "model", &["write", "verify"], &["lab"])))
            .unwrap();
    }
    l
}

/// G1. A well-reviewed refuted claim became impossible to appeal, because
/// every supporter was disqualified from judging. The owner, outside the
/// matter, now decides as a last resort.
#[test]
fn g1_well_supported_claim_can_still_be_appealed() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 12, "verdict", verdict(&a, "refutes"))).unwrap();
    l.append(draft("gpt-g1", 13, "supersede", sup(&a, &k, "refuted", vec![&eid(&v)]))).unwrap();
    let mut first = String::new();
    for (i, (w, _)) in MODELS.iter().enumerate() {
        let s = l.append(draft(w, 14 + i as u32, "verdict", verdict(&a, "supports"))).unwrap();
        if i == 0 {
            first = eid(&s);
        }
    }
    let ap = l.append(draft("claude-c1", 30, "appeal", appeal(&a, vec![&first]))).unwrap();
    // No ordinary neutral remains, so the owner (no stake) decides.
    assert_eq!(code(l.append(draft("john", 31, "resolve", decide(&eid(&ap), "reinstated")))), "OK");
    assert_eq!(l.state.truth(&a).unwrap(), "true");
}

/// The last-resort judge is used only when no ordinary neutral remains.
#[test]
fn backstop_only_when_needed() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    l.append(draft("john", 12, "verdict", verdict(&a, "supports"))).unwrap();
    let s = l.append(draft("gpt-g1", 13, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    // john gave a verdict on A, and neutral parties exist: john cannot rule.
    assert_eq!(code(l.append(draft("john", 14, "resolve", decide(&eid(&s), "new")))), "E_RESOLVE_PARTY");
    assert_eq!(code(l.append(draft("qwen-q1", 15, "resolve", decide(&eid(&s), "old")))), "OK");
}

/// G2. A refuter conscripted the claim's own party (or a chosen supporter)
/// as the "opposing side" to block their appeals.
#[test]
fn g2_refuter_must_use_its_own_claim() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let other_anthropic = add_claim(&mut l, "claude-c2", 11, claim_body("A2", "asserted", "true"));
    let mistral_claim = add_claim(&mut l, "mistral-m1", 12, claim_body("M", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 13, "verdict", verdict(&a, "refutes"))).unwrap();
    for new in [&other_anthropic, &mistral_claim] {
        assert_eq!(code(l.append(draft("gpt-g1", 14, "supersede", sup(&a, new, "refuted", vec![&eid(&v)])))), "E_CONSENT");
    }
}

/// G3. Verdicts given after a contest opened stranded it.
#[test]
fn g3_late_verdicts_cannot_strand_a_contest() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    l.append(draft("kimi-k1", 13, "verdict", verdict(&a, "supports"))).unwrap();
    for (i, (w, _)) in MODELS.iter().enumerate() {
        l.append(draft(w, 14 + i as u32, "verdict", verdict(&a, "supports"))).unwrap();
    }
    assert_eq!(code(l.append(draft("john", 30, "resolve", decide(&eid(&s), "old")))), "OK");
    assert!(!l.state.disputed(&a));
}

/// G5. A defender drawn into a contest can still challenge the other claim.
#[test]
fn g5_defenders_keep_their_argument() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    l.append(draft("kimi-k1", 13, "resolve", decide(&eid(&s), "both"))).unwrap();
    let a2 = add_claim(&mut l, "claude-c1", 14, claim_body("A2", "asserted", "true"));
    assert_eq!(code(l.append(draft("claude-c1", 15, "supersede", sup(&b, &a2, "contested", vec![])))), "OK");
    // The challenger and the judge still get no second argument against A.
    let b2 = add_claim(&mut l, "gpt-g1", 16, claim_body("B2", "asserted", "true"));
    assert_eq!(code(l.append(draft("gpt-g1", 17, "supersede", sup(&a, &b2, "contested", vec![])))), "E_FINAL");
    let k2 = add_claim(&mut l, "kimi-k1", 18, claim_body("K2", "asserted", "true"));
    assert_eq!(code(l.append(draft("kimi-k1", 19, "supersede", sup(&a, &k2, "contested", vec![])))), "E_FINAL");
}
