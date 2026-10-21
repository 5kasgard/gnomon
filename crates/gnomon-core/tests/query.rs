//! M3: queries. The engine is exact (same answers whatever the rule order),
//! always terminates within hard limits, refuses programs without a single
//! meaning, and proves every answer down to log events the reader may see.

mod common;
use common::*;

use gnomon_core::datalog::{run_program, Limits};
use gnomon_core::json::Value;
use gnomon_core::ledger::Ledger;
use gnomon_core::query;

fn run(src: &str) -> Value {
    run_program(src, Limits::default()).unwrap_or_else(|e| panic!("{}: {}", e.code, e.message))
}

fn err(src: &str) -> (String, String) {
    match run_program(src, Limits::default()) {
        Ok(v) => panic!("expected an error, got {v:?}"),
        Err(e) => (e.code.to_string(), e.message),
    }
}

/// Answers as rows of strings/ints rendered as text.
fn rows(v: &Value) -> Vec<Vec<String>> {
    v.get("answers")
        .unwrap()
        .as_array()
        .unwrap()
        .iter()
        .map(|a| {
            a.get("values")
                .unwrap()
                .as_array()
                .unwrap()
                .iter()
                .map(|x| match x {
                    Value::Int(i) => i.to_string(),
                    _ => x.as_str().unwrap().to_string(),
                })
                .collect()
        })
        .collect()
}

/// The text of the rule that derived node `id`.
fn rule_of(v: &Value, id: &str) -> String {
    let rid = v
        .get("nodes")
        .unwrap()
        .get(id)
        .unwrap()
        .get("rule")
        .unwrap()
        .as_str()
        .unwrap();
    v.get("rules")
        .unwrap()
        .get(rid)
        .unwrap()
        .get("text")
        .unwrap()
        .as_str()
        .unwrap()
        .to_string()
}

fn r(items: &[&[&str]]) -> Vec<Vec<String>> {
    items
        .iter()
        .map(|row| row.iter().map(|s| s.to_string()).collect())
        .collect()
}

const GRAPH: &str = r#"
edge("a", "b"). edge("b", "c"). edge("c", "d"). edge("x", "y").
path(X, Y) :- edge(X, Y).
path(X, Z) :- path(X, Y), edge(Y, Z).
"#;

#[test]
fn recursion_reaches_the_fixpoint() {
    let v = run(&format!("{GRAPH} ?- path(\"a\", Y)."));
    assert_eq!(rows(&v), r(&[&["b"], &["c"], &["d"]]));
}

#[test]
fn rule_order_never_changes_the_answers() {
    let a = run(&format!("{GRAPH} ?- path(X, Y)."));
    let reordered = r#"
        path(X, Z) :- path(X, Y), edge(Y, Z).
        edge("x", "y"). edge("c", "d").
        path(X, Y) :- edge(X, Y).
        edge("b", "c"). edge("a", "b").
        ?- path(X, Y)."#;
    assert_eq!(rows(&a), rows(&run(reordered)));
    assert_eq!(rows(&a).len(), 7);
}

#[test]
fn every_answer_has_a_proof_ending_in_facts() {
    let v = run(&format!("{GRAPH} ?- path(\"a\", \"d\")."));
    let nodes = v.get("nodes").unwrap();
    let ans = &v.get("answers").unwrap().as_array().unwrap()[0];
    let mut todo = vec![ans.get("proof").unwrap().as_str().unwrap().to_string()];
    let mut leaves = 0;
    while let Some(id) = todo.pop() {
        let n = nodes.get(&id).unwrap();
        match n.get("premises") {
            Some(ps) => {
                assert!(rule_of(&v, &id).ends_with('.'));
                for p in ps.as_array().unwrap() {
                    todo.push(p.as_str().unwrap().to_string());
                }
            }
            None => leaves += 1,
        }
    }
    // path(a,d) <- path(a,c), edge(c,d) <- path(a,b), edge(b,c) <- edge(a,b)
    assert_eq!(
        leaves, 0,
        "facts written in the program are derived by their own clause"
    );
    assert_eq!(rule_of(&v, "n0"), "edge(\"a\", \"b\").");
}

