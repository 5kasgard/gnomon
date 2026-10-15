//! Disputes as a flag, and the three ways back for a defeated claim:
//! 1. appeal on new outside evidence, 2. appeal on the balance of verdicts,
//! 3. restating it as a new claim. Appeals are decided by a neutral party.

mod common;
use common::*;

use gnomon_core::json::{canonical, Value};
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
        ("reason", Value::str("new evidence")),
    ])
}

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

/// lab() plus mistral-m1 (party mistral) and judge (party judge-co, verify only).
fn world() -> Ledger {
    let mut l = lab();
    l.append(draft("alex", 6, "register", register_body("mistral-m1", "mistral", "model", &["write", "verify"], &["lab"])))
        .unwrap();
    l.append(draft("alex", 7, "register", register_body("judge", "judge-co", "model", &["verify"], &["lab"])))
        .unwrap();
    l
}

/// T (claude) refuted by kimi's verdict, superseded by gpt's K.
/// Returns (ledger, T, K, kimi's refuting verdict).
fn refuted_t(l: &mut Ledger) -> (String, String, String) {
    let t = add_claim(l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let k = add_claim(l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 12, "verdict", verdict(&t, "refutes"))).unwrap();
    l.append(draft("gpt-g1", 13, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)]))).unwrap();
    assert_eq!(l.state.truth(&t).unwrap(), "false");
    (t, k, eid(&v))
}

#[test]
fn contests_flag_but_do_not_change_truth() {
    let mut l = world();
    // One party contests many claims: none of their truths change.
    let mut ids = Vec::new();
    for i in 0..20 {
        ids.push(add_claim(&mut l, "claude-c1", 10, claim_body(&format!("fact {i}"), "asserted", "true")));
    }
    for (i, id) in ids.iter().enumerate() {
        let mine = add_claim(&mut l, "gpt-g1", 12, claim_body(&format!("rival {i}"), "asserted", "true"));
        l.append(draft("gpt-g1", 12, "supersede", sup(id, &mine, "contested", vec![]))).unwrap();
    }
    for id in &ids {
        assert_eq!(l.state.truth(id).unwrap(), "true");
        assert!(l.state.disputed(id));
    }
    let view = canonical(&l.state.to_value(None));
    assert!(view.contains("\"disputed\":true"));
}

#[test]
fn route1_appeal_on_new_outside_evidence() {
    let mut l = world();
    let (t, _k, _v) = refuted_t(&mut l);
    // Mistral checks T afterwards and supports it: new, outside evidence.
    let s = l.append(draft("mistral-m1", 20, "verdict", verdict(&t, "supports"))).unwrap();
    let a = l.append(draft("claude-c1", 21, "appeal", appeal(&t, vec![&eid(&s)]))).unwrap();
    assert!(l.state.disputed(&t));
    assert_eq!(l.state.truth(&t).unwrap(), "false", "an appeal alone changes nothing");
    // Anyone with a stake cannot decide: author, refuters, evidence giver.
    for w in ["claude-c2", "gpt-g1", "kimi-k1", "mistral-m1"] {
        assert_eq!(code(l.append(draft(w, 22, "resolve", decide(&eid(&a), "reinstated")))), "E_RESOLVE_PARTY", "{w}");
    }
    assert_eq!(code(l.append(draft("judge", 23, "resolve", decide(&eid(&a), "reinstated")))), "OK");
    assert_eq!(l.state.truth(&t).unwrap(), "true");
    assert!(!l.state.disputed(&t));
}

#[test]
fn route2_appeal_on_the_balance_of_verdicts() {
    let mut l = world();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let s1 = l.append(draft("judge", 11, "verdict", verdict(&t, "supports"))).unwrap();
    l.append(draft("mistral-m1", 12, "verdict", verdict(&t, "supports"))).unwrap();
    let v = l.append(draft("kimi-k1", 13, "verdict", verdict(&t, "refutes"))).unwrap();
    let k = add_claim(&mut l, "gpt-g1", 14, claim_body("K", "asserted", "true"));
    l.append(draft("gpt-g1", 15, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)]))).unwrap();
    assert_eq!(l.state.truth(&t).unwrap(), "false");
    // No new evidence, but two parties support and one refutes: a hearing is earned.
    let a = l.append(draft("claude-c1", 16, "appeal", appeal(&t, vec![&eid(&s1)]))).unwrap();
    // The decider must be neutral: alex (owner) has no stake.
    assert_eq!(code(l.append(draft("alex", 17, "resolve", decide(&eid(&a), "upheld")))), "OK");
    assert_eq!(l.state.truth(&t).unwrap(), "false", "upheld: stays refuted");
}

