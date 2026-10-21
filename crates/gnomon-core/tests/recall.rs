//! M5: recall. The text and calendar machinery, exactly.

use gnomon_core::json::parse;
use gnomon_core::recall::{civil_from_days, days_from_civil, resolve, stem, terms, Request};

#[test]
fn porter_stemmer_matches_the_published_examples() {
    // Examples from Porter (1980), "An algorithm for suffix stripping".
    let cases = [
        ("caresses", "caress"),
        ("ponies", "poni"),
        ("ties", "ti"),
        ("caress", "caress"),
        ("cats", "cat"),
        ("feed", "feed"),
        ("agreed", "agre"),
        ("plastered", "plaster"),
        ("motoring", "motor"),
        ("sing", "sing"),
        ("conflated", "conflat"),
        ("troubled", "troubl"),
        ("sized", "size"),
        ("hopping", "hop"),
        ("tanned", "tan"),
        ("falling", "fall"),
        ("hissing", "hiss"),
        ("fizzed", "fizz"),
        ("failing", "fail"),
        ("filing", "file"),
        ("happy", "happi"),
        ("sky", "sky"),
        ("relational", "relat"),
        ("conditional", "condit"),
        ("rational", "ration"),
        ("valenci", "valenc"),
        ("digitizer", "digit"),
        ("operator", "oper"),
        ("triplicate", "triplic"),
        ("formative", "form"),
        ("electrical", "electr"),
        ("hopeful", "hope"),
        ("goodness", "good"),
        ("revival", "reviv"),
        ("allowance", "allow"),
        ("inference", "infer"),
        ("adjustable", "adjust"),
        ("effective", "effect"),
        ("probate", "probat"),
        ("rate", "rate"),
        ("controll", "control"),
        ("roll", "roll"),
    ];
    for (w, want) in cases {
        assert_eq!(stem(w), want, "stem({w})");
    }
}

#[test]
fn stop_words_go_and_forms_meet() {
    assert_eq!(terms("What did Caroline paint?"), vec!["carolin", "paint"]);
    assert_eq!(terms("She was painting sunsets"), vec!["paint", "sunset"]);
}

#[test]
fn the_calendar_round_trips() {
    for z in -800_000..800_000 {
        let (y, m, d) = civil_from_days(z);
        assert_eq!(days_from_civil(y, m, d), z);
    }
    assert_eq!(days_from_civil(1970, 1, 1), 0);
    assert_eq!(
        civil_from_days(days_from_civil(2024, 2, 29) + 1),
        (2024, 3, 1)
    );
    assert_eq!(
        civil_from_days(days_from_civil(2100, 2, 28) + 1),
        (2100, 3, 1)
    );
}

fn dates(text: &str, y: i64, m: i64, d: i64) -> Vec<(String, String)> {
    let z = days_from_civil(y, m, d);
    let out = resolve(text, z);
    out.into_iter()
        .map(|(p, s)| {
            let f = civil_from_days(s.first);
            let l = civil_from_days(s.last);
            (
                p,
                format!(
                    "{:04}-{:02}-{:02}..{:04}-{:02}-{:02}",
                    f.0, f.1, f.2, l.0, l.1, l.2
                ),
            )
        })
        .collect()
}

#[test]
fn relative_dates_resolve_by_the_calendar() {
    // 8 May 2023 was a Monday.
    let one = |t: &str| {
        dates(t, 2023, 5, 8)
            .into_iter()
            .map(|x| x.1)
            .collect::<Vec<_>>()
    };
    assert_eq!(one("I went yesterday"), ["2023-05-07..2023-05-07"]);
    assert_eq!(one("see you tomorrow"), ["2023-05-09..2023-05-09"]);
    assert_eq!(one("the day before yesterday"), ["2023-05-06..2023-05-06"]);
    assert_eq!(one("last Friday"), ["2023-05-05..2023-05-05"]);
    assert_eq!(one("next week"), ["2023-05-15..2023-05-21"]);
    assert_eq!(one("last month"), ["2023-04-01..2023-04-30"]);
    assert_eq!(one("last year"), ["2022-01-01..2022-12-31"]);
    assert_eq!(one("three days ago"), ["2023-05-05..2023-05-05"]);
    // Month arithmetic across a year boundary, and leap days.
    assert_eq!(
        dates("last month", 2024, 1, 15)[0].1,
        "2023-12-01..2023-12-31"
    );
    assert_eq!(
        dates("next month", 2024, 1, 31)[0].1,
        "2024-02-01..2024-02-29"
    );
    assert!(one("nothing temporal here").is_empty());
}

#[test]
fn requests_are_checked() {
    let bad = [
        r#"{}"#,
        r#"{"query":"x","k":0}"#,
        r#"{"query":"x","k":201}"#,
        r#"{"query":"x","kinds":["pages"]}"#,
        r#"{"query":"x","after":"yesterday"}"#,
        r#"{"query":"x","context":6}"#,
        r#"{"query":"x","tuning":{"magic":true}}"#,
        r#"{"query":"x","tuning":{"prf":1}}"#,
        r#"{"query":"x","colour":"red"}"#,
    ];
    for b in bad {
        let e = Request::from_json(&parse(b).unwrap())
            .err()
            .unwrap_or_else(|| panic!("accepted {b}"));
        assert_eq!(e.code, "E_RECALL", "{b}");
    }
    let r = Request::from_json(&parse(r#"{"query":"x"}"#).unwrap()).unwrap();
    assert_eq!(r.k, 10);
    assert!(r.turns && r.claims);
    assert!(r.tuning.joins && r.tuning.speaker_prior && r.tuning.propagate && !r.tuning.prf);
}

// ---------------------------------------------------- review M5 findings

#[test]
fn stemming_long_words_is_cheap() {
    let w = "y".repeat(99_000) + "ement";
    let t = std::time::Instant::now();
    assert_eq!(stem(&w), w); // longer than any English word: left alone
    assert!(t.elapsed().as_millis() < 50);
    assert_eq!(stem(&"y".repeat(48)).len(), 48);
}

#[test]
fn in_n_units_and_the_calendar_edges() {
    let one = |t: &str| {
        dates(t, 2023, 5, 8)
            .into_iter()
            .map(|x| x.1)
            .collect::<Vec<_>>()
    };
    assert_eq!(one("in two months"), ["2023-07-01..2023-07-31"]);
    assert_eq!(one("in 3 days"), ["2023-05-11..2023-05-11"]);
    assert_eq!(one("two days from now"), ["2023-05-10..2023-05-10"]);
    // Dates the calendar cannot write as YYYY-MM-DD are not resolved.
    assert!(dates("100 years ago", 50, 6, 1).is_empty());
    assert!(dates("tomorrow", 9999, 12, 31).is_empty());
    assert_eq!(dates("yesterday", 0, 1, 2).len(), 1);
}

#[test]
fn speaker_must_be_text() {
    for b in [
        r#"{"query":"x","speaker":5}"#,
        r#"{"query":"x","speaker":["Ann"]}"#,
    ] {
        assert!(Request::from_json(&parse(b).unwrap()).is_err(), "{b}");
    }
}
