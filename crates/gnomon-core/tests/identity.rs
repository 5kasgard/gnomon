//! M6: who is who. Each session or agent is its own writer; the model it
//! runs and the person it acts for are on record; names in recordings are
//! only names until bound to a writer.

mod common;
use common::*;

use gnomon_core::json::Value;
use gnomon_core::ledger::Ledger;

fn instance(writer: &str, model: &str, session: &str, caps: &[&str]) -> Value {
    register_plain(writer, "anthropic", "model", caps, &["lab"])
        .with("model", Value::str(model))
        .with("operator", Value::str("john"))
        .with("surface", Value::str("claude-code"))
        .with("session", Value::str(session))
        .with("name", Value::str("Claude"))
}

fn base() -> Ledger {
    let mut l = Ledger::new();
    l.append(draft(
        "john",
        0,
        "register",
        register_plain("john", "john", "human", &["owner"], &["lab", "private"])
            .with("name", Value::str("John Doe"))
            .with("schema_version", Value::Int(2)),
    ))
    .unwrap();
    l
}

#[test]
fn models_say_what_they_run_and_who_they_act_for() {
    let mut l = base();
    let plain = register_plain("c1", "anthropic", "model", &["write"], &["lab"]);
    assert_eq!(
        code(l.append(draft("john", 1, "register", plain.clone()))),
        "E_BODY"
    );
    let no_op = plain.with("model", Value::str("claude-opus-5-5"));
    assert_eq!(
        code(l.append(draft("john", 1, "register", no_op.clone()))),
        "E_BODY"
    );
    let ghost = no_op.with("operator", Value::str("nobody"));
    assert_eq!(
        code(l.append(draft("john", 1, "register", ghost))),
        "E_IDENTITY"
    );
    let tool = register_plain("t1", "tools", "tool", &["write"], &["lab"])
        .with("operator", Value::str("john"));
    l.append(draft("john", 1, "register", tool)).unwrap();
    // An operator is a person, not another program.
    let by_tool = no_op.with("operator", Value::str("t1"));
    assert_eq!(
        code(l.append(draft("john", 2, "register", by_tool))),
        "E_IDENTITY"
    );
    // A person has no model or session.
    let human =
        register_plain("bo", "bo", "human", &["write"], &["lab"]).with("model", Value::str("x"));
    assert_eq!(
        code(l.append(draft("john", 2, "register", human))),
        "E_BODY"
    );
    // Opaque ids show exactly as they are: no spaces, no hidden characters.
    let spaced = instance("c1", "claude opus", "s1", &["write"]);
    assert_eq!(
        code(l.append(draft("john", 2, "register", spaced))),
        "E_BODY"
    );
    let hidden = instance("c1", "claude-opus-5-5", "s\u{200b}1", &["write"]);
    assert_eq!(
        code(l.append(draft("john", 2, "register", hidden))),
        "E_HIDDEN_CHARS"
    );
    assert_eq!(
        code(l.append(draft(
            "john",
            2,
            "register",
            instance("c1", "claude-opus-5-5", "s1", &["write"])
        ))),
        "OK"
    );
}

#[test]
fn one_session_one_writer() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("c1", "claude-opus-5-5", "s1", &["write"]),
    ))
    .unwrap();
    assert_eq!(
        code(l.append(draft(
            "john",
            2,
            "register",
            instance("c2", "claude-opus-5-5", "s1", &["write"])
        ))),
        "E_IDENTITY"
    );
    assert_eq!(
        code(l.append(draft(
            "john",
            2,
            "register",
            instance("c2", "claude-opus-5-5", "s2", &["write"])
        ))),
        "OK"
    );
}

fn episode(content: &str, turns: &[(&str, usize, usize)], bindings: &[(&str, &str)]) -> Value {
    let mut names: Vec<&str> = Vec::new();
    for (n, _, _) in turns {
        if !names.contains(n) {
            names.push(n);
        }
    }
    let mut b = Value::obj(vec![
        ("compartment", Value::str("lab")),
        ("content", Value::str(content)),
        ("medium", Value::str("chat")),
        (
            "participants",
            Value::Array(names.iter().map(|n| Value::str(*n)).collect()),
        ),
        (
            "turns",
            Value::Array(
                turns
                    .iter()
                    .map(|(n, a, z)| {
                        Value::obj(vec![
                            ("speaker", Value::str(*n)),
                            ("start", Value::Int(*a as i64)),
                            ("end", Value::Int(*z as i64)),
                        ])
                    })
                    .collect(),
            ),
        ),
    ]);
    if !bindings.is_empty() {
        b = b.with(
            "bindings",
            Value::Object(
                bindings
                    .iter()
                    .map(|(k, v)| (k.to_string(), Value::str(*v)))
                    .collect(),
            ),
        );
    }
    b
}

