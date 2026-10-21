//! Organisations: positions, appointments and dismissals, with authority
//! checked by the gate. Owners and governors may do anything; anyone else
//! acts only on positions below one they hold. No one appoints themselves;
//! anyone may resign.

mod common;
use common::*;

use gnomon_core::json::Value;
use gnomon_core::ledger::Ledger;
use gnomon_core::datalog::Limits;
use gnomon_core::query;

fn bosses(l: &Ledger, of: &str) -> Vec<String> {
    let v = query::run(l, &["lab".to_string()], &format!("?- oversees(B, \"{of}\")."), Limits::default()).unwrap();
    let mut out: Vec<String> = v
        .get("answers")
        .unwrap()
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a.get("values").unwrap().as_array().unwrap()[0].as_str().unwrap().to_string())
        .collect();
    out.sort();
    out
}

fn pos(title: &str, up: Option<&str>) -> Value {
    let mut v = Value::obj(vec![
        ("compartment", Value::str("lab")),
        ("org", Value::str("acme")),
        ("title", Value::str(title)),
        ("duties", Value::str("Does the work of the position.")),
    ]);
    if let Some(u) = up {
        v = v.with("reports_to", Value::str(u));
    }
    v
}

fn appoint(title: &str, who: &str) -> Value {
    Value::obj(vec![
        ("compartment", Value::str("lab")),
        ("org", Value::str("acme")),
        ("title", Value::str(title)),
        ("holder", Value::str(who)),
    ])
}

fn dismiss(a: &str) -> Value {
    Value::obj(vec![("appointment", Value::str(a)), ("reason", Value::str("Performance below the bar."))])
}

fn eid(v: &Value) -> String {
    v.get("event_id").unwrap().as_str().unwrap().to_string()
}

/// john founds acme: CEO (claude-c1) over Lead (gpt-g1) over Engineer.
fn acme() -> (Ledger, String, String) {
    let mut l = lab();
    l.append(draft("john", 10, "position", pos("CEO", None))).unwrap();
    let ceo = eid(&l.append(draft("john", 11, "appoint", appoint("CEO", "claude-c1"))).unwrap());
    l.append(draft("claude-c1", 12, "position", pos("Lead", Some("CEO")))).unwrap();
    let lead = eid(&l.append(draft("claude-c1", 13, "appoint", appoint("Lead", "gpt-g1"))).unwrap());
    l.append(draft("gpt-g1", 14, "position", pos("Engineer", Some("Lead")))).unwrap();
    (l, ceo, lead)
}

#[test]
fn authority_flows_down_the_organisation() {
    let (mut l, _, _) = acme();
    // The Lead hires engineers; an engineer hires no one.
    assert_eq!(code(l.append(draft("gpt-g1", 20, "appoint", appoint("Engineer", "kimi-k1")))), "OK");
    assert_eq!(code(l.append(draft("kimi-k1", 21, "appoint", appoint("Engineer", "claude-c2")))), "E_ORG");
    // Nor can anyone define positions above themselves or beside their own.
    assert_eq!(code(l.append(draft("kimi-k1", 22, "position", pos("Intern", Some("Lead"))))), "E_ORG");
    assert_eq!(code(l.append(draft("gpt-g1", 23, "position", pos("Deputy", Some("CEO"))))), "E_ORG");
    // The CEO may act anywhere below.
    assert_eq!(code(l.append(draft("claude-c1", 24, "appoint", appoint("Engineer", "claude-c2")))), "OK");
    // A Lead cannot redefine the CEO's position.
    assert_eq!(code(l.append(draft("gpt-g1", 25, "position", pos("CEO", None)))), "E_ORG");
}