#[test]
fn appeal_needs_new_evidence_or_favourable_balance() {
    let mut l = world();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let s = l.append(draft("mistral-m1", 11, "verdict", verdict(&t, "supports"))).unwrap();
    let v = l.append(draft("kimi-k1", 12, "verdict", verdict(&t, "refutes"))).unwrap();
    let k = add_claim(&mut l, "gpt-g1", 13, claim_body("K", "asserted", "true"));
    l.append(draft("gpt-g1", 14, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)]))).unwrap();
    // One old support against one refute: not eligible.
    assert_eq!(code(l.append(draft("claude-c1", 15, "appeal", appeal(&t, vec![&eid(&s)])))), "E_EVIDENCE");
}

#[test]
fn appeal_evidence_must_be_outside_support() {
    let mut l = world();
    let (t, _k, v) = refuted_t(&mut l);
    // The refuting verdict isn't support; a refuter's party can't support it later.
    assert_eq!(code(l.append(draft("claude-c1", 20, "appeal", appeal(&t, vec![&v])))), "E_EVIDENCE");
    l.append(draft("alex", 21, "register", register_body("kimi-k2", "moonshot", "model", &["verify"], &["lab"])))
        .unwrap();
    let s = l.append(draft("kimi-k2", 22, "verdict", verdict(&t, "supports")));
    // One verdict per party: moonshot already refuted, so this is refused anyway.
    assert_eq!(code(s), "E_DUPLICATE");
    // Unknown or hidden evidence answers like missing.
    assert_eq!(code(l.append(draft("claude-c1", 23, "appeal", appeal(&t, vec!["nobody/1"])))), "E_REF");
}

#[test]
fn only_defeated_claims_are_appealed() {
    let mut l = world();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let s = l.append(draft("mistral-m1", 11, "verdict", verdict(&t, "supports"))).unwrap();
    assert_eq!(code(l.append(draft("claude-c1", 12, "appeal", appeal(&t, vec![&eid(&s)])))), "E_APPEAL");
}

#[test]
fn one_appeal_at_a_time_and_once_per_party() {
    let mut l = world();
    let (t, _k, _v) = refuted_t(&mut l);
    let s = l.append(draft("mistral-m1", 20, "verdict", verdict(&t, "supports"))).unwrap();
    let a = l.append(draft("claude-c1", 21, "appeal", appeal(&t, vec![&eid(&s)]))).unwrap();
    // While open: no second appeal, no corroboration, no supersession.
    assert_eq!(code(l.append(draft("alex", 22, "appeal", appeal(&t, vec![&eid(&s)])))), "E_OPEN_DISPUTE");
    assert_eq!(code(l.append(draft("alex", 22, "claim", claim_body("T", "asserted", "true")))), "E_OPEN_DISPUTE");
    // Withdrawal is the appellant's alone, needs only write, and uses up its appeal.
    assert_eq!(code(l.append(draft("judge", 23, "resolve", decide(&eid(&a), "withdrawn")))), "E_CAPABILITY");
    assert_eq!(code(l.append(draft("alex", 23, "resolve", decide(&eid(&a), "withdrawn")))), "E_RESOLVE_PARTY");
    assert_eq!(code(l.append(draft("claude-c2", 24, "resolve", decide(&eid(&a), "withdrawn")))), "OK");
    assert_eq!(code(l.append(draft("claude-c1", 25, "appeal", appeal(&t, vec![&eid(&s)])))), "E_FINAL");
    assert_eq!(l.state.truth(&t).unwrap(), "false");
    // Contest outcomes don't apply to appeals, and vice versa.
    let a2 = l.append(draft("alex", 26, "appeal", appeal(&t, vec![&eid(&s)]))).unwrap();
    assert_eq!(code(l.append(draft("judge", 27, "resolve", decide(&eid(&a2), "new")))), "E_RESOLVE");
}

#[test]
fn after_reinstatement_only_fresh_outside_evidence_refutes_again() {
    let mut l = world();
    let (t, k, old_v) = refuted_t(&mut l);
    let s = l.append(draft("mistral-m1", 20, "verdict", verdict(&t, "supports"))).unwrap();
    let a = l.append(draft("claude-c1", 21, "appeal", appeal(&t, vec![&eid(&s)]))).unwrap();
    l.append(draft("judge", 22, "resolve", decide(&eid(&a), "reinstated"))).unwrap();
    assert_eq!(l.state.truth(&t).unwrap(), "true");
    // The old refuting verdict cannot simply be replayed.
    let k2 = add_claim(&mut l, "gpt-g1", 23, claim_body("K2", "asserted", "true"));
    assert_eq!(code(l.append(draft("gpt-g1", 24, "supersede", sup(&t, &k2, "refuted", vec![&old_v])))), "E_EVIDENCE");
    // Nor can the appeal's decider or evidence givers produce the new refutation.
    assert_eq!(code(l.append(draft("judge", 25, "verdict", verdict(&t, "refutes")))), "OK");
    let jv = l.state.claims[&t].refutes.last().unwrap().clone();
    assert_eq!(code(l.append(draft("gpt-g1", 26, "supersede", sup(&t, &k2, "refuted", vec![&jv])))), "E_EVIDENCE");
    // A fresh verdict from a party with no stake can.
    l.append(draft("alex", 27, "register", register_body("auditor", "audit-co", "model", &["verify"], &["lab"])))
        .unwrap();
    let fresh = l.append(draft("auditor", 28, "verdict", verdict(&t, "refutes"))).unwrap();
    assert_eq!(code(l.append(draft("gpt-g1", 29, "supersede", sup(&t, &k2, "refuted", vec![&eid(&fresh)])))), "OK");
    assert_eq!(l.state.truth(&t).unwrap(), "false");
    let _ = k;
}

