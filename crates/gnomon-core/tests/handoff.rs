//! M8: continuity. A handoff records where a piece of work stands so that
//! anyone (another session, another model, a person) can pick it up.

mod common;
use common::*;

use gnomon_core::json::Value;
use gnomon_core::ledger::claim_id;

fn handoff(frame: &str, summary: &str) -> Value {
    Value::obj(vec![
        ("compartment", Value::str("lab")),
        ("frame", Value::str(frame)),
        ("summary", Value::str(summary)),
    ])
}

fn strs(xs: &[&str]) -> Value {
    Value::Array(xs.iter().map(|x| Value::str(*x)).collect())
}

#[test]
fn a_handoff_carries_the_work() {
    let mut l = lab();
    let c = claim_body("Use SQLite.", "decided", "true");
    l.append(draft("claude-c1", 10, "claim", c.clone())).unwrap();
    let h = handoff("db", "Picked SQLite; the schema is next.")
        .with("goal", Value::str("A working prototype"))
        .with("done", strs(&["picked the database"]))
        .with("next", strs(&["write the schema"]))
        .with("open", strs(&["backups?"]))
        .with("refs", strs(&[&claim_id(&c)]))
        .with(
            "artifacts",
            Value::Array(vec![Value::obj(vec![
                ("uri", Value::str("https://github.com/5kasgard/gnomon/commit/abc")),
                ("note", Value::str("the commit")),
            ])]),
        );
    assert_eq!(code(l.append(draft("claude-c1", 11, "handoff", h))), "OK");
    // Another model continues it.
    let next = handoff("db", "Schema written.").with("follows", Value::str("claude-c1/2"));
    assert_eq!(code(l.append(draft("gpt-g1", 12, "handoff", next))), "OK");
    assert_eq!(l.state.handoffs.len(), 2);
}

#[test]
fn handoffs_are_checked() {
    let mut l = lab();
    let secret = claim_body("The supplier is Acme.", "decided", "true").with("compartment", Value::str("private"));
    l.append(draft("alex", 10, "claim", secret.clone())).unwrap();
    l.append(draft("claude-c1", 11, "handoff", handoff("db", "Start."))).unwrap();
    let hidden = l
        .append(draft("gpt-g1", 12, "handoff", handoff("db", "x").with("refs", strs(&[&claim_id(&secret)]))))
        .unwrap_err();
    let missing = l
        .append(draft("gpt-g1", 12, "handoff", handoff("db", "x").with("refs", strs(&["c:nothing"]))))
        .unwrap_err();
    assert_eq!(hidden.code, missing.code);
    for (body, want) in [
        (handoff("db", "x").with("follows", Value::str("nobody/1")), "E_REF"),
        (handoff("other", "x").with("follows", Value::str("claude-c1/1")), "E_REFERENCE"),
        (handoff("db", "x").with("next", strs(&[""])), "E_BODY"),
        (handoff("db", "x").with("next", strs(&["sneaky\u{200b}"])), "E_HIDDEN_CHARS"),
        (handoff("db", "x").with("refs", strs(&["claude-c1/1", "claude-c1/1"])), "E_BODY"),
        (handoff("db", "x").with("colour", Value::str("red")), "E_BODY"),
        (handoff("db", "").with("next", strs(&["a"])), "E_BODY"),
        (handoff("db", "x").with("compartment", Value::str("private")), "E_COMPARTMENT"),
        (
            handoff("db", "x").with(
                "artifacts",
                Value::Array(vec![Value::obj(vec![("uri", Value::str("a b"))])]),
            ),
            "E_BODY",
        ),
    ] {
        assert_eq!(code(l.append(draft("gpt-g1", 12, "handoff", body.clone()))), want, "{body:?}");
    }
    let many: Vec<String> = (0..65).map(|i| format!("step {i}")).collect();
    let refs: Vec<&str> = many.iter().map(|s| s.as_str()).collect();
    assert_eq!(code(l.append(draft("gpt-g1", 12, "handoff", handoff("db", "x").with("next", strs(&refs))))), "E_BODY");
}
