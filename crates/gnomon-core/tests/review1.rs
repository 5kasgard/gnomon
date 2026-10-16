//! Independent review round 1: every confirmed finding, reproduced as a test
//! that must now fail to exploit. Numbers match the review report.

mod common;
use common::*;

use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use gnomon_core::json::{self, canonical, Value};
use gnomon_core::ledger::{envelope_hash, Ledger};
use gnomon_core::schema;
use gnomon_core::store::{self, StoreError};

fn verdict(claim: &str, result: &str) -> Value {
    Value::obj(vec![
        ("claim", Value::str(claim)),
        ("result", Value::str(result)),
        ("method", Value::str("cross_model_review")),
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
        ("reason", Value::str("neutral decision")),
    ])
}

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

/// Adds `bridge` (party: bridge-co) with access to both lab and private.
fn lab_with_bridge() -> Ledger {
    let mut l = lab();
    l.append(draft(
        "john",
        5,
        "register",
        register_body("bridge", "bridge-co", "model", &["write", "verify"], &["lab", "private"]),
    ))
    .unwrap();
    l
}

/// 1. Corroborate-then-verify was self-certification.
#[test]
fn f1_corroborator_cannot_verify() {
    let mut l = lab();
    let body = claim_body("B", "asserted", "true");
    let id = add_claim(&mut l, "claude-c1", 10, body.clone());
    l.append(draft("gpt-g1", 11, "claim", body)).unwrap(); // corroborates
    assert_eq!(code(l.append(draft("gpt-g1", 12, "verdict", verdict(&id, "supports")))), "E_SELF_VERIFY");
}

/// 1b. The reverse order: a verifier's party can't then co-assert the claim.
#[test]
fn f1b_verifier_cannot_then_corroborate() {
    let mut l = lab();
    let body = claim_body("B", "asserted", "true");
    let id = add_claim(&mut l, "claude-c1", 10, body.clone());
    l.append(draft("gpt-g1", 11, "verdict", verdict(&id, "supports"))).unwrap();
    assert_eq!(code(l.append(draft("gpt-g1", 12, "claim", body))), "E_ROLE_CONFLICT");
}

/// 1c. Two identities of one party cannot both give verdicts on a claim.
#[test]
fn f1c_one_verdict_per_party() {
    let mut l = lab();
    let id = add_claim(&mut l, "gpt-g1", 10, claim_body("B", "asserted", "true"));
    l.append(draft("claude-c1", 11, "verdict", verdict(&id, "supports"))).unwrap();
    assert_eq!(code(l.append(draft("claude-c2", 12, "verdict", verdict(&id, "supports")))), "E_DUPLICATE");
}

/// 2. Corroborating a side made a party look neutral to resolve.
#[test]
fn f2_corroborator_cannot_resolve() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b_body = claim_body("B", "asserted", "true");
    let b = add_claim(&mut l, "gpt-g1", 11, b_body.clone());
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    // Joining a side mid-dispute is refused outright (round 3), so nobody can
    // turn neutral resolvers into parties and jam the dispute.
    assert_eq!(code(l.append(draft("kimi-k1", 13, "claim", b_body))), "E_OPEN_DISPUTE");
    assert_eq!(code(l.append(draft("kimi-k1", 14, "resolve", resolve(&eid(&s), "new")))), "OK");
}

/// 3. A party settled its own dispute by stacking a counter-supersession.
#[test]
fn f3_open_dispute_blocks_further_supersession() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    let v = l.append(draft("kimi-k1", 13, "verdict", verdict(&b, "refutes"))).unwrap();
    let v = eid(&v);
    assert_eq!(
        code(l.append(draft("claude-c1", 14, "supersede", sup(&b, &a, "refuted", vec![&v])))),
        "E_OPEN_DISPUTE"
    );
    assert_eq!(l.state.truth(&b).unwrap(), "true");
    assert!(l.state.disputed(&b));
    // Once a neutral party resolves it, evidence-backed refutation is open to
    // anyone, provided the refuting verdict comes from outside the dispute
    // (kimi's verdict here).
    l.append(draft("john", 15, "resolve", resolve(&eid(&s), "both"))).unwrap();
    assert_eq!(code(l.append(draft("claude-c1", 16, "supersede", sup(&b, &a, "refuted", vec![&v])))), "OK");
    assert_eq!(l.state.truth(&b).unwrap(), "false");
}

/// 4. Cross-compartment supersession let a lab-only writer decide a private claim.
#[test]
fn f4_supersession_stays_in_one_compartment() {
    let mut l = lab_with_bridge();
    let a = add_claim(&mut l, "bridge", 10, claim_body("A", "asserted", "true"));
    let p = add_claim(
        &mut l,
        "bridge",
        11,
        claim_body("P", "asserted", "true").with("compartment", Value::str("private")),
    );
    assert_eq!(code(l.append(draft("bridge", 12, "supersede", sup(&a, &p, "contested", vec![])))), "E_COMPARTMENT");
}