/// An uncited refuting verdict from a party with no stake, given before the
/// reinstatement, is stale: only evidence after the reinstatement counts.
#[test]
fn stale_uncited_refutation_cannot_undo_a_reinstatement() {
    let mut l = world();
    l.append(draft("alex", 8, "register", register_body("auditor", "audit-co", "model", &["verify"], &["lab"])))
        .unwrap();
    let t = add_claim(&mut l, "claude-c1", 10, claim_body("T", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    let stale = l.append(draft("auditor", 12, "verdict", verdict(&t, "refutes"))).unwrap();
    let v = l.append(draft("kimi-k1", 13, "verdict", verdict(&t, "refutes"))).unwrap();
    l.append(draft("gpt-g1", 14, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)]))).unwrap();
    let s = l.append(draft("mistral-m1", 15, "verdict", verdict(&t, "supports"))).unwrap();
    let a = l.append(draft("claude-c1", 16, "appeal", appeal(&t, vec![&eid(&s)]))).unwrap();
    l.append(draft("judge", 17, "resolve", decide(&eid(&a), "reinstated"))).unwrap();
    let k2 = add_claim(&mut l, "gpt-g1", 18, claim_body("K2", "asserted", "true"));
    assert_eq!(code(l.append(draft("gpt-g1", 19, "supersede", sup(&t, &k2, "refuted", vec![&eid(&stale)])))), "E_EVIDENCE");
    assert_eq!(l.state.truth(&t).unwrap(), "true");
}

#[test]
fn route3_restating_as_a_new_claim_is_always_open() {
    let mut l = world();
    let (t, _k, _v) = refuted_t(&mut l);
    let t2 = add_claim(&mut l, "claude-c1", 20, claim_body("T, with corrected scope", "asserted", "true"));
    assert_eq!(l.state.truth(&t2).unwrap(), "true");
    assert_ne!(t, t2);
}

#[test]
fn appeals_need_a_neutral_decider() {
    let mut l = lab();
    // Only alex is outside the stake here, and alex is the claim's author.
    let t = add_claim(&mut l, "alex", 10, claim_body("T", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 12, "verdict", verdict(&t, "refutes"))).unwrap();
    l.append(draft("gpt-g1", 13, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)]))).unwrap();
    let s = l.append(draft("claude-c1", 14, "verdict", verdict(&t, "supports"))).unwrap();
    assert_eq!(code(l.append(draft("alex", 15, "appeal", appeal(&t, vec![&eid(&s)])))), "E_NO_RESOLVER");
}

#[test]
fn filtered_view_hides_other_compartments_appeals() {
    let mut l = world();
    l.append(draft("alex", 8, "register", register_body("bridge", "bridge-co", "model", &["write", "verify"], &["private"])))
        .unwrap();
    l.append(draft("alex", 9, "register", register_body("p-judge", "pj", "model", &["verify"], &["private"])))
        .unwrap();
    l.append(draft("alex", 9, "register", register_body("p-ref", "pr", "model", &["write", "verify"], &["private"])))
        .unwrap();
    let priv_ = |s: &str| claim_body(s, "asserted", "true").with("compartment", Value::str("private"));
    let t = add_claim(&mut l, "bridge", 10, priv_("secret T"));
    let k = add_claim(&mut l, "p-ref", 11, priv_("secret K"));
    let v = l.append(draft("alex", 12, "verdict", verdict(&t, "refutes"))).unwrap();
    l.append(draft("p-ref", 13, "supersede", sup(&t, &k, "refuted", vec![&eid(&v)]))).unwrap();
    let s = l.append(draft("p-judge", 14, "verdict", verdict(&t, "supports"))).unwrap();
    l.append(draft("bridge", 15, "appeal", appeal(&t, vec![&eid(&s)]))).unwrap_err();
    let view = canonical(&l.state.to_value(Some(&["lab".to_string()])));
    assert!(!view.contains("secret"));
    assert!(view.contains("\"appeals\":{}"));
}