#[test]
fn stratified_negation() {
    let v = run(&format!(
        "{GRAPH} node(X) :- edge(X, _). node(Y) :- edge(_, Y).
         sink(X) :- node(X), not edge(X, _).
         ?- sink(X)."
    ));
    assert_eq!(rows(&v), r(&[&["d"], &["y"]]));
    // The proof records what was checked absent.
    let nodes = v.get("nodes").unwrap().as_object().unwrap();
    let sink = nodes
        .iter()
        .find(|(_, n)| n.get("fact").unwrap().as_array().unwrap()[0].as_str() == Some("sink"))
        .unwrap();
    let absent = sink.1.get("absent").unwrap().as_array().unwrap();
    assert_eq!(absent.len(), 1);
    assert_eq!(
        absent[0].as_array().unwrap()[2],
        Value::Null,
        "_ is shown as null"
    );
}

#[test]
fn negation_through_recursion_is_refused() {
    let (code, msg) = err("p(\"a\"). q(X) :- p(X), not r(X). r(X) :- p(X), not q(X). ?- q(X).");
    assert_eq!(code, "E_QUERY");
    assert!(msg.contains("own negation"), "{msg}");
    let (code, _) = err("p(\"a\"). q(X) :- p(X), not q(X). ?- q(X).");
    assert_eq!(code, "E_QUERY");
}

#[test]
fn unsafe_rules_are_refused_with_a_reason() {
    for (src, why) in [
        ("p(X). ?- p(X).", "never appears"),
        ("e(1). p(X) :- e(Y). ?- p(X).", "never appears"),
        (
            "e(1). p(X) :- e(X), not f(Y). f(1). ?- p(X).",
            "must first appear",
        ),
        ("e(1). p(X) :- X > 1, e(X). ?- p(X).", "compared before"),
        ("e(1). p(_) :- e(_). ?- p(X).", "head"),
        (
            "e(\"a\"). p(X) :- e(X), contains(X, Y). ?- p(X).",
            "contains",
        ),
        (
            "e(1). p(X) :- e(X), _ = X. ?- p(X).",
            "_ cannot be compared",
        ),
    ] {
        let (code, msg) = err(src);
        assert_eq!(code, "E_QUERY", "{src}");
        assert!(msg.contains(why), "{src}: {msg}");
    }
}

#[test]
fn typos_are_errors_not_empty_answers() {
    let (_, msg) = err("edge(1, 2). ?- egde(X, Y).");
    assert!(msg.contains("unknown relation 'egde/2'"), "{msg}");
    let (_, msg) = err("edge(1, 2). ?- edge(X).");
    assert!(msg.contains("takes 2 arguments"), "{msg}");
    let (_, msg) = err("edge(1, 2). edge(1). ?- edge(X, Y).");
    assert!(msg.contains("used with 2 and 1"), "{msg}");
    let (_, msg) = err("edge(1, 2).");
    assert!(msg.contains("needs a query"), "{msg}");
    let (_, msg) = err("edge(1, 2). ?- edge(X, Y). ?- edge(Y, X).");
    assert!(msg.contains("exactly one query"), "{msg}");
    let (_, msg) = err("not(1). ?- not(X).");
    assert!(msg.contains("reserved"), "{msg}");
    for bad in [
        "p(\"a). ?- p(X).",
        "p(1) ?- p(X).",
        "p(1). ?- p(X)",
        "p(1). ?- p(X) & q.",
        "p(9999999999999999999). ?- p(X).",
    ] {
        assert_eq!(err(bad).0, "E_QUERY", "{bad}");
    }
}

