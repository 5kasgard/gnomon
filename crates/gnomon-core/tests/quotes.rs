//! M2: claims taken from an episode quote it exactly, and the gate checks
//! the quote against the episode itself: the words, who said them, and (for
//! direct claims) that the statement is those words. An extractor, however
//! buggy or however clever, cannot put words in anyone's mouth.

mod common;
use common::*;

use gnomon_core::json::Value;
use gnomon_core::ledger::Ledger;

const TEXT: &str = "John: Café first. Let's use Rust for the core.\nClaude: Agreed. We will delete nothing.";

/// Code-point offset of `needle` in TEXT.
fn at(needle: &str) -> i64 {
    TEXT.find(needle).map(|b| TEXT[..b].chars().count()).unwrap() as i64
}

fn turn(speaker: &str, start: i64, end: i64) -> Value {
    Value::obj(vec![
        ("speaker", Value::str(speaker)),
        ("start", Value::Int(start)),
        ("end", Value::Int(end)),
    ])
}

fn transcript_turns() -> Value {
    let john_end = at("\nClaude:");
    let claude_start = at("Agreed.");
    Value::Array(vec![
        turn("John", at("Café"), john_end),
        turn("Claude", claude_start, TEXT.chars().count() as i64),
    ])
}

fn episode(l: &mut Ledger, minute: u32, content: &str, participants: &[&str], turns: Option<Value>) -> Result<String, &'static str> {
    let mut fields = vec![
        ("compartment", Value::str("lab")),
        ("content", Value::str(content)),
        ("medium", Value::str("chat")),
        ("participants", Value::Array(participants.iter().map(|p| Value::str(*p)).collect())),
    ];
    if let Some(t) = turns {
        fields.push(("turns", t));
    }
    match l.append(draft("claude-c1", minute, "episode", Value::obj(fields))) {
        Ok(env) => Ok(env.get("event_id").unwrap().as_str().unwrap().to_string()),
        Err(e) => Err(e.code),
    }
}

fn transcript(l: &mut Ledger) -> String {
    episode(l, 10, TEXT, &["John", "Claude"], Some(transcript_turns())).unwrap()
}

/// A direct quote: statement == quote.
fn said(ep: &str, speaker: &str, quote: &str, start: i64) -> Value {
    claim_body(quote, "decided", "true")
        .with("speaker", Value::str(speaker))
        .with("origin", Value::str("direct"))
        .with("source_episode", Value::str(ep))
        .with("quote", Value::str(quote))
        .with("quote_start", Value::Int(start))
}

#[test]
fn exact_quote_by_its_speaker_is_accepted() {
    let mut l = lab();
    let ep = transcript(&mut l);
    // Offsets count Unicode code points: "é" is one, not two bytes.
    let q = "Let's use Rust for the core.";
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", said(&ep, "John", q, at(q))))), "OK");
}

#[test]
fn invented_shifted_or_overlong_quotes_are_refused() {
    let mut l = lab();
    let ep = transcript(&mut l);
    let pos = at("Let's");
    for (q, s) in [
        ("Let's use Python for the core.", pos), // words never said
        ("Let's use Rust for the core.", pos + 1), // right words, wrong place
        ("Let's use Rust for the core. And more", pos), // runs past what was said
        ("x", 99_999), // beyond the episode
        // Inside the speaker's turn, same length: only the word-for-word
        // comparison can catch these (found by mutation testing).
        ("Let's use Ruby for the core.", pos), // one word swapped
        ("Café first.", at("Café first.") + 1), // shifted by one, still inside the turn
        ("CAFÉ FIRST.", at("Café first.")), // case changed
    ] {
        assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", said(&ep, "John", q, s)))), "E_QUOTE", "{q}");
    }
}

/// Review M2, finding 1: a valid quote used as a certificate for anything.
#[test]
fn a_quote_cannot_certify_a_different_statement_or_speaker() {
    let mut l = lab();
    let ep = transcript(&mut l);
    // Claude's words attributed to John.
    let q = "We will delete nothing.";
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", said(&ep, "John", q, at(q))))), "E_QUOTE");
    assert_eq!(code(l.append(draft("gpt-g1", 12, "claim", said(&ep, "Claude", q, at(q))))), "OK");
    // A quote spanning two turns belongs to nobody.
    let both = "core.\nClaude: Agreed.";
    for who in ["John", "Claude"] {
        assert_eq!(code(l.append(draft("gpt-g1", 13, "claim", said(&ep, who, both, at(both))))), "E_QUOTE", "{who}");
    }
    // A direct claim must say exactly what its quote says.
    let lie = said(&ep, "John", "Café first.", at("Café first."))
        .with("statement", Value::str("John approved deleting the database."));
    assert_eq!(code(l.append(draft("gpt-g1", 14, "claim", lie))), "E_QUOTE");
    // Text outside every turn (the "John: " label) is nobody's words.
    assert_eq!(code(l.append(draft("gpt-g1", 15, "claim", said(&ep, "John", "John", 0)))), "E_QUOTE");
}

