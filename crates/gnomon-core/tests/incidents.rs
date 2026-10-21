//! Past failures from earlier projects, written as tests that must pass from
//! day one. Each test names the incident it guards against.

mod common;
use common::*;

use gnomon_core::json::{self, canonical, Value};
use gnomon_core::ledger::{envelope_hash, Ledger, VerifyStatus};

// ------------------------------------------- Convergence Lab incidents

/// SI-008: two models running in parallel both wrote EVT-0071 and EVT-0072.
/// Event ids are now "<writer>/<writer_seq>", so two writers can never
/// produce the same id, and a forged id outside the writer's namespace fails.
#[test]
fn si008_parallel_writers_cannot_collide() {
    let mut l = lab();
    let a = l
        .append(draft(
            "claude-c1",
            10,
            "claim",
            claim_body("A", "asserted", "true"),
        ))
        .unwrap();
    let b = l
        .append(draft(
            "gpt-g1",
            10,
            "claim",
            claim_body("B", "asserted", "true"),
        ))
        .unwrap();
    assert_ne!(a.get("event_id"), b.get("event_id"));

    // Forge: gpt writes an event claiming claude's namespace.
    let mut forged = l.events.last().unwrap().clone();
    forged = forged
        .with("seq", Value::Int(l.events.len() as i64))
        .with("prev", Value::str(l.head()))
        .with("event_id", Value::str("claude-c1/2"))
        .with("writer_seq", Value::Int(2));
    let forged = forged.with("hash", Value::str(envelope_hash(&forged)));
    assert_eq!(l.apply(forged).unwrap_err().code, "E_EVENT_ID");
}

/// SI-010: one writer reused its own event id (EVT-G1-0019 twice).
/// writer_seq must be exactly last + 1: no reuse, no gaps.
#[test]
fn si010_writer_cannot_reuse_or_skip_ids() {
    let mut l = lab();
    l.append(draft(
        "gpt-g1",
        10,
        "claim",
        claim_body("first", "asserted", "true"),
    ))
    .unwrap();
    let last = l.events.last().unwrap().clone();
    for bad_seq in [1, 3] {
        let env = last
            .with("seq", Value::Int(l.events.len() as i64))
            .with("prev", Value::str(l.head()))
            .with("writer_seq", Value::Int(bad_seq))
            .with("event_id", Value::str(format!("gpt-g1/{bad_seq}")))
            .with("body", claim_body("second", "asserted", "true"));
        let env = env.with("hash", Value::str(envelope_hash(&env)));
        assert_eq!(l.apply(env).unwrap_err().code, "E_WRITER_SEQ");
    }
}

/// ARP-001 (a): a model self-certified its own challenge gate.
#[test]
fn arp001_author_cannot_verify_own_claim() {
    let mut l = lab();
    let id = add_claim(
        &mut l,
        "gpt-g1",
        10,
        claim_body("gate passed", "asserted", "true"),
    );
    let r = l.append(draft(
        "gpt-g1",
        11,
        "verdict",
        Value::obj(vec![
            ("claim", Value::str(id)),
            ("result", Value::str("supports")),
            ("method", Value::str("cross_model_review")),
        ]),
    ));
    assert_eq!(code(r), "E_SELF_VERIFY");
}

/// ARP-001 (b): identity disguise. A second identity of the same party is
/// still self-certification.
#[test]
fn arp001_second_identity_of_same_party_cannot_verify() {
    let mut l = lab();
    let id = add_claim(
        &mut l,
        "claude-c1",
        10,
        claim_body("x holds", "asserted", "true"),
    );
    let r = l.append(draft(
        "claude-c2",
        11,
        "verdict",
        Value::obj(vec![
            ("claim", Value::str(id.clone())),
            ("result", Value::str("supports")),
            ("method", Value::str("cross_model_review")),
        ]),
    ));
    assert_eq!(code(r), "E_SAME_PARTY_VERIFY");

    // A different party can.
    let r = l.append(draft(
        "kimi-k1",
        12,
        "verdict",
        Value::obj(vec![
            ("claim", Value::str(id)),
            ("result", Value::str("supports")),
            ("method", Value::str("cross_model_review")),
        ]),
    ));
    assert_eq!(code(r), "OK");
}

