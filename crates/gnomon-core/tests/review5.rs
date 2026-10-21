//! Independent review round 5 (flags and appeals): each finding reproduced
//! as a test that must now fail to exploit.

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
        (
            "evidence",
            Value::Array(evidence.into_iter().map(Value::str).collect()),
        ),
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
        (
            "evidence",
            Value::Array(evidence.into_iter().map(Value::str).collect()),
        ),
        ("reason", Value::str("appeal")),
    ])
}

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

/// lab() plus several more independent parties.
fn world() -> Ledger {
    let mut l = lab();
    for (i, (w, p)) in [
        ("mistral-m1", "mistral"),
        ("qwen-q1", "qwen"),
        ("deepseek-d1", "deepseek"),
        ("meta-l1", "meta"),
        ("judge", "judge-co"),
    ]
    .iter()
    .enumerate()
    {
        l.append(draft(
            "john",
            6 + i as u32,
            "register",
            register_body(w, p, "model", &["write", "verify"], &["lab"]),
        ))
        .unwrap();
    }
    l
}

/// B1. A refutation that ends a contest made the refuter a "resolver".
#[test]
fn b1_refuter_closing_a_contest_is_not_a_judge() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    l.append(draft(
        "gpt-g1",
        12,
        "supersede",
        sup(&a, &b, "contested", vec![]),
    ))
    .unwrap();
    let v = l
        .append(draft("mistral-m1", 13, "verdict", verdict(&b, "refutes")))
        .unwrap();
    let z = add_claim(&mut l, "kimi-k1", 14, claim_body("Z", "asserted", "true"));
    l.append(draft(
        "kimi-k1",
        15,
        "supersede",
        sup(&b, &z, "refuted", vec![&eid(&v)]),
    ))
    .unwrap();
    assert_eq!(
        (l.state.truth(&a).unwrap(), l.state.truth(&b).unwrap()),
        ("true".into(), "false".into())
    );
    // kimi did not judge: it may still corroborate A, and its verdicts still count.
    assert_eq!(
        code(l.append(draft(
            "kimi-k1",
            16,
            "claim",
            claim_body("A", "asserted", "true")
        ))),
        "OK"
    );
}

/// D1. A party to an open contest won it with its own verdict.
#[test]
fn d1_contest_party_cannot_win_with_own_verdict() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    l.append(draft(
        "gpt-g1",
        12,
        "supersede",
        sup(&a, &b, "contested", vec![]),
    ))
    .unwrap();
    let own = l
        .append(draft("gpt-g1", 13, "verdict", verdict(&a, "refutes")))
        .unwrap();
    let z = add_claim(&mut l, "gpt-g1", 14, claim_body("Z", "asserted", "true"));
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            15,
            "supersede",
            sup(&a, &z, "refuted", vec![&eid(&own)])
        ))),
        "E_EVIDENCE"
    );
    assert_eq!(l.state.truth(&a).unwrap(), "true");
    // A neutral party's verdict still ends it.
    let neutral = l
        .append(draft("mistral-m1", 16, "verdict", verdict(&a, "refutes")))
        .unwrap();
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            17,
            "supersede",
            sup(&a, &z, "refuted", vec![&eid(&neutral)])
        ))),
        "OK"
    );
}

/// Reinstate A on appeal (gpt refuted it via kimi; mistral supported; judge decided).
fn reinstated_a(l: &mut Ledger) -> String {
    let a = add_claim(l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let k = add_claim(l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    let v = l
        .append(draft("kimi-k1", 12, "verdict", verdict(&a, "refutes")))
        .unwrap();
    l.append(draft(
        "gpt-g1",
        13,
        "supersede",
        sup(&a, &k, "refuted", vec![&eid(&v)]),
    ))
    .unwrap();
    let s = l
        .append(draft("mistral-m1", 14, "verdict", verdict(&a, "supports")))
        .unwrap();
    let ap = l
        .append(draft("claude-c1", 15, "appeal", appeal(&a, vec![&eid(&s)])))
        .unwrap();
    l.append(draft(
        "judge",
        16,
        "resolve",
        decide(&eid(&ap), "reinstated"),
    ))
    .unwrap();
    assert_eq!(l.state.truth(&a).unwrap(), "true");
    a
}

/// D2/D3. A reinstated claim was killed again by a contest, without evidence.
#[test]
fn d2_reinstated_claim_cannot_be_re_killed_by_argument_from_its_opponents() {
    let mut l = world();
    let a = reinstated_a(&mut l);
    // The original refuter cannot argue its way back in.
    let c2 = add_claim(&mut l, "gpt-g1", 20, claim_body("C2", "asserted", "true"));
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            21,
            "supersede",
            sup(&a, &c2, "contested", vec![])
        ))),
        "E_FINAL"
    );
    // Nor can the refuting verdict's giver.
    let c3 = add_claim(&mut l, "kimi-k1", 22, claim_body("C3", "asserted", "true"));
    assert_eq!(
        code(l.append(draft(
            "kimi-k1",
            23,
            "supersede",
            sup(&a, &c3, "contested", vec![])
        ))),
        "E_FINAL"
    );
    assert_eq!(l.state.truth(&a).unwrap(), "true");
}