/// 5. Filtered reads leaked other compartments, including their names.
#[test]
fn f5_filtered_read_has_no_trace_of_other_compartments() {
    let mut l = lab_with_bridge();
    add_claim(&mut l, "claude-c1", 10, claim_body("lab claim", "asserted", "true"));
    let p_body = claim_body("very private", "asserted", "true").with("compartment", Value::str("private"));
    let p = add_claim(&mut l, "bridge", 11, p_body);
    let p2 = add_claim(
        &mut l,
        "bridge",
        12,
        claim_body("also private", "asserted", "true").with("compartment", Value::str("private")),
    );
    l.append(draft("bridge", 13, "supersede", sup(&p, &p2, "generalised", vec![&p]))).unwrap();
    let view = canonical(&l.state.to_value(Some(&["lab".to_string()])));
    for leak in ["very private", "also private", "\"private\"", &p[2..], &p2[2..], "bridge/"] {
        // "bridge/" would only appear via a hidden event id.
        assert!(!view.contains(leak), "filtered view leaks {leak}");
    }
    assert!(view.contains("lab claim"));
}

/// 6. Evidence could cite another compartment (and probe for existence).
#[test]
fn f6_evidence_cannot_cross_compartments() {
    let mut l = lab_with_bridge();
    let ep = l
        .append(draft(
            "bridge",
            10,
            "episode",
            Value::obj(vec![
                ("compartment", Value::str("private")),
                ("content", Value::str("secret transcript")),
                ("medium", Value::str("chat")),
                ("participants", Value::Array(vec![])),
            ]),
        ))
        .unwrap();
    let a = add_claim(&mut l, "claude-c1", 11, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 12, claim_body("B", "asserted", "true"));
    // A lab-only writer gets the same answer for a hidden id as for a missing one.
    let hidden = code(l.append(draft("gpt-g1", 13, "supersede", sup(&a, &b, "generalised", vec![&eid(&ep)]))));
    let missing = code(l.append(draft("gpt-g1", 13, "supersede", sup(&a, &b, "generalised", vec!["nobody/1"]))));
    assert_eq!(hidden, "E_REF");
    assert_eq!(missing, "E_REF");
    // A writer who can see both still cannot mix compartments.
    let mixed = code(l.append(draft("bridge", 14, "supersede", sup(&a, &b, "generalised", vec![&eid(&ep)]))));
    assert_eq!(mixed, "E_COMPARTMENT");
}

/// 7. Error codes confirmed the content of private claims by hash.
#[test]
fn f7_no_existence_oracle_through_error_codes() {
    let mut l = lab_with_bridge();
    let private_body = claim_body("salary is X", "asserted", "true").with("compartment", Value::str("private"));
    let p = add_claim(&mut l, "bridge", 10, private_body);
    let guessed_right = code(l.append(draft("gpt-g1", 11, "verdict", verdict(&p, "supports"))));
    let guessed_wrong = code(l.append(draft("gpt-g1", 11, "verdict", verdict("c:0000", "supports"))));
    assert_eq!(guessed_right, guessed_wrong);
    let src_hidden = claim_body("x", "asserted", "true").with("source_episode", Value::str("bridge/1"));
    let src_missing = claim_body("x", "asserted", "true").with("source_episode", Value::str("nobody/1"));
    assert_eq!(
        code(l.append(draft("gpt-g1", 11, "claim", src_hidden))),
        code(l.append(draft("gpt-g1", 11, "claim", src_missing)))
    );
}

/// 8. The parser panicked on a multi-byte character inside \u escapes; a
/// tampered log line could crash a reader.
#[test]
fn f8_log_with_bad_escape_is_malformed_not_a_crash() {
    let path = tmp_path("f8");
    fs::write(&path, "\"\\u123\u{e9}\"\n").unwrap();
    assert!(matches!(store::open(&path, None), Err(StoreError::Malformed { .. })));
    assert!(json::parse("\"\\u123\u{e9}\"").is_err());
}