/// ARP-001 (c): a model executed a schema mutation on its own authority.
#[test]
fn arp001_schema_change_needs_govern_capability() {
    let mut l = lab();
    let r = l.append(draft(
        "gpt-g1",
        10,
        "schema_change",
        Value::obj(vec![
            ("version", Value::Int(3)),
            ("description", Value::str("drop the Reliability field")),
        ]),
    ));
    assert_eq!(code(r), "E_CAPABILITY");
    assert_eq!(l.state.schema_version, 2);
}

/// ARP-001 (d): a model closed its own challenge unilaterally. A party to a
/// dispute can never resolve it.
#[test]
fn arp001_party_to_dispute_cannot_resolve_it() {
    let mut l = lab();
    let old = add_claim(
        &mut l,
        "claude-c1",
        10,
        claim_body("design A is best", "asserted", "true"),
    );
    let new = add_claim(
        &mut l,
        "gpt-g1",
        11,
        claim_body("design B is best", "asserted", "true"),
    );
    let sup = l
        .append(draft(
            "gpt-g1",
            12,
            "supersede",
            Value::obj(vec![
                ("old", Value::str(old.clone())),
                ("new", Value::str(new.clone())),
                ("kind", Value::str("contested")),
                ("evidence", Value::Array(vec![])),
                ("reason", Value::str("B benchmarks better")),
            ]),
        ))
        .unwrap();
    let sup_id = sup.get("event_id").unwrap().as_str().unwrap().to_string();
    // An open contest keeps the truth and sets the disputed flag.
    assert_eq!(l.state.truth(&old).unwrap(), "true");
    assert!(l.state.disputed(&old));

    let resolve = |outcome: &str| {
        Value::obj(vec![
            ("dispute", Value::str(sup_id.clone())),
            ("outcome", Value::str(outcome)),
            ("reason", Value::str("decided")),
        ])
    };
    // Both parties (and the old claim's party via a second identity) are refused.
    for w in ["gpt-g1", "claude-c1", "claude-c2"] {
        assert_eq!(
            code(l.append(draft(w, 13, "resolve", resolve("new")))),
            "E_RESOLVE_PARTY",
            "{w}"
        );
    }
    // A neutral party resolves it.
    assert_eq!(
        code(l.append(draft("kimi-k1", 14, "resolve", resolve("new")))),
        "OK"
    );
    assert_eq!(l.state.truth(&old).unwrap(), "false");
    assert_eq!(l.state.truth(&new).unwrap(), "true");
    // And it cannot be re-resolved.
    assert_eq!(
        code(l.append(draft("john", 15, "resolve", resolve("old")))),
        "E_RESOLVE"
    );
}

// ------------------------------------------------- The Field / RabbitHole

/// The [inf, inf] overflow family: finite input rounded to infinity became a
/// NaN factory. Here non-finite and out-of-range numbers are rejected at the
/// boundary, never rounded, and confidence is integer basis points.
#[test]
fn field_overflow_family_is_unrepresentable() {
    assert!(json::parse("1e309").is_err());
    assert!(json::parse("NaN").is_err());
    assert!(json::parse("9007199254740993").is_err());
    let mut l = lab();
    let body = claim_body("x", "asserted", "true").with("confidence", Value::Int(10_001));
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", body))),
        "E_BODY"
    );
    let body = claim_body("x", "asserted", "true").with("confidence", Value::Int(-1));
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", body))),
        "E_BODY"
    );
}