#[test]
fn paraphrase_is_allowed_only_when_not_claimed_as_direct_and_never_near_certain() {
    let mut l = lab();
    let ep = transcript(&mut l);
    let q = "Let's use Rust for the core.";
    let para = |confidence: i64| {
        said(&ep, "John", q, at(q))
            .with("statement", Value::str("John chose Rust for the core"))
            .with("origin", Value::str("inferred"))
            .with("confidence", Value::Int(confidence))
    };
    // Review M2 round 2, finding 2: a paraphrase at full confidence.
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", para(10_000)))), "E_QUOTE");
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", para(7_001)))), "E_QUOTE");
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", para(7_000)))), "OK");
}

/// Review M2 round 2, finding 1: "direct" without any quote skipped every check.
#[test]
fn a_direct_claim_must_carry_its_quote() {
    let mut l = lab();
    let ep = transcript(&mut l);
    let bare = claim_body("I approve deleting the production database.", "decided", "true")
        .with("speaker", Value::str("John"))
        .with("origin", Value::str("direct"));
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", bare.clone()))), "E_QUOTE");
    let sourced = bare.with("source_episode", Value::str(ep));
    assert_eq!(code(l.append(draft("gpt-g1", 12, "claim", sourced))), "E_QUOTE");
}

/// Review M2 round 2, finding 7: speakers that only differ in case, or
/// names hiding a line break.
#[test]
fn participant_names_are_distinct_and_single_line() {
    let mut l = lab();
    for people in [&["John", "JOHN"][..], &["John\nClaude"][..], &["John", "Al\tex"][..]] {
        assert_eq!(episode(&mut l, 10, TEXT, people, None), Err("E_BODY"), "{people:?}");
    }
}

#[test]
fn readers_see_who_recorded_the_quoted_source() {
    let mut l = lab();
    let ep = transcript(&mut l);
    let q = "Let's use Rust for the core.";
    let id = {
        let body = said(&ep, "John", q, at(q));
        l.append(draft("gpt-g1", 11, "claim", body.clone())).unwrap();
        gnomon_core::ledger::claim_id(&body)
    };
    let view = l.state.to_value(None);
    let c = view.get("claims").unwrap().get(&id).unwrap();
    assert_eq!(c.get("source_recorded_by").unwrap().as_str(), Some("claude-c1"));
}

#[test]
fn episodes_without_turns_have_one_speaker_or_none() {
    let mut l = lab();
    let doc = "Rust is fast. Python is friendly.";
    let solo = episode(&mut l, 10, doc, &["John"], None).unwrap();
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", said(&solo, "John", "Rust is fast.", 0)))), "OK");
    assert_eq!(code(l.append(draft("gpt-g1", 12, "claim", said(&solo, "Claude", "Python is friendly.", 14)))), "E_QUOTE");
    // Two participants and no turns: nobody can be quoted.
    let group = episode(&mut l, 13, doc, &["John", "Claude"], None).unwrap();
    assert_eq!(code(l.append(draft("gpt-g1", 14, "claim", said(&group, "John", "Rust is fast.", 0)))), "E_QUOTE");
}

#[test]
fn turns_must_be_well_formed() {
    let mut l = lab();
    let n = TEXT.chars().count() as i64;
    for (turns, why) in [
        (Value::Array(vec![turn("Bob", 0, 5)]), "speaker not a participant"),
        (Value::Array(vec![turn("John", 5, 5)]), "empty"),
        (Value::Array(vec![turn("John", 0, n + 1)]), "past the end"),
        (Value::Array(vec![turn("John", 0, 10), turn("Claude", 5, 20)]), "overlapping"),
        (Value::Array(vec![turn("Claude", 20, 30), turn("John", 0, 10)]), "out of order"),
        (Value::Array(vec![turn("John", -1, 10)]), "negative"),
    ] {
        assert_eq!(episode(&mut l, 10, TEXT, &["John", "Claude"], Some(turns)), Err("E_BODY"), "{why}");
    }
}

#[test]
fn quote_needs_its_source_and_position() {
    let mut l = lab();
    let ep = transcript(&mut l);
    let no_source = claim_body("x", "asserted", "true")
        .with("quote", Value::str("Agreed."))
        .with("quote_start", Value::Int(0));
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", no_source))), "E_BODY");
    let no_start = claim_body("x", "asserted", "true")
        .with("source_episode", Value::str(ep.clone()))
        .with("quote", Value::str("Agreed."));
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", no_start))), "E_BODY");
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", said(&ep, "John", "John", -1)))), "E_BODY");
}

#[test]
fn hidden_characters_cannot_be_quoted_into_a_claim() {
    let mut l = lab();
    let ep = episode(&mut l, 10, "ok\u{200b}go now", &["John"], None).unwrap();
    // The episode keeps them; a claim may not carry them, even as a quote.
    assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", said(&ep, "John", "ok\u{200b}go now", 0)))), "E_HIDDEN_CHARS");
}

