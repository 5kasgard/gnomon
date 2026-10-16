//! M7: the record as a forum. Claims can answer questions, critique, reply
//! to and cite earlier claims; none of that changes anyone's truth.

mod common;
use common::*;

use gnomon_core::json::Value;
use gnomon_core::ledger::claim_id;

fn about(refs: &[(&str, &str)]) -> Value {
    Value::Array(
        refs.iter()
            .map(|(c, r)| Value::obj(vec![("claim", Value::str(*c)), ("relation", Value::str(*r))]))
            .collect(),
    )
}

fn question(text: &str) -> Value {
    claim_body(text, "asked", "unknown").with("statement_kind", Value::str("ask"))
}

#[test]
fn answers_critiques_and_replies() {
    let mut l = lab();
    let q = question("Which database should the prototype use?");
    l.append(draft("john", 10, "claim", q.clone())).unwrap();
    let qid = claim_id(&q);
    let a = claim_body("SQLite: one file, no server.", "suggested", "unknown").with("about", about(&[(&qid, "answers")]));
    assert_eq!(code(l.append(draft("claude-c1", 11, "claim", a.clone()))), "OK");
    let aid = claim_id(&a);
    // Only a question can be answered.
    let wrong = claim_body("Postgres.", "suggested", "unknown").with("about", about(&[(&aid, "answers")]));
    assert_eq!(code(l.append(draft("gpt-g1", 12, "claim", wrong))), "E_REFERENCE");
    // A critique is a claim of its own: the critiqued claim keeps its truth.
    let k = claim_body("SQLite locks the whole file on write.", "asserted", "true")
        .with("about", about(&[(&aid, "critiques")]));
    l.append(draft("gpt-g1", 12, "claim", k.clone())).unwrap();
    assert_eq!(l.state.claims[&aid].asserted_truth, "unknown");
    let r = claim_body("WAL mode allows concurrent readers.", "asserted", "true")
        .with("about", about(&[(&claim_id(&k), "replies")]));
    assert_eq!(code(l.append(draft("claude-c1", 13, "claim", r))), "OK");
}

#[test]
fn references_are_checked() {
    let mut l = lab();
    let base = claim_body("The build is green.", "asserted", "true");
    l.append(draft("claude-c1", 10, "claim", base.clone())).unwrap();
    let id = claim_id(&base);
    let nowhere = format!("c:{}", "0".repeat(64));
    for (refs, want) in [
        (about(&[(&nowhere, "cites")]), "E_REF"),
        (about(&[(&id, "endorses")]), "E_BODY"),
        (about(&[(&id, "cites"), (&id, "cites")]), "E_BODY"),
        (about(&[("c:short", "cites")]), "E_BODY"),
        (Value::Array(vec![]), "E_BODY"),
    ] {
        let c = claim_body("See above.", "asserted", "true").with("about", refs);
        assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", c))), want);
    }
    // A question cannot be an answer.
    let q = question("Why?").with("about", about(&[(&id, "answers")]));
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", q))), "E_BODY");
}

#[test]
fn threads_stay_in_their_compartment() {
    let mut l = lab();
    let secret = claim_body("The supplier is Acme.", "decided", "true").with("compartment", Value::str("private"));
    l.append(draft("john", 10, "claim", secret.clone())).unwrap();
    let sid = claim_id(&secret);
    // gpt cannot see it: refused exactly as if it did not exist.
    let probe = claim_body("Interesting.", "asserted", "true").with("about", about(&[(&sid, "replies")]));
    let missing = claim_body("Interesting.", "asserted", "true")
        .with("about", about(&[(&format!("c:{}", "1".repeat(64)), "replies")]));
    let a = l.append(draft("gpt-g1", 11, "claim", probe)).unwrap_err();
    let b = l.append(draft("gpt-g1", 11, "claim", missing)).unwrap_err();
    assert_eq!(a.code, b.code);
    // john sees both compartments, but a thread does not cross them.
    let cross = claim_body("Noted.", "asserted", "true").with("about", about(&[(&sid, "replies")]));
    assert_eq!(code(l.append(draft("john", 12, "claim", cross))), "E_REF");
}