fn said(ep: &str, speaker: &str, quote: &str, start: i64, confidence: i64) -> Value {
    claim_body(quote, "asserted", "true")
        .with("speaker", Value::str(speaker))
        .with("origin", Value::str("direct"))
        .with("source_episode", Value::str(ep))
        .with("quote", Value::str(quote))
        .with("quote_start", Value::Int(start))
        .with("confidence", Value::Int(confidence))
}

#[test]
fn names_are_bound_not_matched() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("c1", "claude-opus-5-5", "s1", &["write"]),
    ))
    .unwrap();
    l.append(draft(
        "john",
        2,
        "register",
        instance("c2", "claude-opus-5-5", "s2", &["write"]),
    ))
    .unwrap();
    let text = "John: Use Rust.\nClaude: Rust it is.";
    let turns = [("John", 6, 15), ("Claude", 24, 35)];
    // Bindings name participants and registered writers only.
    assert_eq!(
        code(l.append(draft(
            "c1",
            3,
            "episode",
            episode(text, &turns, &[("Bob", "c1")])
        ))),
        "E_BODY"
    );
    assert_eq!(
        code(l.append(draft(
            "c1",
            3,
            "episode",
            episode(text, &turns, &[("Claude", "zed")])
        ))),
        "E_IDENTITY"
    );
    l.append(draft(
        "c1",
        3,
        "episode",
        episode(text, &turns, &[("Claude", "c1"), ("John", "john")]),
    ))
    .unwrap();
    // c1 quoting its own bound words: its own, at full confidence.
    assert_eq!(
        code(l.append(draft(
            "c1",
            4,
            "claim",
            said("c1/1", "Claude", "Rust it is.", 24, 9_500)
        ))),
        "OK"
    );
    // c2 is another session of the same model: "Claude" there is c1, not
    // c2, and c2's party recorded it, so it is c2's account of c1: capped.
    assert_eq!(
        code(l.append(draft(
            "c2",
            5,
            "claim",
            said("c1/1", "Claude", "Rust it is.", 24, 9_500)
        ))),
        "E_ATTRIBUTION"
    );
    assert_eq!(
        code(l.append(draft(
            "c2",
            5,
            "claim",
            said("c1/1", "Claude", "Rust it is.", 24, 7_000)
        ))),
        "OK"
    );
    // Without a binding, the same name is nobody in particular.
    l.append(draft("c2", 6, "episode", episode(text, &turns, &[])))
        .unwrap();
    assert_eq!(
        code(l.append(draft(
            "c2",
            7,
            "claim",
            said("c2/2", "Claude", "Rust it is.", 24, 9_500)
        ))),
        "E_ATTRIBUTION"
    );
}

#[test]
fn instances_start_instances_of_themselves_only() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance(
            "claude",
            "claude-opus-5-5",
            "base",
            &["write", "verify", "spawn"],
        ),
    ))
    .unwrap();
    let ok = instance("claude-1", "claude-opus-5-5", "s1", &["write"]);
    assert_eq!(
        code(l.append(draft("claude", 2, "register", ok.clone()))),
        "OK"
    );
    let st = l.state.writers.get("claude-1").unwrap();
    assert_eq!(st.instance_of.as_deref(), Some("claude"));
    for (bad, why) in [
        (
            instance("claude-2", "claude-sonnet-5-5", "s2", &["write"]),
            "another model",
        ),
        (
            instance("claude-2", "claude-opus-5-5", "s2", &["write", "spawn"]),
            "spawn",
        ),
        (
            instance("claude-2", "claude-opus-5-5", "s2", &["govern"]),
            "govern",
        ),
        (
            instance("claude-2", "claude-opus-5-5", "s2", &["owner"]),
            "owner",
        ),
        (
            ok.with("writer", Value::str("claude-2"))
                .with("session", Value::str("s2"))
                .with("party", Value::str("openai")),
            "another party",
        ),
        (
            ok.with("writer", Value::str("claude-2"))
                .with("session", Value::str("s2"))
                .with("compartments", Value::Array(vec![Value::str("private")])),
            "a compartment it is not in",
        ),
    ] {
        assert_eq!(
            code(l.append(draft("claude", 3, "register", bad))),
            "E_SPAWN",
            "{why}"
        );
    }
    // An instance without 'spawn' cannot start more.
    let more = instance("claude-3", "claude-opus-5-5", "s3", &["write"]);
    assert_eq!(
        code(l.append(draft("claude-1", 4, "register", more))),
        "E_CAPABILITY"
    );
}