/// 9. Concurrent appends corrupted the log permanently.
#[test]
fn f9_concurrent_appends_serialise() {
    let path = Arc::new(tmp_path("f9"));
    for env in lab().events {
        let mut text = canonical(&env);
        text.push('\n');
        let mut existing = fs::read_to_string(&*path).unwrap_or_default();
        existing.push_str(&text);
        fs::write(&*path, existing).unwrap();
    }
    let writers = ["claude-c1", "gpt-g1", "kimi-k1", "claude-c2"];
    let handles: Vec<_> = writers
        .iter()
        .map(|w| {
            let path = Arc::clone(&path);
            let w = w.to_string();
            std::thread::spawn(move || {
                for i in 0..25 {
                    store::append(
                        &path,
                        draft(&w, 59, "claim", claim_body(&format!("{w} {i}"), "asserted", "true")),
                    )
                    .expect("append under contention");
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let l = store::open(&path, None).expect("log still verifies");
    assert_eq!(l.state.claims.len(), 100);
}

/// 10. Duplicate keys built in code slipped past the gate and bricked the log.
#[test]
fn f10_duplicate_keys_from_code_are_refused() {
    let mut l = lab();
    let mut members = claim_body("A", "asserted", "true").as_object().unwrap().to_vec();
    members.push(("statement".to_string(), Value::str("x\u{202e}y")));
    let r = l.append(draft("claude-c1", 10, "claim", Value::Object(members)));
    assert_eq!(code(r), "E_ENVELOPE");

    let path = tmp_path("f10");
    store::append(&path, draft("john", 0, "register", register_body("john", "john", "human", &["owner"], &["lab"])))
        .unwrap();
    let mut members = claim_body("A", "asserted", "true").as_object().unwrap().to_vec();
    members.push(("statement".to_string(), Value::str("dup")));
    assert!(store::append(&path, draft("john", 1, "claim", Value::Object(members))).is_err());
    assert!(store::open(&path, None).is_ok(), "a refused append must not brick the log");
}

/// 11. The spec says owner implies every capability, including govern.
#[test]
fn f11_owner_can_change_schema() {
    let mut l = lab();
    let r = l.append(draft(
        "john",
        10,
        "schema_change",
        Value::obj(vec![("version", Value::Int(3)), ("description", Value::str("v3"))]),
    ));
    assert_eq!(code(r), "OK");
    assert_eq!(l.state.schema_version, 3);
}

/// 12. Several invisible or reordering characters were accepted.
#[test]
fn f12_wider_hidden_character_set() {
    let mut l = lab();
    for c in ['\u{61c}', '\u{34f}', '\u{fe0f}', '\u{e0100}', '\u{3164}', '\u{fffa}', '\u{1d175}', '\u{2028}', '\u{0}', '\u{1b}', '\u{85}'] {
        let body = claim_body(&format!("a{c}b"), "asserted", "true");
        assert_eq!(code(l.append(draft("claude-c1", 10, "claim", body))), "E_HIDDEN_CHARS", "{:x}", c as u32);
    }
    // Ordinary multi-line text is still fine.
    let body = claim_body("line one\nline two\twith a tab", "asserted", "true");
    assert_eq!(code(l.append(draft("claude-c1", 10, "claim", body))), "OK");
}

/// 13. Impossible dates were accepted.
#[test]
fn f13_impossible_dates_refused() {
    for bad in ["2026-02-31T00:00:00Z", "2025-02-29T00:00:00Z", "0000-04-30T00:00:00Z", "2026-11-31T00:00:00Z"] {
        assert!(!schema::is_timestamp(bad), "{bad}");
    }
    let mut l = lab();
    let mut env = l.events.last().unwrap().clone();
    env = env
        .with("seq", Value::Int(l.events.len() as i64))
        .with("prev", Value::str(l.head()))
        .with("writer_seq", Value::Int(5))
        .with("event_id", Value::str("john/5"))
        .with("record_time", Value::str("2026-02-31T00:00:00Z"));
    let env = env.with("hash", Value::str(envelope_hash(&env)));
    assert_eq!(l.apply(env).unwrap_err().code, "E_TIME_FORMAT");
}

/// 14. A missing log verified as an empty, valid one.
#[test]
fn f14_missing_log_is_not_found() {
    let path = tmp_path("f14");
    assert!(matches!(store::open(&path, None), Err(StoreError::NotFound)));
}

/// Contradictory outcomes: resolutions apply in log order, so mutual
/// disputes end in a single, consistent answer.
#[test]
fn truth_follows_log_order_of_resolutions() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s1 = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    l.append(draft("kimi-k1", 13, "resolve", resolve(&eid(&s1), "new"))).unwrap();
    assert_eq!((l.state.truth(&a).unwrap(), l.state.truth(&b).unwrap()), ("false".into(), "true".into()));
    // The loser cannot be revived by another contest, by anyone.
    assert_eq!(code(l.append(draft("claude-c1", 14, "supersede", sup(&b, &a, "contested", vec![])))), "E_FINAL");
    // An outsider can't stand in for the loser (consent), and couldn't revive it anyway.
    assert_eq!(code(l.append(draft("john", 15, "supersede", sup(&b, &a, "contested", vec![])))), "E_CONSENT");
    // But the winner can still be refuted with outside evidence; later wins.
    let v = l
        .append(draft("john", 16, "verdict", Value::obj(vec![
            ("claim", Value::str(b.clone())),
            ("result", Value::str("refutes")),
            ("method", Value::str("test")),
        ])))
        .unwrap();
    let c = add_claim(&mut l, "john", 17, claim_body("C", "asserted", "true"));
    l.append(draft("john", 18, "supersede", sup(&b, &c, "refuted", vec![&eid(&v)]))).unwrap();
    assert_eq!((l.state.truth(&a).unwrap(), l.state.truth(&b).unwrap()), ("false".into(), "false".into()));
}

fn tmp_path(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("gnomon-review1-{}-{}", std::process::id(), name));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir.join("log.jsonl")
}