#[test]
fn hidden_episode_answers_like_missing_even_with_a_quote() {
    let mut l = lab();
    l.append(draft("john", 5, "register", register_body("bridge", "bridge-co", "model", &["write"], &["private"])))
        .unwrap();
    let body = Value::obj(vec![
        ("compartment", Value::str("private")),
        ("content", Value::str("secret plan")),
        ("medium", Value::str("chat")),
        ("participants", Value::Array(vec![Value::str("John")])),
    ]);
    l.append(draft("bridge", 10, "episode", body)).unwrap();
    // Guessing the right words gives the same answer as guessing wrong ones.
    let right = code(l.append(draft("gpt-g1", 11, "claim", said("bridge/1", "John", "secret", 0))));
    let wrong = code(l.append(draft("gpt-g1", 11, "claim", said("bridge/1", "John", "public", 0))));
    assert_eq!((right, wrong), ("E_REF", "E_REF"));
}

/// Review M2 round 3, finding 1: leaving the quote out escaped the cap.
#[test]
fn hearsay_without_a_quote_is_never_near_certain() {
    let mut l = lab();
    let hearsay = |origin: &str, confidence: i64| {
        claim_body("John approved deleting the production database.", "decided", "true")
            .with("speaker", Value::str("John"))
            .with("origin", Value::str(origin))
            .with("confidence", Value::Int(confidence))
    };
    for origin in ["inferred", "derived", "manual", "observed"] {
        assert_eq!(code(l.append(draft("gpt-g1", 11, "claim", hearsay(origin, 10_000)))), "E_ATTRIBUTION", "{origin}");
    }
    assert_eq!(code(l.append(draft("gpt-g1", 12, "claim", hearsay("inferred", 7_000)))), "OK");
    // M6: a name is only a name. "John" is not the writer john unless john
    // registered that name.
    assert_eq!(code(l.append(draft("john", 13, "claim", hearsay("manual", 10_000)))), "E_ATTRIBUTION");
    let mine = hearsay("manual", 10_000).with("speaker", Value::str("john"));
    assert_eq!(code(l.append(draft("john", 13, "claim", mine))), "OK");
    let named = register_body("ann", "ann", "human", &["write"], &["lab"]).with("name", Value::str("John"));
    l.append(draft("john", 14, "register", named)).unwrap();
    assert_eq!(code(l.append(draft("ann", 15, "claim", hearsay("manual", 10_000)))), "OK");
    // Nor does a writer speak with certainty for its party, or for another
    // session of its own model: each instance is its own speaker.
    for speaker in ["anthropic", "claude-c1"] {
        let other = claim_body("We ship on Friday.", "decided", "true")
            .with("speaker", Value::str(speaker))
            .with("confidence", Value::Int(9_500));
        assert_eq!(code(l.append(draft("claude-c2", 16, "claim", other))), "E_ATTRIBUTION", "{speaker}");
    }
}

/// Review M2 round 3, finding 2: look-alike names the gate accepted.
#[test]
fn look_alike_participant_names_are_refused_by_the_gate() {
    let mut l = lab();
    for people in [
        &["Zo\u{eb}", "Zoe\u{308}"][..], // composed and decomposed
        &["Zoe\u{308}"][..],              // decomposed alone
        &["Stra\u{df}e", "STRASSE"][..],  // full case fold
        &["John", "\u{410}lex"][..],      // Cyrillic A
        &["John", "John\u{a0}"][..],      // trailing no-break space
        &["John", " John"][..],           // leading space
    ] {
        assert_eq!(episode(&mut l, 10, TEXT, people, None), Err("E_BODY"), "{people:?}");
    }
    assert!(episode(&mut l, 10, TEXT, &["Zo\u{eb}", "\u{410}\u{43b}\u{435}\u{43a}\u{441}"], None).is_ok());
}

/// Review M4, finding 3: a writer that records a "transcript" itself and
/// then quotes others from it is still giving its own account of what they
/// said. The quote is checked against the recording, but the recording is
/// the writer's party's: such quotes are capped like any attribution.
#[test]
fn quoting_others_from_your_own_recording_is_capped() {
    let mut l = lab();
    let ep = transcript(&mut l); // recorded by claude-c1 (party anthropic)
    let q = "Let's use Rust for the core.";
    let sure = said(&ep, "John", q, at(q)).with("confidence", Value::Int(9000));
    // Same party as the recorder: capped.
    assert_eq!(code(l.append(draft("claude-c2", 11, "claim", sure.clone()))), "E_ATTRIBUTION");
    assert_eq!(code(l.append(draft("claude-c1", 12, "claim", sure.clone()))), "E_ATTRIBUTION");
    assert_eq!(code(l.append(draft("claude-c2", 13, "claim", said(&ep, "John", q, at(q))))), "OK"); // 7000
    // Another party quoting the same recording: the recording is not theirs.
    assert_eq!(code(l.append(draft("gpt-g1", 14, "claim", sure))), "OK");
}
