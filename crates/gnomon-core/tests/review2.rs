//! Independent review round 2: every confirmed finding, reproduced as a test
//! that must now fail to exploit. Numbers match the review report.

mod common;
use common::*;

use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use gnomon_core::json::{canonical, Value};
use gnomon_core::store::{self, StoreError};

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

fn tmp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("gnomon-review2-{}-{}", std::process::id(), name));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn write_events(path: &PathBuf, events: &[Value]) {
    let mut text = String::new();
    for e in events {
        text.push_str(&canonical(e));
        text.push('\n');
    }
    fs::write(path, text).unwrap();
}

/// A ruled dispute: A (claude) vs B (gpt), contested by gpt, kimi rules for B.
fn ruled() -> (gnomon_core::ledger::Ledger, String, String) {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    l.append(draft("kimi-k1", 13, "resolve", resolve(&eid(&s), "new"))).unwrap();
    assert_eq!(l.state.truth(&a).unwrap(), "false");
    (l, a, b)
}

/// 1. Two spellings of the same file (a symlink) used different locks.
#[cfg(unix)]
#[test]
fn r1_symlinked_paths_share_one_lock() {
    let dir = tmp_dir("r1");
    let real = dir.join("log.jsonl");
    write_events(&real, &lab().events);
    let link = dir.join("link.jsonl");
    std::os::unix::fs::symlink(&real, &link).unwrap();
    let paths = Arc::new([real.clone(), link]);
    let writers = ["claude-c1", "gpt-g1", "kimi-k1", "claude-c2"];
    let handles: Vec<_> = writers
        .iter()
        .enumerate()
        .map(|(i, w)| {
            let paths = Arc::clone(&paths);
            let w = w.to_string();
            std::thread::spawn(move || {
                let path = &paths[i % 2];
                for n in 0..25 {
                    let body = Value::obj(vec![
                        ("compartment", Value::str("lab")),
                        ("content", Value::str(format!("{w} {n} {}", "x".repeat(4000)))),
                        ("medium", Value::str("chat")),
                        ("participants", Value::Array(vec![])),
                    ]);
                    store::append(path, draft(&w, 59, "episode", body)).expect("serialised append");
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let l = store::open(&real, None).expect("still verifies");
    assert_eq!(l.state.episodes.len(), 100);
    assert!(!dir.join("log.jsonl.lock").exists(), "no sidecar lock file to delete");
}

/// 2a. The losing party reopened a ruled dispute through a fresh claim.
#[test]
fn r2a_loser_cannot_reopen_a_ruling() {
    let (mut l, a, _b) = ruled();
    let d = add_claim(&mut l, "claude-c1", 14, claim_body("D", "asserted", "true"));
    assert_eq!(code(l.append(draft("claude-c2", 15, "supersede", sup(&a, &d, "contested", vec![])))), "E_SELF_DISPUTE");
    let e = add_claim(&mut l, "kimi-k1", 15, claim_body("E", "asserted", "true"));
    assert_eq!(code(l.append(draft("kimi-k1", 16, "supersede", sup(&a, &e, "contested", vec![])))), "E_FINAL");
    assert_eq!(l.state.truth(&a).unwrap(), "false");
}

/// 2b. The losing party overturned the winner with its own refutation.
#[test]
fn r2b_loser_cannot_overturn_the_winner() {
    let (mut l, a, b) = ruled();
    let v = l.append(draft("claude-c2", 14, "verdict", verdict(&b, "refutes"))).unwrap();
    assert_eq!(code(l.append(draft("claude-c1", 15, "supersede", sup(&b, &a, "refuted", vec![&eid(&v)])))), "E_EVIDENCE");
    // An outsider can't conscript the loser's claim as the opposing side
    // (consent), and with its own claim can't use the loser's verdict either.
    assert_eq!(code(l.append(draft("john", 16, "supersede", sup(&b, &a, "refuted", vec![&eid(&v)])))), "E_CONSENT");
    let own = add_claim(&mut l, "john", 17, claim_body("john's view", "asserted", "true"));
    assert_eq!(code(l.append(draft("john", 18, "supersede", sup(&b, &own, "refuted", vec![&eid(&v)])))), "E_EVIDENCE");
    assert_eq!(l.state.truth(&b).unwrap(), "true");
}

/// 3a. An author undid a valid refutation by contesting with its own claim.
#[test]
fn r3a_author_cannot_undo_a_refutation() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let v = l.append(draft("gpt-g1", 12, "verdict", verdict(&a, "refutes"))).unwrap();
    l.append(draft("gpt-g1", 13, "supersede", sup(&a, &b, "refuted", vec![&eid(&v)]))).unwrap();
    assert_eq!(l.state.truth(&a).unwrap(), "false");
    let c = add_claim(&mut l, "claude-c1", 14, claim_body("C", "asserted", "true"));
    assert_eq!(code(l.append(draft("claude-c1", 15, "supersede", sup(&a, &c, "contested", vec![])))), "E_SELF_DISPUTE");
    // An outsider can't revive a refuted claim by contesting it either.
    assert_eq!(code(l.append(draft("gpt-g1", 16, "supersede", sup(&a, &b, "contested", vec![])))), "E_FINAL");
    assert_eq!(l.state.truth(&a).unwrap(), "false");
}

/// 3b. An author pre-emptively blocked refutation with a self-dispute.
#[test]
fn r3b_no_self_dispute_to_block_refutation() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let c = add_claim(&mut l, "claude-c1", 11, claim_body("C", "asserted", "true"));
    assert_eq!(code(l.append(draft("claude-c1", 12, "supersede", sup(&a, &c, "contested", vec![])))), "E_SELF_DISPUTE");
    // And not via another identity of the same party either.
    assert_eq!(code(l.append(draft("claude-c2", 12, "supersede", sup(&a, &c, "contested", vec![])))), "E_SELF_DISPUTE");
    // So refutation by another party proceeds normally.
    let b = add_claim(&mut l, "gpt-g1", 13, claim_body("B", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 14, "verdict", verdict(&a, "refutes"))).unwrap();
    assert_eq!(code(l.append(draft("gpt-g1", 15, "supersede", sup(&a, &b, "refuted", vec![&eid(&v)])))), "OK");
}

/// 4. Filtered views leaked through last_seq (count/head/digest are covered
/// in the Python FFI tests).
#[test]
fn r4_filtered_view_has_no_last_seq() {
    let l = lab();
    let filtered = canonical(&l.state.to_value(Some(&["lab".to_string()])));
    assert!(!filtered.contains("last_seq"));
    let full = canonical(&l.state.to_value(None));
    assert!(full.contains("last_seq"));
}

/// 5. The cache never evicted.
#[test]
fn r5_cache_is_bounded() {
    let dir = tmp_dir("r5");
    let events = lab().events;
    for i in 0..40 {
        let p = dir.join(format!("log{i}.jsonl"));
        write_events(&p, &events);
        store::with_ledger(&p, |_| ()).unwrap();
    }
    assert!(store::cached_logs() <= 16, "cache holds {}", store::cached_logs());
}

/// 6. Format characters, noncharacters and private use in claims.
#[test]
fn r6_format_and_private_use_refused() {
    let mut l = lab();
    for c in ['\u{17b4}', '\u{600}', '\u{6dd}', '\u{110bd}', '\u{13430}', '\u{1bca0}', '\u{fffc}', '\u{2800}', '\u{1d159}', '\u{fffe}', '\u{e000}', '\u{10fffd}'] {
        let body = claim_body(&format!("a{c}b"), "asserted", "true");
        assert_eq!(code(l.append(draft("claude-c1", 10, "claim", body))), "E_HIDDEN_CHARS", "{:x}", c as u32);
    }
}

/// 7. A rejected first append left an empty file that verified as valid.
#[test]
fn r7_empty_log_is_not_found() {
    let dir = tmp_dir("r7");
    let p = dir.join("log.jsonl");
    let bad_genesis = store::append(&p, draft("mallory", 0, "claim", claim_body("x", "asserted", "true")));
    assert!(matches!(bad_genesis, Err(StoreError::Rejected(_))));
    assert!(matches!(store::open(&p, None), Err(StoreError::NotFound)));
    fs::write(&p, "").unwrap();
    assert!(matches!(store::open(&p, None), Err(StoreError::NotFound)));
    // And an empty file can still start a new log.
    store::append(&p, draft("john", 0, "register", register_body("john", "john", "human", &["owner"], &["lab"]))).unwrap();
    assert_eq!(store::open(&p, None).unwrap().events.len(), 1);
}

/// 8. A resolver joined the winning side afterwards.
#[test]
fn r8_resolver_cannot_join_a_side() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b_body = claim_body("B", "asserted", "true");
    let b = add_claim(&mut l, "gpt-g1", 11, b_body.clone());
    let s = l.append(draft("gpt-g1", 12, "supersede", sup(&a, &b, "contested", vec![]))).unwrap();
    l.append(draft("kimi-k1", 13, "resolve", resolve(&eid(&s), "new"))).unwrap();
    assert_eq!(code(l.append(draft("kimi-k1", 14, "claim", b_body))), "E_ROLE_CONFLICT");
}