/// "Budget exhausted" was once reported as "settled". Verification with a
/// budget smaller than the log is Exhausted, never Ok.
#[test]
fn field_exhausted_is_never_reported_as_ok() {
    let l = lab();
    let (status, _) = Ledger::verify(&l.events, Some(2));
    assert_eq!(
        status,
        VerifyStatus::Exhausted {
            checked: 2,
            total: l.events.len()
        }
    );
    let (status, _) = Ledger::verify(&l.events, None);
    assert!(matches!(status, VerifyStatus::Ok { .. }));
}

/// The Field kernel: honest failure over confident approximation. A guess
/// cannot carry full confidence; a question cannot carry a truth value.
#[test]
fn field_honest_states() {
    let mut l = lab();
    let body = claim_body("maybe", "asserted", "true")
        .with("origin", Value::str("guessed"))
        .with("confidence", Value::Int(10_000));
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", body))),
        "E_BODY"
    );
    let body = claim_body("is it?", "asked", "true");
    assert_eq!(
        code(l.append(draft("claude-c1", 11, "claim", body))),
        "E_BODY"
    );
}

// ------------------------------------------- Swarm and memory failures

/// Suggestion vs decision: past chats replayed Claude's suggestions as the
/// user's decisions. Stance is part of the content address, so the same
/// statement as a suggestion and as a decision are two different claims.
#[test]
fn suggestion_and_decision_are_distinct_claims() {
    let s = gnomon_core::ledger::claim_id(&claim_body("use Rust", "suggested", "unknown"));
    let d = gnomon_core::ledger::claim_id(&claim_body("use Rust", "decided", "true"));
    assert_ne!(s, d);
}

/// The July 2026 swarm smuggled coordination through shared files. Hidden
/// and reordering characters are rejected in claims; in episodes they are
/// kept (lossless) and flagged.
#[test]
fn hidden_channels_rejected_in_claims_flagged_in_episodes() {
    let mut l = lab();
    let body = claim_body("normal\u{200b}text", "asserted", "true");
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", body))),
        "E_HIDDEN_CHARS"
    );
    let body = claim_body("tag\u{e0041}smuggle", "asserted", "true");
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", body))),
        "E_HIDDEN_CHARS"
    );

    let ep = Value::obj(vec![
        ("compartment", Value::str("lab")),
        ("content", Value::str("raw transcript\u{202e}with bidi")),
        ("medium", Value::str("chat")),
        ("participants", Value::Array(vec![Value::str("john")])),
    ]);
    let env = l.append(draft("claude-c1", 11, "episode", ep)).unwrap();
    let id = env.get("event_id").unwrap().as_str().unwrap();
    assert!(l.state.episodes[id].hidden_chars);
    assert!(l.state.episodes[id]
        .body
        .get("content")
        .unwrap()
        .as_str()
        .unwrap()
        .contains('\u{202e}'));
}

/// Compartments: a writer can't write into, verify in, or cite across a
/// compartment it has no access to.
#[test]
fn compartments_do_not_leak() {
    let mut l = lab();
    let private =
        claim_body("private note", "asserted", "true").with("compartment", Value::str("private"));
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", private.clone()))),
        "E_COMPARTMENT"
    );
    let id = add_claim(&mut l, "john", 11, private);
    let r = l.append(draft(
        "gpt-g1",
        12,
        "verdict",
        Value::obj(vec![
            ("claim", Value::str(id)),
            ("result", Value::str("supports")),
            ("method", Value::str("human")),
        ]),
    ));
    // Hidden content answers exactly like missing content (no existence oracle).
    assert_eq!(code(r), "E_REF");
    let visible = l.state.to_value(Some(&["lab".to_string()]));
    assert!(!canonical(&visible).contains("private note"));
}