#[test]
fn no_one_appoints_themselves_and_no_cycles() {
    let (mut l, _, _) = acme();
    assert_eq!(code(l.append(draft("gpt-g1", 20, "appoint", appoint("Engineer", "gpt-g1")))), "E_ORG");
    // The owner may (it is the owner's organisation).
    assert_eq!(code(l.append(draft("john", 21, "appoint", appoint("Engineer", "john")))), "OK");
    // CEO -> Lead -> Engineer: making the CEO report to the Engineer is a cycle.
    assert_eq!(code(l.append(draft("john", 22, "position", pos("CEO", Some("Engineer"))))), "E_ORG");
    assert_eq!(code(l.append(draft("john", 23, "position", pos("CEO", Some("CEO"))))), "E_BODY");
    // Reporting to a position that does not exist.
    assert_eq!(code(l.append(draft("john", 24, "position", pos("Ghost", Some("Nobody"))))), "E_REF");
    // Holding a position twice at once.
    assert_eq!(code(l.append(draft("claude-c1", 25, "appoint", appoint("Lead", "gpt-g1")))), "E_DUPLICATE");
}

#[test]
fn dismissal_resignation_and_what_follows() {
    let (mut l, _, lead) = acme();
    let eng = eid(&l.append(draft("gpt-g1", 20, "appoint", appoint("Engineer", "kimi-k1"))).unwrap());
    // A peer cannot dismiss; the one above can; the holder can resign.
    l.append(draft("john", 21, "appoint", appoint("Engineer", "claude-c2"))).unwrap();
    assert_eq!(code(l.append(draft("claude-c2", 22, "dismiss", dismiss(&eng)))), "E_ORG");
    assert_eq!(code(l.append(draft("kimi-k1", 23, "dismiss", dismiss(&eng)))), "OK");
    assert_eq!(code(l.append(draft("gpt-g1", 24, "dismiss", dismiss(&eng)))), "E_ORG");
    // The CEO dismisses the Lead, who then has no authority left.
    assert_eq!(code(l.append(draft("claude-c1", 25, "dismiss", dismiss(&lead)))), "OK");
    assert_eq!(code(l.append(draft("gpt-g1", 26, "appoint", appoint("Engineer", "kimi-k1")))), "E_ORG");
    // A new Lead, appointed by the CEO.
    assert_eq!(code(l.append(draft("claude-c1", 27, "appoint", appoint("Lead", "claude-c2")))), "OK");
    assert_eq!(code(l.append(draft("claude-c2", 28, "appoint", appoint("Engineer", "kimi-k1")))), "OK");
}

#[test]
fn organisations_are_compartmented() {
    let (mut l, _, _) = acme();
    l.append(draft("john", 20, "register", register_body("out", "outco", "model", &["write"], &["private"])))
        .unwrap();
    // Someone outside the compartment cannot see or use it.
    let mut p = pos("Spy", Some("CEO"));
    if let Value::Object(o) = &mut p {
        o.retain(|(k, _)| k != "compartment");
        o.push(("compartment".into(), Value::str("private")));
    }
    assert_eq!(code(l.append(draft("john", 21, "position", p))), "E_REF", "one organisation, one compartment");
    // A holder must be a writer in the organisation's compartment.
    assert_eq!(code(l.append(draft("john", 22, "appoint", appoint("Engineer", "out")))), "E_REF");
    let v = l.state.to_value(Some(&["private".to_string()]));
    assert_eq!(v.get("positions").unwrap().as_object().unwrap().len(), 0);
    assert_eq!(v.get("appointments").unwrap().as_object().unwrap().len(), 0);
}

#[test]
fn queries_see_who_holds_what_and_who_oversees_whom() {
    let (mut l, _, lead) = acme();
    l.append(draft("gpt-g1", 20, "appoint", appoint("Engineer", "kimi-k1"))).unwrap();
    assert_eq!(bosses(&l, "kimi-k1"), vec!["claude-c1".to_string(), "gpt-g1".to_string()]);
    l.append(draft("claude-c1", 21, "dismiss", dismiss(&lead))).unwrap();
    assert_eq!(bosses(&l, "kimi-k1"), vec!["claude-c1".to_string()]);
}
