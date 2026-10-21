//! Independent review round 3, plus the dispute model that replaced the
//! patch-by-patch rules: disputes are about a pair of claims, parties are
//! frozen at opening, defeated claims cannot enter contests, rulings are
//! final per pair, evidence-backed refutation stays open, and every dispute
//! has a way out (a neutral resolver must exist, and the challenger may
//! withdraw).

mod common;
use common::*;

use std::fs;
use std::time::{Duration, Instant};

use gnomon_core::json::{canonical, Value};
use gnomon_core::ledger::Ledger;
use gnomon_core::store;

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
        (
            "evidence",
            Value::Array(evidence.into_iter().map(Value::str).collect()),
        ),
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

fn refuted_a() -> (Ledger, String, String) {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let v = l
        .append(draft("kimi-k1", 12, "verdict", verdict(&a, "refutes")))
        .unwrap();
    l.append(draft(
        "gpt-g1",
        13,
        "supersede",
        sup(&a, &b, "refuted", vec![&eid(&v)]),
    ))
    .unwrap();
    assert_eq!(l.state.truth(&a).unwrap(), "false");
    (l, a, b)
}

/// 1. A refuted claim was revived by placing it on the new side of a contest.
#[test]
fn n1_defeated_claim_cannot_enter_a_contest_on_either_side() {
    let (mut l, a, b) = refuted_a();
    let c = add_claim(&mut l, "kimi-k1", 14, claim_body("C", "asserted", "true"));
    assert_eq!(
        code(l.append(draft(
            "claude-c1",
            15,
            "supersede",
            sup(&c, &a, "contested", vec![])
        ))),
        "E_FINAL"
    );
    // Nor through an ally who joins A's side, nor with A on the old side.
    l.append(draft(
        "john",
        16,
        "claim",
        claim_body("A", "asserted", "true"),
    ))
    .unwrap();
    assert_eq!(
        code(l.append(draft(
            "john",
            17,
            "supersede",
            sup(&b, &a, "contested", vec![])
        ))),
        "E_FINAL"
    );
    assert_eq!(
        code(l.append(draft(
            "kimi-k1",
            18,
            "supersede",
            sup(&a, &c, "contested", vec![])
        ))),
        "E_FINAL"
    );
    assert_eq!(l.state.truth(&a).unwrap(), "false");
}

/// 1b. A claim that lost a ruling can't be revived by a stand-in either.
#[test]
fn n1b_ruled_loser_cannot_be_revived_by_an_ally() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l
        .append(draft(
            "gpt-g1",
            12,
            "supersede",
            sup(&a, &b, "contested", vec![]),
        ))
        .unwrap();
    l.append(draft("kimi-k1", 13, "resolve", resolve(&eid(&s), "new")))
        .unwrap();
    l.append(draft(
        "john",
        14,
        "claim",
        claim_body("A", "asserted", "true"),
    ))
    .unwrap(); // ally joins A
    assert_eq!(
        code(l.append(draft(
            "john",
            15,
            "supersede",
            sup(&b, &a, "contested", vec![])
        ))),
        "E_FINAL"
    );
    assert_eq!(l.state.truth(&b).unwrap(), "true");
}

/// 2. Contesting an outsider's claim used to gag that outsider forever.
#[test]
fn n2_being_drawn_into_a_dispute_does_not_gag_you() {
    let mut l = lab();
    let n = add_claim(&mut l, "kimi-k1", 10, claim_body("N", "asserted", "true"));
    let x = add_claim(&mut l, "claude-c1", 11, claim_body("X", "asserted", "true"));
    let s = l
        .append(draft(
            "claude-c1",
            12,
            "supersede",
            sup(&n, &x, "contested", vec![]),
        ))
        .unwrap();
    l.append(draft("gpt-g1", 13, "resolve", resolve(&eid(&s), "both")))
        .unwrap();
    // kimi may still refute X, citing neutral evidence.
    let v = l
        .append(draft("john", 14, "verdict", verdict(&x, "refutes")))
        .unwrap();
    let y = add_claim(&mut l, "kimi-k1", 15, claim_body("Y", "asserted", "true"));
    assert_eq!(
        code(l.append(draft(
            "kimi-k1",
            16,
            "supersede",
            sup(&x, &y, "refuted", vec![&eid(&v)])
        ))),
        "OK"
    );
    assert_eq!(l.state.truth(&x).unwrap(), "false");
}

/// 3a. A dispute in a compartment with no neutral writer could never end.
#[test]
fn n3a_no_dispute_without_a_possible_resolver() {
    let mut l = lab();
    for (i, (w, p)) in [("p1", "party-1"), ("p2", "party-2")].iter().enumerate() {
        l.append(draft(
            "john",
            6 + i as u32,
            "register",
            register_body(w, p, "model", &["write", "verify"], &["duo"]),
        ))
        .unwrap();
    }
    let a = add_claim(
        &mut l,
        "p1",
        10,
        claim_body("A", "asserted", "true").with("compartment", Value::str("duo")),
    );
    let b = add_claim(
        &mut l,
        "p2",
        11,
        claim_body("B", "asserted", "true").with("compartment", Value::str("duo")),
    );
    assert_eq!(
        code(l.append(draft(
            "p2",
            12,
            "supersede",
            sup(&a, &b, "contested", vec![])
        ))),
        "E_NO_RESOLVER"
    );
    // Once a neutral writer can see the compartment, the dispute may open.
    l.append(draft(
        "john",
        13,
        "register",
        register_body("p3", "party-3", "model", &["verify"], &["duo"]),
    ))
    .unwrap();
    assert_eq!(
        code(l.append(draft(
            "p2",
            14,
            "supersede",
            sup(&a, &b, "contested", vec![])
        ))),
        "OK"
    );
}