#[test]
fn comparisons_never_mix_types() {
    let v = run(r#"v(1). v(2). v("2"). v("b"). v("a").
        lt(X, Y) :- v(X), v(Y), X < Y.
        ?- lt(X, Y)."#);
    let all = rows(&v);
    assert!(all.contains(&vec!["1".into(), "2".into()]));
    assert!(all.contains(&vec!["2".into(), "b".into()])); // "2" < "b" as strings
    assert!(all.contains(&vec!["a".into(), "b".into()]));
    assert_eq!(all.len(), 4); // also "2" < "a"
    let v = run(r#"v(1). v("1"). same(X, Y) :- v(X), v(Y), X = Y. ?- same(X, Y)."#);
    assert_eq!(rows(&v).len(), 2, "1 and \"1\" are different values");
}

#[test]
fn contains_folds_ascii_case_only() {
    // Review M3, finding 8: Unicode case tables differ between Rust and
    // Python versions, so only ASCII letters fold; the answer is the same
    // on every platform.
    let v = run(r#"s("CAFÉ au lait"). s("café"). s("\u{10d50}").
        a(S) :- s(S), contains(S, "café").
        b(S) :- s(S), contains(S, "\u{10d70}").
        ?- a(S)."#);
    assert_eq!(rows(&v), r(&[&["café"]]));
    let v = run(r#"s("\u{10d50}"). b(S) :- s(S), contains(S, "\u{10d70}"). ?- b(S)."#);
    assert!(rows(&v).is_empty());
}

#[test]
fn equality_binds_and_contains_ignores_case() {
    let v = run(r#"s("We will use RUST for the core"). s("Python on top").
        hit(S) :- s(S), contains(S, "rust").
        tag(S, T) :- hit(S), T = "lang".
        ?- tag(S, T)."#);
    assert_eq!(rows(&v), r(&[&["We will use RUST for the core", "lang"]]));
}

#[test]
fn propositions_and_yes_no_queries() {
    let v = run("ready. go :- ready. ?- go.");
    assert_eq!(v.get("answers").unwrap().as_array().unwrap().len(), 1);
    assert_eq!(v.get("variables").unwrap().as_array().unwrap().len(), 0);
    let v = run("ready. stop :- ready, not ready. ?- stop.");
    assert_eq!(v.get("answers").unwrap().as_array().unwrap().len(), 0);
}

#[test]
fn rule_text_in_proofs_round_trips_strings() {
    let v = run(r#"q("say \"hi\"\n\\"). ?- q(X)."#);
    assert_eq!(rows(&v), r(&[&["say \"hi\"\n\\"]]));
    let t = rule_of(&v, "n0");
    assert_eq!(t, r#"q("say \"hi\"\n\\")."#);
    // The printed rule parses back to the same fact.
    assert_eq!(rows(&run(&format!("{t} ?- q(X)."))), rows(&v));
}

#[test]
fn control_characters_print_and_parse_back() {
    let v = run("q(\"a\\u{1}b\\u{7f}\"). ?- q(X).");
    assert_eq!(rows(&v), r(&[&["a\u{1}b\u{7f}"]]));
    let t = rule_of(&v, "n0");
    assert_eq!(rows(&run(&format!("{t} ?- q(X)."))), rows(&v));
    for bad in [
        "q(\"\\u{110000}\"). ?- q(X).",
        "q(\"\\u{d800}\"). ?- q(X).",
        "q(\"\\u12\"). ?- q(X).",
    ] {
        assert_eq!(err(bad).0, "E_QUERY", "{bad}");
    }
}

#[test]
fn nothing_can_exhaust_the_machine() {
    let lim = Limits {
        max_facts: 10_000,
        max_steps: 1_000_000,
        ..Limits::default()
    };
    // A cross product that would need 10^8 facts.
    let mut src = String::new();
    for i in 0..100 {
        src.push_str(&format!("n({i}). "));
    }
    src.push_str("p(A, B, C, D) :- n(A), n(B), n(C), n(D). ?- p(A, B, C, D).");
    let e = run_program(&src, lim).unwrap_err();
    assert_eq!(e.code, "E_QUERY_LIMIT");
    // Work that derives little but joins a lot.
    let src2 = format!(
        "{} q :- n(A), n(B), n(C), A = 1000. ?- q.",
        src.split("p(A").next().unwrap()
    );
    assert_eq!(run_program(&src2, lim).unwrap_err().code, "E_QUERY_LIMIT");
    let huge = "p(1). ".repeat(30_000) + "?- p(X).";
    assert_eq!(
        run_program(&huge, Limits::default()).unwrap_err().code,
        "E_QUERY_LIMIT"
    );
}

#[test]
fn repeated_not_cannot_overflow_the_stack() {
    // Review M3, finding 1: 'not ' x 24990 recursed once per 'not' and
    // crashed the process (stack overflow cannot be caught).
    let src = format!("p(1). ?- {}p(1).", "not ".repeat(24_990));
    let (code, msg) = err(&src);
    assert_eq!(code, "E_QUERY");
    assert!(msg.contains("cannot be repeated"), "{msg}");
    // On a small stack too.
    let h = std::thread::Builder::new()
        .stack_size(256 << 10)
        .spawn(move || {
            run_program(&src, Limits::default())
                .map(|_| ())
                .unwrap_err()
                .code
        })
        .unwrap();
    assert_eq!(h.join().unwrap(), "E_QUERY");
}

fn timed<T>(f: impl FnOnce() -> T) -> (T, f64) {
    let start = std::time::Instant::now();
    let r = f();
    (r, start.elapsed().as_secs_f64())
}

#[test]
fn narrow_deltas_over_big_buckets_stay_fast() {
    // Review M3, finding 2: each round scanned a whole index bucket to find
    // the few new rows (56 s for this 7 KB program).
    let d = 600;
    let mut src: String = (0..d - 1).map(|i| format!("s({i},{}). ", i + 1)).collect();
    src.push_str(&format!(
        "c(\"t\",0,0). c(\"t\",A,B2) :- c(\"t\",A,B), s(B,B2). \
         c(\"t\",A2,0) :- c(\"t\",A,{}), s(A,A2). x(B) :- c(\"t\",_,B). ?- x(B), B < 0.",
        d - 1
    ));
    let (r, secs) = timed(|| run_program(&src, Limits::default()));
    assert!(r.is_ok() || r.as_ref().unwrap_err().code == "E_QUERY_LIMIT");
    assert!(secs < 10.0, "{secs} s");
}

#[test]
fn indexes_count_against_memory() {
    // Review M3, finding 3: every new bound-column pattern built a full
    // index, uncounted: 4.7 GB from a 1 KB program.
    let mut src: String = (0..30).map(|i| format!("d({i}). ")).collect();
    src.push_str("r(A,B,C,D,A,B,C,D,A,B,C,D,A,B,C,D) :- d(A), d(B), d(C), d(D).\n");
    for k in 0..20 {
        let args: Vec<String> = (0..16)
            .map(|i| {
                if (k >> (i % 5)) & 1 == 1 || i == k % 16 {
                    "0".into()
                } else {
                    "_".into()
                }
            })
            .collect();
        src.push_str(&format!("z :- r({}).\n", args.join(",")));
    }
    src.push_str("?- z.");
    let (r, secs) = timed(|| run_program(&src, Limits::default()));
    assert_eq!(r.unwrap_err().code, "E_QUERY_LIMIT");
    assert!(secs < 10.0, "{secs} s");
}

#[test]
fn long_rules_are_printed_once_and_output_is_bounded() {
    // Review M3, finding 4: the 95 KB query text was copied into each of
    // 10,000 answers' proofs (3.7 GB).
    let long = "x".repeat(95_000);
    let src = format!("d(0). d(1). d(2). d(3). d(4). d(5). d(6). d(7). d(8). d(9). ?- d(A), d(B), d(C), d(D), A != \"{long}\".");
    let (r, secs) = timed(|| run_program(&src, Limits::default()));
    let v = r.unwrap();
    assert_eq!(v.get("rules").unwrap().as_object().unwrap().len(), 11);
    assert!(gnomon_core::json::canonical(&v).len() < 6_000_000);
    assert!(secs < 10.0, "{secs} s");
    // Proofs that would still be huge are refused, not truncated silently.
    let big = "y".repeat(90_000);
    let src = format!(
        "n(0). n(1). n(2). n(3). n(4). n(5). n(6). n(7). n(8). n(9). \
        w(A, B, C, D, S) :- n(A), n(B), n(C), n(D), S = \"{big}\". ?- w(A, B, C, D, S)."
    );
    assert_eq!(
        run_program(&src, Limits::default()).unwrap_err().code,
        "E_QUERY_LIMIT"
    );
}

#[test]
fn contains_on_long_strings_is_charged() {
    // Review M3, finding 5: lowercasing 47,000 'Ä's again for every check
    // took 29 s before the limit hit.
    let src = format!(
        "d(0). d(1). d(2). d(3). d(4). d(5). d(6). d(7). d(8). d(9). s(\"{}\"). \
        ?- s(X), d(A), d(B), d(C), d(D), d(E), contains(X, \"zz\").",
        "Ä".repeat(47_000)
    );
    let (r, secs) = timed(|| run_program(&src, Limits::default()));
    assert_eq!(r.unwrap_err().code, "E_QUERY_LIMIT");
    assert!(secs < 10.0, "{secs} s");
}

#[test]
fn many_rules_cost_only_what_changes() {
    // Review M3 round 2, finding 1: every round revisited every rule
    // (75 s for a 9,000-rule chain; 6,000 here to fit 100 KB; 95 s with 5,000 inert rules).
    let names: Vec<String> = (0..6001).map(|i| format!("p{i}")).collect();
    let mut src: String = (0..6000)
        .map(|i| format!("{}:-{}.", names[i], names[i + 1]))
        .collect();
    src.push_str(&format!("{}. ?- p0.", names[6000]));
    let (r, secs) = timed(|| run_program(&src, Limits::default()));
    assert_eq!(
        r.unwrap().get("answers").unwrap().as_array().unwrap().len(),
        1
    );
    assert!(secs < 2.0, "{secs} s");
    let mut src: String = (0..300).map(|i| format!("n({i}). ")).collect();
    src.push_str("pr(0, 0). pr(A, B2) :- pr(A, B), n(B2), B2 = B. ");
    for i in 0..5000 {
        src.push_str(&format!("z{i}:-n(-1)."));
    }
    src.push_str("?- pr(X, Y).");
    let (r, secs) = timed(|| run_program(&src, Limits::default()));
    assert!(r.is_ok());
    assert!(secs < 2.0, "{secs} s");
}

#[test]
fn escapes_count_toward_the_output_budget() {
    // Review M3 round 2, finding 3: control characters are 6 bytes each in
    // JSON, but were charged as 1 (1.1 GB peak under a 64 MB budget).
    let src = format!(
        "d(0). d(1). d(2). d(3). d(4). d(5). d(6). d(7). d(8). d(9). s(\"{}\"). \
        ?- s(X), d(A), d(B), d(C), d(D).",
        "\\u{1}".repeat(3000)
    );
    assert_eq!(
        run_program(&src, Limits::default()).unwrap_err().code,
        "E_QUERY_LIMIT"
    );
}

#[test]
fn results_say_where_the_readers_view_ends() {
    let (l, ..) = memory();
    let v = ask(&l, &lab_only(), "?- claim(C).");
    let through = v.get("through").unwrap();
    // The last event is john's private claim: lab readers' view ends before it.
    let lab_last = eid(&l.events[l.events.len() - 2]);
    assert_eq!(
        through.get("event").unwrap().as_str(),
        Some(lab_last.as_str())
    );
    let v = ask(
        &l,
        &["lab".to_string(), "private".to_string()],
        "?- claim(C).",
    );
    assert_eq!(
        v.get("through").unwrap().get("event").unwrap().as_str(),
        Some(eid(l.events.last().unwrap()).as_str())
    );
}

#[test]
fn long_chains_prove_without_overflowing() {
    let mut src = String::new();
    for i in 0..3000 {
        src.push_str(&format!("e({i}, {}). ", i + 1));
    }
    src.push_str("reach(X) :- e(0, X). reach(Y) :- reach(X), e(X, Y). ?- reach(3000).");
    let v = run(&src);
    assert_eq!(v.get("answers").unwrap().as_array().unwrap().len(), 1);
}

// ------------------------------------------------------------ the memory

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

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

fn lab_only() -> Vec<String> {
    vec!["lab".to_string()]
}

fn ask(l: &Ledger, comps: &[String], src: &str) -> Value {
    query::run(l, comps, src, Limits::default())
        .unwrap_or_else(|e| panic!("{}: {}", e.code, e.message))
}

/// Decided Rust (claude), suggested Go (gpt); T refuted by kimi and
/// replaced by K; a private claim only john can see.
fn memory() -> (Ledger, String, String, String, String) {
    let mut l = lab();
    let rust = add_claim(
        &mut l,
        "claude-c1",
        10,
        claim_body("Core in Rust", "decided", "true"),
    );
    let go = add_claim(
        &mut l,
        "gpt-g1",
        11,
        claim_body("Maybe Go", "suggested", "unknown"),
    );
    let t = add_claim(&mut l, "claude-c1", 12, claim_body("T", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 13, claim_body("K", "asserted", "true"));
    let v = l
        .append(draft("kimi-k1", 14, "verdict", verdict(&t, "refutes")))
        .unwrap();
    l.append(draft(
        "gpt-g1",
        15,
        "supersede",
        sup(&t, &k, "refuted", vec![&eid(&v)]),
    ))
    .unwrap();
    let mut secret = claim_body("Secret supplier", "decided", "true");
    secret = secret.with("compartment", Value::str("private"));
    add_claim(&mut l, "john", 16, secret);
    (l, rust, go, t, k)
}

#[test]
fn views_answer_with_proofs_to_real_events() {
    let (l, rust, _, t, _) = memory();
    let v = ask(&l, &lab_only(), "?- decision(C), statement(C, S).");
    assert_eq!(
        rows(&v),
        vec![vec![rust.clone(), "Core in Rust".to_string()]]
    );

    // Every leaf cites real events, by id and hash, with nothing else.
    let hashes: Vec<(String, String)> = l
        .events
        .iter()
        .map(|e| (eid(e), e.get("hash").unwrap().as_str().unwrap().to_string()))
        .collect();
    for (_, n) in v.get("nodes").unwrap().as_object().unwrap() {
        if let Some(srcs) = n.get("sources") {
            for s in srcs.as_array().unwrap() {
                let pair = (
                    s.get("event").unwrap().as_str().unwrap().to_string(),
                    s.get("hash").unwrap().as_str().unwrap().to_string(),
                );
                assert!(hashes.contains(&pair), "{pair:?}");
                assert!(
                    s.get("seq").is_none(),
                    "positions would reveal hidden events"
                );
            }
        }
    }
    assert!(v.get("count").is_none() && v.get("head").is_none());

    // T is rejected, and its truth cites the refutation, not the claim.
    let v = ask(&l, &lab_only(), &format!("?- truth(\"{t}\", X)."));
    assert_eq!(rows(&v), vec![vec!["false".to_string()]]);
    let node = v
        .get("nodes")
        .unwrap()
        .as_object()
        .unwrap()
        .iter()
        .find(|(_, n)| n.get("sources").is_some())
        .unwrap()
        .1
        .clone();
    let src = &node.get("sources").unwrap().as_array().unwrap()[0];
    assert_eq!(src.get("kind").unwrap().as_str(), Some("supersede"));
    let v = ask(&l, &lab_only(), "?- rejected(C).");
    assert_eq!(rows(&v), vec![vec![t]]);
}

#[test]
fn queries_see_only_the_readers_compartments() {
    let (l, ..) = memory();
    let mine = ask(&l, &lab_only(), "?- claim(C).");
    let all = ask(
        &l,
        &["lab".to_string(), "private".to_string()],
        "?- claim(C).",
    );
    assert_eq!(rows(&mine).len(), 4);
    assert_eq!(rows(&all).len(), 5);
    let v = ask(&l, &lab_only(), "?- member(W, K).");
    assert!(
        rows(&v).iter().all(|row| row[1] == "lab"),
        "john's private compartment stays hidden"
    );
    let v = ask(
        &l,
        &lab_only(),
        "?- statement(C, S), contains(S, \"secret\").",
    );
    assert!(rows(&v).is_empty());
    // A hidden event never appears as a source.
    let secret_event = eid(l.events.last().unwrap());
    let v = ask(&l, &lab_only(), "?- asserted_by(C, W).");
    let text = gnomon_core::json::canonical(&v);
    assert!(!text.contains(&format!("\"{secret_event}\"")));
    // Errors don't depend on hidden data.
    let e1 = query::run(&l, &lab_only(), "?- nope(X).", Limits::default()).unwrap_err();
    let e2 = query::run(&lab(), &lab_only(), "?- nope(X).", Limits::default()).unwrap_err();
    assert_eq!((e1.code, e1.message), (e2.code, e2.message));
}

#[test]
fn as_of_answers_what_was_known_then() {
    let (l, _, _, t, _) = memory();
    let before = l.as_of(&common::t(14));
    let v = ask(&before, &lab_only(), &format!("?- believed(\"{t}\")."));
    assert_eq!(v.get("answers").unwrap().as_array().unwrap().len(), 1);
    let v = ask(&l, &lab_only(), &format!("?- believed(\"{t}\")."));
    assert_eq!(v.get("answers").unwrap().as_array().unwrap().len(), 0);
}

#[test]
fn open_disputes_hold_belief_back_and_cite_the_contest() {
    let (mut l, rust, go, ..) = memory();
    let c = l
        .append(draft(
            "gpt-g1",
            20,
            "supersede",
            sup(&rust, &go, "contested", vec![]),
        ))
        .unwrap();
    let v = ask(&l, &lab_only(), "?- decision(C).");
    assert!(rows(&v).is_empty(), "a contested decision does not stand");
    let v = ask(&l, &lab_only(), "?- disputed(C).");
    let leaf = v.get("nodes").unwrap().as_object().unwrap()[0].1.clone();
    assert_eq!(
        leaf.get("sources").unwrap().as_array().unwrap()[0]
            .get("event")
            .unwrap()
            .as_str(),
        Some(eid(&c).as_str())
    );
}

#[test]
fn base_relations_and_views_cannot_be_redefined() {
    let (l, ..) = memory();
    for src in [
        "claim(\"x\"). ?- claim(C).",
        "believed(C) :- claim(C). ?- believed(C).",
    ] {
        let e = query::run(&l, &lab_only(), src, Limits::default()).unwrap_err();
        assert_eq!(e.code, "E_QUERY");
        assert!(e.message.contains("cannot be redefined"), "{}", e.message);
    }
    // Users build on views freely.
    let v = ask(
        &l,
        &lab_only(),
        "mine(C) :- decision(C), author(C, \"claude-c1\"). ?- mine(C).",
    );
    assert_eq!(rows(&v).len(), 1);
}

#[test]
fn every_base_relation_is_documented_and_loaded() {
    // Each relation in the schema can be queried (no unknown-relation error).
    for (name, arity) in query::schema() {
        let args: Vec<String> = (0..arity).map(|i| format!("X{i}")).collect();
        ask(
            &memory().0,
            &lab_only(),
            &format!("?- {name}({}).", args.join(", ")),
        );
    }
}