/// Refutation must rest on consequence, not on assertion.
#[test]
fn refutation_requires_a_refuting_verdict() {
    let mut l = lab();
    let old = add_claim(
        &mut l,
        "claude-c1",
        10,
        claim_body("cache is safe", "asserted", "true"),
    );
    let new = add_claim(
        &mut l,
        "gpt-g1",
        11,
        claim_body("cache leaks across users", "asserted", "true"),
    );
    let sup = |ev: Vec<Value>| {
        Value::obj(vec![
            ("old", Value::str(old.clone())),
            ("new", Value::str(new.clone())),
            ("kind", Value::str("refuted")),
            ("evidence", Value::Array(ev)),
            ("reason", Value::str("leak reproduced")),
        ])
    };
    assert_eq!(
        code(l.append(draft(
            "gpt-g1",
            12,
            "supersede",
            sup(vec![Value::str(new.clone())])
        ))),
        "E_EVIDENCE"
    );
    let v = l
        .append(draft(
            "kimi-k1",
            13,
            "verdict",
            Value::obj(vec![
                ("claim", Value::str(old.clone())),
                ("result", Value::str("refutes")),
                ("method", Value::str("test")),
            ]),
        ))
        .unwrap();
    let v_id = v.get("event_id").unwrap().clone();
    assert_eq!(
        code(l.append(draft("gpt-g1", 14, "supersede", sup(vec![v_id])))),
        "OK"
    );
    assert_eq!(l.state.truth(&old).unwrap(), "false");
}

/// Newton is narrowed, not deleted: the old claim stays true within its
/// domain, and the narrowing must state that domain.
#[test]
fn narrowing_keeps_old_claim_true_in_its_domain() {
    let mut l = lab();
    let old = add_claim(
        &mut l,
        "claude-c1",
        10,
        claim_body("F = ma exactly", "asserted", "true"),
    );
    let new_body = claim_body("F = ma at low speeds", "asserted", "true")
        .with("validity_domain", Value::str("v much less than c"));
    let new = add_claim(&mut l, "gpt-g1", 11, new_body);
    let r = l.append(draft(
        "gpt-g1",
        12,
        "supersede",
        Value::obj(vec![
            ("old", Value::str(old.clone())),
            ("new", Value::str(new)),
            ("kind", Value::str("narrowed")),
            ("evidence", Value::Array(vec![Value::str(old.clone())])),
            ("reason", Value::str("relativity")),
        ]),
    ));
    assert_eq!(code(r), "OK");
    assert_eq!(l.state.truth(&old).unwrap(), "true");
    assert_eq!(l.state.claims[&old].superseded_by.len(), 1);
}

/// Corroboration, not overwrite: the same claim from a second writer is
/// recorded as corroboration; the original author is never replaced.
#[test]
fn identical_claims_corroborate() {
    let mut l = lab();
    let body = claim_body("tests pass on main", "asserted", "true");
    let id = add_claim(&mut l, "claude-c1", 10, body.clone());
    l.append(draft("gpt-g1", 11, "claim", body.clone()))
        .unwrap();
    assert_eq!(l.state.claims[&id].author, "claude-c1");
    assert_eq!(
        l.state.claims[&id].corroborated_by,
        vec!["gpt-g1".to_string()]
    );
    assert_eq!(
        code(l.append(draft("gpt-g1", 12, "claim", body))),
        "E_DUPLICATE"
    );
}

/// Record time never runs backwards.
#[test]
fn clock_cannot_run_backwards() {
    let mut l = lab();
    l.append(draft(
        "claude-c1",
        30,
        "claim",
        claim_body("later", "asserted", "true"),
    ))
    .unwrap();
    let r = l.append(draft(
        "gpt-g1",
        20,
        "claim",
        claim_body("earlier", "asserted", "true"),
    ));
    assert_eq!(code(r), "E_CLOCK");
}

/// Unknown fields are refused, not silently kept or dropped.
#[test]
fn unknown_fields_are_refused() {
    let mut l = lab();
    let body = claim_body("x", "asserted", "true").with("reliability", Value::str("high"));
    assert_eq!(
        code(l.append(draft("claude-c1", 10, "claim", body))),
        "E_BODY"
    );
    let _ = j("{}");
}