#[test]
fn agreement_counts_once_per_model() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("c1", "claude-opus-5-5", "s1", &["write"]),
    ))
    .unwrap();
    l.append(draft(
        "john",
        2,
        "register",
        instance("c2", "claude-opus-5-5", "s2", &["write"]),
    ))
    .unwrap();
    let gpt = register_plain("g1", "openai", "model", &["write"], &["lab"])
        .with("model", Value::str("gpt-6"))
        .with("operator", Value::str("john"));
    l.append(draft("john", 3, "register", gpt)).unwrap();
    let body = claim_body("The build is green.", "asserted", "true");
    l.append(draft("c1", 4, "claim", body.clone())).unwrap();
    l.append(draft("c2", 5, "claim", body.clone())).unwrap();
    let id = gnomon_core::ledger::claim_id(&body);
    let groups = |l: &Ledger| l.state.claims[&id].groups(&l.state);
    assert_eq!(groups(&l), vec!["model:claude-opus-5-5"]);
    l.append(draft("g1", 6, "claim", body.clone())).unwrap();
    assert_eq!(groups(&l), vec!["model:claude-opus-5-5", "model:gpt-6"]);
}

// ---------------------------------------------------- review M6 findings

/// Finding 1: logs written before M6 replay under their own rules, and
/// move to the new ones only when their owner says so.
#[test]
fn version_1_logs_keep_their_rules_until_changed() {
    let mut l = Ledger::new();
    l.append(draft(
        "john",
        0,
        "register",
        register_plain("john", "john", "human", &["owner"], &["lab"]),
    ))
    .unwrap();
    assert_eq!(l.state.schema_version, 1);
    let old_model = register_plain("c1", "anthropic", "model", &["write"], &["lab"]);
    assert_eq!(
        code(l.append(draft("john", 1, "register", old_model))),
        "OK"
    );
    let old_claim = claim_body("Ship it.", "decided", "true")
        .with("speaker", Value::str("John"))
        .with("origin", Value::str("manual"))
        .with("confidence", Value::Int(9_000));
    assert_eq!(code(l.append(draft("john", 2, "claim", old_claim))), "OK");
    let up = Value::obj(vec![
        ("version", Value::Int(2)),
        ("description", Value::str("M6 identity")),
    ]);
    l.append(draft("john", 3, "schema_change", up)).unwrap();
    let new_model = register_plain("c2", "anthropic", "model", &["write"], &["lab"]);
    assert_eq!(
        code(l.append(draft("john", 4, "register", new_model))),
        "E_BODY"
    );
    let again = claim_body("Ship it now.", "decided", "true")
        .with("speaker", Value::str("John"))
        .with("origin", Value::str("manual"))
        .with("confidence", Value::Int(9_000));
    assert_eq!(
        code(l.append(draft("john", 4, "claim", again))),
        "E_ATTRIBUTION"
    );
    // Only genesis may set the version.
    let sneaky = register_body("bo", "bo", "human", &["write"], &["lab"])
        .with("schema_version", Value::Int(1));
    assert_eq!(
        code(l.append(draft("john", 5, "register", sneaky))),
        "E_BODY"
    );
}

/// Findings 2 and 5: who the speaker is is settled when a claim is first
/// recorded; a name cannot be taken by another party, and repeating a claim
/// under a shared name does not make it the repeater's own words.
#[test]
fn the_speaker_is_settled_once() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("c1", "claude-opus-5-5", "s1", &["write"]),
    ))
    .unwrap();
    l.append(draft(
        "john",
        2,
        "register",
        instance("c2", "claude-opus-5-5", "s2", &["write"]),
    ))
    .unwrap();
    let mallory = register_plain("mal", "evil", "model", &["write"], &["lab"])
        .with("model", Value::str("mal-1"))
        .with("operator", Value::str("john"))
        .with("name", Value::str("Claude"));
    assert_eq!(
        code(l.append(draft("john", 3, "register", mallory))),
        "E_IDENTITY"
    );
    let body = claim_body("I ran the tests.", "asserted", "true")
        .with("speaker", Value::str("Claude"))
        .with("origin", Value::str("manual"))
        .with("confidence", Value::Int(9_500));
    l.append(draft("c1", 4, "claim", body.clone())).unwrap();
    // c2 shares the name, but the claim's speaker is c1.
    assert_eq!(
        code(l.append(draft("c2", 5, "claim", body.clone()))),
        "E_ATTRIBUTION"
    );
    let id = gnomon_core::ledger::claim_id(&body);
    assert_eq!(l.state.claims[&id].speaker_is.as_deref(), Some("c1"));
    // A writer registered later under a matching id changes nothing.
    let named =
        claim_body("Lunch is at noon.", "asserted", "true").with("speaker", Value::str("Claude"));
    l.append(draft("c1", 6, "claim", named.clone())).unwrap();
    l.append(draft(
        "john",
        7,
        "register",
        register_plain("claude", "x", "human", &["write"], &["lab"]),
    ))
    .unwrap();
    let nid = gnomon_core::ledger::claim_id(&named);
    assert_eq!(
        l.state.speaker_of(&l.state.claims[&nid]).as_deref(),
        Some("c1")
    );
}