/// One argument per party per claim: rewording does not re-open a ruling.
#[test]
fn one_argument_per_party_per_claim() {
    let mut l = world();
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
    l.append(draft("judge", 13, "resolve", decide(&eid(&s), "old")))
        .unwrap();
    let b2 = add_claim(
        &mut l,
        "gpt-g1",
        14,
        claim_body("B, reworded", "asserted", "true"),
    );
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            15,
            "supersede",
            sup(&a, &b2, "contested", vec![])
        ))),
        "E_FINAL"
    );
    // Evidence still works.
    let v = l
        .append(draft("qwen-q1", 16, "verdict", verdict(&a, "refutes")))
        .unwrap();
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            17,
            "supersede",
            sup(&a, &b2, "refuted", vec![&eid(&v)])
        ))),
        "OK"
    );
}

/// D4. A refuter filed a sham appeal on its victim.
#[test]
fn d4_opponents_cannot_appeal() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    let v = l
        .append(draft("kimi-k1", 12, "verdict", verdict(&a, "refutes")))
        .unwrap();
    l.append(draft(
        "gpt-g1",
        13,
        "supersede",
        sup(&a, &k, "refuted", vec![&eid(&v)]),
    ))
    .unwrap();
    let s = l
        .append(draft("mistral-m1", 14, "verdict", verdict(&a, "supports")))
        .unwrap();
    for w in ["gpt-g1", "kimi-k1"] {
        assert_eq!(
            code(l.append(draft(w, 15, "appeal", appeal(&a, vec![&eid(&s)])))),
            "E_STAKE",
            "{w}"
        );
    }
    assert_eq!(
        code(l.append(draft("claude-c1", 16, "appeal", appeal(&a, vec![&eid(&s)])))),
        "OK"
    );
}

/// D5a. A party that refuted the claim (uncited) decided its appeal.
#[test]
fn d5a_any_verdict_giver_cannot_decide_the_appeal() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 11, claim_body("K", "asserted", "true"));
    l.append(draft("deepseek-d1", 12, "verdict", verdict(&a, "refutes")))
        .unwrap(); // uncited
    let v = l
        .append(draft("kimi-k1", 13, "verdict", verdict(&a, "refutes")))
        .unwrap();
    l.append(draft(
        "gpt-g1",
        14,
        "supersede",
        sup(&a, &k, "refuted", vec![&eid(&v)]),
    ))
    .unwrap();
    let s = l
        .append(draft("mistral-m1", 15, "verdict", verdict(&a, "supports")))
        .unwrap();
    let ap = l
        .append(draft("claude-c1", 16, "appeal", appeal(&a, vec![&eid(&s)])))
        .unwrap();
    assert_eq!(
        code(l.append(draft(
            "deepseek-d1",
            17,
            "resolve",
            decide(&eid(&ap), "upheld")
        ))),
        "E_RESOLVE_PARTY"
    );
    assert_eq!(
        code(l.append(draft("judge", 18, "resolve", decide(&eid(&ap), "upheld")))),
        "OK"
    );
}

/// D5b. An ally who joined the winning side after the ruling decided the appeal.
#[test]
fn d5b_late_allies_of_the_winner_cannot_decide() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b_body = claim_body("B", "asserted", "true");
    let b = add_claim(&mut l, "gpt-g1", 11, b_body.clone());
    let s = l
        .append(draft(
            "gpt-g1",
            12,
            "supersede",
            sup(&a, &b, "contested", vec![]),
        ))
        .unwrap();
    l.append(draft("judge", 13, "resolve", decide(&eid(&s), "new")))
        .unwrap();
    l.append(draft("meta-l1", 14, "claim", b_body)).unwrap(); // joins B after the ruling
    let sv = l
        .append(draft("mistral-m1", 15, "verdict", verdict(&a, "supports")))
        .unwrap();
    let ap = l
        .append(draft(
            "claude-c1",
            16,
            "appeal",
            appeal(&a, vec![&eid(&sv)]),
        ))
        .unwrap();
    assert_eq!(
        code(l.append(draft("meta-l1", 17, "resolve", decide(&eid(&ap), "upheld")))),
        "E_RESOLVE_PARTY"
    );
    assert_eq!(
        code(l.append(draft(
            "qwen-q1",
            18,
            "resolve",
            decide(&eid(&ap), "reinstated")
        ))),
        "OK"
    );
    // D6: the reinstated loser and the original winner are both true.
    assert_eq!(
        (l.state.truth(&a).unwrap(), l.state.truth(&b).unwrap()),
        ("true".into(), "true".into())
    );
}

/// Contest rulings: a party that already judged either claim cannot rule.
#[test]
fn contest_judges_must_not_have_judged_either_claim() {
    let mut l = world();
    let a = add_claim(&mut l, "claude-c1", 10, claim_body("A", "asserted", "true"));
    let b = add_claim(&mut l, "gpt-g1", 11, claim_body("B", "asserted", "true"));
    l.append(draft("qwen-q1", 12, "verdict", verdict(&b, "supports")))
        .unwrap();
    let s = l
        .append(draft(
            "gpt-g1",
            13,
            "supersede",
            sup(&a, &b, "contested", vec![]),
        ))
        .unwrap();
    assert_eq!(
        code(l.append(draft("qwen-q1", 14, "resolve", decide(&eid(&s), "new")))),
        "E_RESOLVE_PARTY"
    );
    assert_eq!(
        code(l.append(draft("judge", 15, "resolve", decide(&eid(&s), "new")))),
        "OK"
    );
}