/// 3b. The challenger can always withdraw: the way out needing no one else.
#[test]
fn n3b_challenger_can_withdraw() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    let s = l
        .append(draft(
            "gpt-g1",
            12,
            "supersede",
            sup(&a, &b, "contested", vec![]),
        ))
        .unwrap();
    assert_eq!(l.state.truth(&a).unwrap(), "true");
    assert!(l.state.disputed(&a));
    // Only the challenger's party may withdraw.
    assert_eq!(
        code(l.append(draft(
            "claude-c1",
            13,
            "resolve",
            resolve(&eid(&s), "withdrawn")
        ))),
        "E_RESOLVE_PARTY"
    );
    assert_eq!(
        code(l.append(draft(
            "kimi-k1",
            13,
            "resolve",
            resolve(&eid(&s), "withdrawn")
        ))),
        "E_RESOLVE_PARTY"
    );
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            14,
            "resolve",
            resolve(&eid(&s), "withdrawn")
        ))),
        "OK"
    );
    assert_eq!(
        (l.state.truth(&a).unwrap(), l.state.truth(&b).unwrap()),
        ("true".into(), "true".into())
    );
    // Withdrawal is final for the party that withdrew (no withdraw-and-reraise
    // loops), but it is not a ruling: another party may raise the challenge.
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            15,
            "supersede",
            sup(&a, &b, "contested", vec![])
        ))),
        "E_FINAL"
    );
    l.append(draft(
        "kimi-k1",
        16,
        "claim",
        claim_body("B", "asserted", "true"),
    ))
    .unwrap();
    assert_eq!(
        code(l.append(draft(
            "kimi-k1",
            17,
            "supersede",
            sup(&a, &b, "contested", vec![])
        ))),
        "OK"
    );
}

/// 3c. Parties are frozen at opening; nobody can join a side mid-dispute.
#[test]
fn n3c_no_joining_sides_mid_dispute() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b_body = claim_body("B", "asserted", "true");
    let b = add_claim(&mut l, "gpt-g1", 11, b_body.clone());
    l.append(draft(
        "gpt-g1",
        12,
        "supersede",
        sup(&a, &b, "contested", vec![]),
    ))
    .unwrap();
    assert_eq!(
        code(l.append(draft("kimi-k1", 13, "claim", b_body))),
        "E_OPEN_DISPUTE"
    );
    assert_eq!(
        code(l.append(draft(
            "john",
            13,
            "claim",
            claim_body("A", "asserted", "true")
        ))),
        "E_OPEN_DISPUTE"
    );
}

/// 4. A read callback that appended deadlocked on its own lock, including
/// via a helper thread. Callbacks now run on a snapshot with the lock
/// released, so appending from inside one simply works.
#[test]
fn n4_append_inside_read_works_without_deadlock() {
    let dir = std::env::temp_dir().join(format!("gnomon-review3-{}-n4", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let p = dir.join("log.jsonl");
    let mut text = String::new();
    for e in lab().events {
        text.push_str(&canonical(&e));
        text.push('\n');
    }
    fs::write(&p, text).unwrap();
    let start = Instant::now();
    let snapshot_len = store::with_ledger(&p, |l| {
        store::append(
            &p,
            draft(
                "claude-c1",
                30,
                "claim",
                claim_body("x", "asserted", "true"),
            ),
        )
        .unwrap();
        let p2 = p.clone();
        std::thread::spawn(move || {
            store::append(
                &p2,
                draft("gpt-g1", 31, "claim", claim_body("y", "asserted", "true")),
            )
            .unwrap();
        })
        .join()
        .unwrap();
        l.events.len()
    })
    .unwrap();
    assert!(start.elapsed() < Duration::from_secs(2));
    assert_eq!(snapshot_len, 5, "callback sees the snapshot it was given");
    assert_eq!(store::open(&p, None).unwrap().events.len(), 7);
    // And the cache did not keep the stale snapshot.
    assert_eq!(store::with_ledger(&p, |l| l.events.len()).unwrap(), 7);
}

/// 6. A claim asserted false (never refuted) can still be contested.
#[test]
fn n6_asserted_false_is_not_defeated() {
    let mut l = lab();
    let a = add_claim(
        &mut l,
        "claude-c1",
        10,
        claim_body("A is not so", "asserted", "false"),
    );
    let b = add_claim(
        &mut l,
        "gpt-g1",
        11,
        claim_body("A is so", "asserted", "true"),
    );
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            12,
            "supersede",
            sup(&a, &b, "contested", vec![])
        ))),
        "OK"
    );
}

/// Refuting your own claim (retraction) is allowed, but only on someone
/// else's evidence, since nobody can verify their own claim.
#[test]
fn retraction_needs_outside_evidence() {
    let mut l = lab();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let a2 = add_claim(
        &mut l,
        "claude-c1",
        11,
        claim_body("A corrected", "asserted", "true"),
    );
    assert_eq!(
        code(l.append(draft(
            "claude-c1",
            12,
            "supersede",
            sup(&a, &a2, "refuted", vec![&a])
        ))),
        "E_EVIDENCE"
    );
    let v = l
        .append(draft("gpt-g1", 13, "verdict", verdict(&a, "refutes")))
        .unwrap();
    assert_eq!(
        code(l.append(draft(
            "claude-c1",
            14,
            "supersede",
            sup(&a, &a2, "refuted", vec![&eid(&v)])
        ))),
        "OK"
    );
}