/// Findings 3, 4 and 8: an instance takes its parent's name and surface, its
/// id is in its parent's namespace, people have no instances, and every
/// instance is in its parent's group.
#[test]
fn instances_inherit_who_they_are() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("claude", "claude-opus-5-5", "base", &["write", "spawn"]),
    ))
    .unwrap();
    let impostor = instance("claude-x", "claude-opus-5-5", "s1", &["write"])
        .with("name", Value::str("John Doe"));
    assert_eq!(
        code(l.append(draft("claude", 2, "register", impostor))),
        "E_SPAWN"
    );
    let elsewhere = instance("claude-x", "claude-opus-5-5", "s1", &["write"])
        .with("surface", Value::str("api"));
    assert_eq!(
        code(l.append(draft("claude", 2, "register", elsewhere))),
        "E_SPAWN"
    );
    let outside = instance("gpt-x", "claude-opus-5-5", "s1", &["write"]);
    assert_eq!(
        code(l.append(draft("claude", 2, "register", outside))),
        "E_SPAWN"
    );
    let person = register_plain("bo", "bo", "human", &["write", "spawn"], &["lab"]);
    assert_eq!(
        code(l.append(draft("john", 2, "register", person))),
        "E_BODY"
    );
    let tool = register_plain("scraper", "tools", "tool", &["write", "spawn"], &["lab"])
        .with("operator", Value::str("john"));
    l.append(draft("john", 3, "register", tool)).unwrap();
    for (i, w) in ["scraper-1", "scraper-2"].iter().enumerate() {
        let inst = register_plain(w, "tools", "tool", &["write"], &["lab"])
            .with("operator", Value::str("john"));
        l.append(draft("scraper", 4 + i as u32, "register", inst))
            .unwrap();
    }
    assert_eq!(l.state.group("scraper-1"), "tool:scraper");
    assert_eq!(l.state.group("scraper-2"), "tool:scraper");
    let body = claim_body("The page is up.", "asserted", "true");
    l.append(draft("scraper-1", 6, "claim", body.clone()))
        .unwrap();
    l.append(draft("scraper-2", 7, "claim", body.clone()))
        .unwrap();
    let id = gnomon_core::ledger::claim_id(&body);
    assert_eq!(l.state.claims[&id].groups(&l.state), vec!["tool:scraper"]);
}

/// Finding 6: binding someone else's name to yourself is refused.
#[test]
fn you_cannot_bind_another_name_to_yourself() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("c1", "claude-opus-5-5", "s1", &["write"]),
    ))
    .unwrap();
    let text = "John: I approved the budget.";
    let turns = [("John", 6, 28)];
    assert_eq!(
        code(l.append(draft(
            "c1",
            2,
            "episode",
            episode(text, &turns, &[("John", "c1")])
        ))),
        "E_IDENTITY"
    );
    assert_eq!(
        code(l.append(draft(
            "c1",
            2,
            "episode",
            episode(text, &turns, &[("John", "john")])
        ))),
        "OK"
    );
}

/// Finding 8: binding to a writer who is not in the episode's compartment
/// is refused exactly like binding to nobody.
#[test]
fn bindings_reveal_nothing_about_other_compartments() {
    let mut l = base();
    l.append(draft(
        "john",
        1,
        "register",
        instance("c1", "claude-opus-5-5", "s1", &["write"]),
    ))
    .unwrap();
    let hidden = register_plain("priv", "priv", "human", &["write"], &["private"]);
    l.append(draft("john", 2, "register", hidden)).unwrap();
    let text = "Bo: hi";
    let turns = [("Bo", 4, 6)];
    let a = l
        .append(draft(
            "c1",
            3,
            "episode",
            episode(text, &turns, &[("Bo", "priv")]),
        ))
        .unwrap_err();
    let b = l
        .append(draft(
            "c1",
            3,
            "episode",
            episode(text, &turns, &[("Bo", "zzzz")]),
        ))
        .unwrap_err();
    assert_eq!(
        (a.code, b.message.replace("zzzz", "priv")),
        (b.code, a.message)
    );
}
