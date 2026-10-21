//! Recall: find what was said, by its words, exactly and explainably.
//!
//! Queries in Datalog need exact facts; recall finds the right moments in
//! everything recorded from a question in plain language. It is ranked
//! retrieval (BM25 over stemmed words, with the speaker's name as part of
//! each turn), computed by plain code: the same memory and question always
//! give the same hits, in the same order, with the same scores.
//!
//! Every hit says where it came from (the episode, its event and hash, the
//! exact position of the words) and why it was recalled (the matched terms
//! and what each contributed). Recall reads the same view as queries: the
//! reader's compartments, as of a moment.
//!
//! Time is resolved exactly. A turn saying "yesterday" in a session recorded
//! on 8 May 2023 carries "yesterday = 2023-05-07"; "last week", "two months
//! ago", "next Friday" and the like become dates or date ranges by calendar
//! arithmetic, never by guesswork. A question can carry its own moment
//! (`now`), so "what did I do last weekend?" favours turns from then.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::{Arc, Mutex, OnceLock};

use crate::json::Value;
use crate::ledger::Ledger;
use crate::schema::{GateError, GateResult};

const MAX_K: usize = 200;
const MAX_QUERY: usize = 10_000;

fn rerr(msg: impl Into<String>) -> GateError {
    GateError::new("E_RECALL", msg)
}

// ------------------------------------------------------------- the request

pub struct Request {
    pub query: String,
    pub k: usize,
    pub turns: bool,
    pub claims: bool,
    pub speaker: Option<String>,
    pub after: Option<String>,
    pub before: Option<String>,
    pub now: Option<String>,
    pub context: usize,
    pub tuning: Tuning,
}

/// Ranking techniques, each switchable so its effect can be measured.
/// All are standard information-retrieval methods with textbook settings.
#[derive(Clone, Copy, Debug)]
pub struct Tuning {
    /// Index adjacent words joined ("road trip" ~ "roadtrip", "de-stress").
    pub joins: bool,
    /// A name in the question means who spoke, not who was addressed.
    pub speaker_prior: bool,
    /// A turn also scores part of the turn before it (answers follow
    /// questions) and after it.
    pub propagate: bool,
    /// Pseudo-relevance feedback (RM3): expand the question with the
    /// strongest terms of its first results. Off by default: on LoCoMo it
    /// cost far more at the top of the ranking than it gained further down.
    pub prf: bool,
    /// Weights, in thousandths (JSON here has no fractions), switchable for
    /// measurement: BM25's k1 and b; the share of a question before a turn
    /// and of other neighbours; the speaker and dates boosts; how much a
    /// turn gains from its whole conversation matching (session); and the
    /// boost for a turn holding a date when the question asks when.
    pub k1: i64,
    pub b: i64,
    pub prop_q: i64,
    pub prop_n: i64,
    pub speaker: i64,
    pub window: i64,
    pub session: i64,
    pub when: i64,
    /// Diversity, in thousandths: each later hit from a conversation that
    /// already has one higher up keeps this share of its score (1000 = off).
    /// Questions whose evidence spans conversations need more than one of
    /// them near the top.
    pub diverse: i64,
}

impl Default for Tuning {
    fn default() -> Self {
        Tuning {
            joins: true,
            speaker_prior: true,
            propagate: true,
            prf: false,
            // Chosen on LoCoMo conversations 0-4 only (bench/locomo.py
            // --grid); conversations 5-9 were held out to check them.
            k1: 1200,
            b: 400,
            prop_q: 1000,
            prop_n: 250,
            speaker: 2500,
            window: 1500,
            session: 1200,
            when: 500,
            diverse: 1000,
        }
    }
}

impl Request {
    pub fn from_json(v: &Value) -> GateResult<Request> {
        let obj = v
            .as_object()
            .ok_or_else(|| rerr("request must be an object"))?;
        for (k, _) in obj {
            if ![
                "query", "k", "kinds", "speaker", "after", "before", "now", "context", "tuning",
            ]
            .contains(&k.as_str())
            {
                return Err(rerr(format!("unknown field '{k}'")));
            }
        }
        let query = v
            .get("query")
            .and_then(|q| q.as_str())
            .ok_or_else(|| rerr("'query' is required"))?;
        if query.len() > MAX_QUERY {
            return Err(rerr(format!("query longer than {MAX_QUERY} bytes")));
        }
        let k = match v.get("k") {
            None => 10,
            Some(x) => match x.as_int() {
                Some(n) if n >= 1 && n as usize <= MAX_K => n as usize,
                _ => return Err(rerr(format!("'k' must be 1..{MAX_K}"))),
            },
        };
        let (mut turns, mut claims) = (true, true);
        if let Some(kinds) = v.get("kinds") {
            let list = kinds
                .as_array()
                .ok_or_else(|| rerr("'kinds' must be a list"))?;
            turns = false;
            claims = false;
            for x in list {
                match x.as_str() {
                    Some("turns") => turns = true,
                    Some("claims") => claims = true,
                    _ => return Err(rerr("'kinds' may hold \"turns\" and \"claims\"")),
                }
            }
        }
        let time = |name: &str| -> GateResult<Option<String>> {
            match v.get(name) {
                None => Ok(None),
                Some(t) => match t.as_str() {
                    Some(s) if crate::schema::is_timestamp(s) => Ok(Some(s.to_string())),
                    _ => Err(rerr(format!("'{name}' must be YYYY-MM-DDTHH:MM:SSZ"))),
                },
            }
        };
        let context = match v.get("context") {
            None => 0,
            Some(x) => match x.as_int() {
                Some(n) if (0..=5).contains(&n) => n as usize,
                _ => return Err(rerr("'context' must be 0..5")),
            },
        };
        let mut tuning = Tuning::default();
        if let Some(t) = v.get("tuning") {
            let obj = t
                .as_object()
                .ok_or_else(|| rerr("'tuning' must be an object"))?;
            for (k, x) in obj {
                let flag = |x: &Value| match x {
                    Value::Bool(b) => Ok(*b),
                    _ => Err(rerr(format!("tuning '{k}' must be true or false"))),
                };
                let num = |x: &Value| match x.as_int() {
                    Some(n) if (0..=100_000).contains(&n) => Ok(n),
                    _ => Err(rerr(format!(
                        "tuning '{k}' must be an integer 0..100000 (thousandths)"
                    ))),
                };
                match k.as_str() {
                    "joins" => tuning.joins = flag(x)?,
                    "speaker_prior" => tuning.speaker_prior = flag(x)?,
                    "propagate" => tuning.propagate = flag(x)?,
                    "prf" => tuning.prf = flag(x)?,
                    "k1" => tuning.k1 = num(x)?,
                    "b" => {
                        tuning.b = num(x)?;
                        if tuning.b > 1000 {
                            return Err(rerr(
                                "tuning 'b' is at most 1000 (BM25's b is between 0 and 1)",
                            ));
                        }
                    }
                    "prop_q" => tuning.prop_q = num(x)?,
                    "prop_n" => tuning.prop_n = num(x)?,
                    "speaker" => tuning.speaker = num(x)?,
                    "window" => tuning.window = num(x)?,
                    "session" => tuning.session = num(x)?,
                    "when" => tuning.when = num(x)?,
                    "diverse" => {
                        tuning.diverse = num(x)?;
                        if tuning.diverse > 1000 {
                            return Err(rerr("tuning 'diverse' is at most 1000 (1000 is off)"));
                        }
                    }
                    _ => return Err(rerr(format!("unknown tuning '{k}'"))),
                }
            }
        }
        Ok(Request {
            tuning,
            query: query.to_string(),
            k,
            turns,
            claims,
            speaker: match v.get("speaker") {
                None => None,
                Some(x) => Some(
                    x.as_str()
                        .ok_or_else(|| rerr("'speaker' must be a string"))?
                        .to_string(),
                ),
            },
            after: time("after")?,
            before: time("before")?,
            now: time("now")?,
            context,
        })
    }
}

// ------------------------------------------------------------------ words

const STOP: &[&str] = &[
    "a", "about", "after", "again", "all", "also", "am", "an", "and", "any", "are", "as", "at",
    "be", "been", "being", "but", "by", "can", "could", "did", "do", "does", "doing", "for",
    "from", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "just", "me", "my", "of", "on", "or", "our", "ours",
    "she", "so", "some", "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "to", "too", "us", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "s", "t", "ll",
    "re", "ve", "d", "m",
];

/// Lower-cased words: runs of letters and digits.
pub fn words(text: &str) -> Vec<String> {
    words_cased(text).into_iter().map(|(w, _)| w).collect()
}

/// Lower-cased words, each with whether it was written with a capital.
fn words_cased(text: &str) -> Vec<(String, bool)> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut cap = false;
    for c in text.chars() {
        if c.is_alphanumeric() {
            if cur.is_empty() {
                cap = c.is_uppercase();
            }
            for l in c.to_lowercase() {
                cur.push(l);
            }
        } else if !cur.is_empty() {
            out.push((std::mem::take(&mut cur), cap));
        }
    }
    if !cur.is_empty() {
        out.push((cur, cap));
    }
    out
}

/// Index terms: words without stopwords, stemmed.
pub fn terms(text: &str) -> Vec<String> {
    words(text)
        .into_iter()
        .filter(|w| !STOP.contains(&w.as_str()))
        .map(|w| stem(&w))
        .collect()
}

/// Terms plus each pair of adjacent words joined into one ("road trip" ->
/// "roadtrip", "de-stress" -> "destress"), so either spelling finds the other.
pub fn terms_joined(text: &str) -> Vec<String> {
    let w = words(text);
    let mut out: Vec<String> = w
        .iter()
        .filter(|x| !STOP.contains(&x.as_str()))
        .map(|x| stem(x))
        .collect();
    for pair in w.windows(2) {
        let alpha = |s: &str| s.chars().all(|c| c.is_alphabetic());
        if alpha(&pair[0])
            && alpha(&pair[1])
            && !(STOP.contains(&pair[0].as_str()) && STOP.contains(&pair[1].as_str()))
        {
            out.push(stem(&format!("{}{}", pair[0], pair[1])));
        }
    }
    out
}

// ----------------------------------------------------- the Porter stemmer

/// M. F. Porter's 1980 stemmer, for ASCII lower-case words; other words,
/// and words longer than MAX_STEM letters (no English word is), are returned
/// unchanged, which keeps the cost of stemming linear in the text.
pub fn stem(word: &str) -> String {
    if word.len() <= 2 || word.len() > MAX_STEM || !word.bytes().all(|b| b.is_ascii_lowercase()) {
        return word.to_string();
    }
    let mut w: Vec<u8> = word.as_bytes().to_vec();
    step1ab(&mut w);
    step1c(&mut w);
    step2(&mut w);
    step3(&mut w);
    step4(&mut w);
    step5(&mut w);
    String::from_utf8(w).expect("ascii")
}

const MAX_STEM: usize = 48;

fn is_cons(w: &[u8], i: usize) -> bool {
    match w[i] {
        b'a' | b'e' | b'i' | b'o' | b'u' => false,
        b'y' => i == 0 || !is_cons(w, i - 1),
        _ => true,
    }
}

/// The measure m of w[..len]: the number of VC sequences.
fn measure(w: &[u8], len: usize) -> usize {
    let mut n = 0;
    let mut i = 0;
    while i < len && is_cons(w, i) {
        i += 1;
    }
    loop {
        while i < len && !is_cons(w, i) {
            i += 1;
        }
        if i >= len {
            return n;
        }
        while i < len && is_cons(w, i) {
            i += 1;
        }
        n += 1;
        if i >= len {
            return n;
        }
    }
}

fn has_vowel(w: &[u8], len: usize) -> bool {
    (0..len).any(|i| !is_cons(w, i))
}

fn double_cons(w: &[u8], len: usize) -> bool {
    len >= 2 && w[len - 1] == w[len - 2] && is_cons(w, len - 1)
}

/// consonant-vowel-consonant at the end, the last not w, x or y.
fn cvc(w: &[u8], len: usize) -> bool {
    len >= 3
        && is_cons(w, len - 3)
        && !is_cons(w, len - 2)
        && is_cons(w, len - 1)
        && !matches!(w[len - 1], b'w' | b'x' | b'y')
}

fn ends(w: &[u8], s: &str) -> bool {
    w.ends_with(s.as_bytes())
}

/// Replace suffix `s` by `r` if the stem before it has measure > `m`.
fn replace_if(w: &mut Vec<u8>, s: &str, r: &str, m: usize) -> bool {
    if ends(w, s) {
        let stem_len = w.len() - s.len();
        if measure(w, stem_len) > m {
            w.truncate(stem_len);
            w.extend_from_slice(r.as_bytes());
        }
        return true;
    }
    false
}

fn step1ab(w: &mut Vec<u8>) {
    if ends(w, "sses") {
        w.truncate(w.len() - 2);
    } else if ends(w, "ies") {
        w.truncate(w.len() - 2);
    } else if ends(w, "ss") {
    } else if ends(w, "s") {
        w.truncate(w.len() - 1);
    }
    let mut extra = false;
    if ends(w, "eed") {
        if measure(w, w.len() - 3) > 0 {
            w.truncate(w.len() - 1);
        }
    } else if ends(w, "ed") && has_vowel(w, w.len() - 2) {
        w.truncate(w.len() - 2);
        extra = true;
    } else if ends(w, "ing") && has_vowel(w, w.len() - 3) {
        w.truncate(w.len() - 3);
        extra = true;
    }
    if extra {
        if ends(w, "at") || ends(w, "bl") || ends(w, "iz") {
            w.push(b'e');
        } else if double_cons(w, w.len()) && !matches!(w[w.len() - 1], b'l' | b's' | b'z') {
            w.truncate(w.len() - 1);
        } else if measure(w, w.len()) == 1 && cvc(w, w.len()) {
            w.push(b'e');
        }
    }
}

fn step1c(w: &mut [u8]) {
    let n = w.len();
    if n >= 2 && w[n - 1] == b'y' && has_vowel(w, n - 1) {
        w[n - 1] = b'i';
    }
}

fn step2(w: &mut Vec<u8>) {
    for (s, r) in [
        ("ational", "ate"),
        ("tional", "tion"),
        ("enci", "ence"),
        ("anci", "ance"),
        ("izer", "ize"),
        ("abli", "able"),
        ("alli", "al"),
        ("entli", "ent"),
        ("eli", "e"),
        ("ousli", "ous"),
        ("ization", "ize"),
        ("ation", "ate"),
        ("ator", "ate"),
        ("alism", "al"),
        ("iveness", "ive"),
        ("fulness", "ful"),
        ("ousness", "ous"),
        ("aliti", "al"),
        ("iviti", "ive"),
        ("biliti", "ble"),
    ] {
        if replace_if(w, s, r, 0) {
            return;
        }
    }
}

fn step3(w: &mut Vec<u8>) {
    for (s, r) in [
        ("icate", "ic"),
        ("ative", ""),
        ("alize", "al"),
        ("iciti", "ic"),
        ("ical", "ic"),
        ("ful", ""),
        ("ness", ""),
    ] {
        if replace_if(w, s, r, 0) {
            return;
        }
    }
}

fn step4(w: &mut Vec<u8>) {
    for s in [
        "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion",
        "ou", "ism", "ate", "iti", "ous", "ive", "ize",
    ] {
        if ends(w, s) {
            let stem_len = w.len() - s.len();
            let ok = if s == "ion" {
                stem_len > 0 && matches!(w[stem_len - 1], b's' | b't')
            } else {
                true
            };
            if ok && measure(w, stem_len) > 1 {
                w.truncate(stem_len);
            }
            return;
        }
    }
}

fn step5(w: &mut Vec<u8>) {
    let n = w.len();
    if n > 1 && w[n - 1] == b'e' {
        let m = measure(w, n - 1);
        if m > 1 || (m == 1 && !cvc(w, n - 1)) {
            w.truncate(n - 1);
        }
    }
    let n = w.len();
    if n > 1 && w[n - 1] == b'l' && double_cons(w, n) && measure(w, n) > 1 {
        w.truncate(n - 1);
    }
}

// ------------------------------------------------------------------ dates

/// Days since 1970-01-01 of a proleptic Gregorian date (Hinnant's algorithm).
pub fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

pub fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// 0 = Monday … 6 = Sunday.
fn weekday(z: i64) -> i64 {
    (z + 3).rem_euclid(7)
}

fn day_of(timestamp: &str) -> Option<i64> {
    let y = timestamp.get(0..4)?.parse().ok()?;
    let m = timestamp.get(5..7)?.parse().ok()?;
    let d = timestamp.get(8..10)?.parse().ok()?;
    Some(days_from_civil(y, m, d))
}

fn ymd(z: i64) -> String {
    let (y, m, d) = civil_from_days(z);
    format!("{y:04}-{m:02}-{d:02}")
}

/// A resolved span of days, inclusive.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Span {
    pub first: i64,
    pub last: i64,
}

impl Span {
    fn day(z: i64) -> Span {
        Span { first: z, last: z }
    }
    fn text(&self) -> String {
        if self.first == self.last {
            ymd(self.first)
        } else {
            let (y1, m1, d1) = civil_from_days(self.first);
            let (y2, m2, d2) = civil_from_days(self.last);
            let month_end = civil_from_days(self.last + 1).2 == 1;
            if d1 == 1 && month_end && (y1, m1) == (y2, m2) {
                format!("{y1:04}-{m1:02}")
            } else if (m1, d1, m2, d2) == (1, 1, 12, 31) && y1 == y2 {
                format!("{y1:04}")
            } else {
                format!("{}..{}", ymd(self.first), ymd(self.last))
            }
        }
    }
}

fn week_of(z: i64) -> Span {
    let monday = z - weekday(z);
    Span {
        first: monday,
        last: monday + 6,
    }
}

fn month_of(z: i64, shift: i64) -> Span {
    let (y, m, _) = civil_from_days(z);
    let idx = y * 12 + (m - 1) + shift;
    let (ny, nm) = (idx.div_euclid(12), idx.rem_euclid(12) + 1);
    let first = days_from_civil(ny, nm, 1);
    let next = if nm == 12 {
        days_from_civil(ny + 1, 1, 1)
    } else {
        days_from_civil(ny, nm + 1, 1)
    };
    Span {
        first,
        last: next - 1,
    }
}

fn year_of(z: i64, shift: i64) -> Span {
    let y = civil_from_days(z).0 + shift;
    Span {
        first: days_from_civil(y, 1, 1),
        last: days_from_civil(y, 12, 31),
    }
}

fn number(w: &str) -> Option<i64> {
    let n = match w {
        "a" | "an" | "one" => 1,
        "two" | "couple" => 2,
        "three" => 3,
        "four" => 4,
        "five" => 5,
        "six" => 6,
        "seven" => 7,
        "eight" => 8,
        "nine" => 9,
        "ten" => 10,
        "eleven" => 11,
        "twelve" => 12,
        _ => return w.parse::<i64>().ok().filter(|n| (1..=100).contains(n)),
    };
    Some(n)
}

const WEEKDAYS: [&str; 7] = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
];

/// Relative time expressions in `text`, resolved against day `z`: each with
/// the words matched and the span of days it means. The conventions (weeks
/// start on Monday; "last Friday" is the latest Friday before today; "this
/// weekend" is this week's Saturday and Sunday) are fixed and documented.
pub fn resolve(text: &str, z: i64) -> Vec<(String, Span)> {
    let w = words(text);
    let mut out = Vec::new();
    let mut i = 0;
    while i < w.len() {
        let at = |k: usize| w.get(i + k).map(|s| s.as_str()).unwrap_or("");
        let mut found: Option<(usize, Span)> = None;
        if at(0) == "the" && at(1) == "day" && at(2) == "before" && at(3) == "yesterday" {
            found = Some((4, Span::day(z - 2)));
        } else if at(0) == "the" && at(1) == "day" && at(2) == "after" && at(3) == "tomorrow" {
            found = Some((4, Span::day(z + 2)));
        } else if at(0) == "yesterday" || (at(0) == "last" && at(1) == "night") {
            found = Some((if at(0) == "last" { 2 } else { 1 }, Span::day(z - 1)));
        } else if at(0) == "today" || at(0) == "tonight" {
            found = Some((1, Span::day(z)));
        } else if at(0) == "tomorrow" {
            found = Some((1, Span::day(z + 1)));
        } else if matches!(at(0), "last" | "next" | "this") {
            let dir = match at(0) {
                "last" => -1,
                "next" => 1,
                _ => 0,
            };
            let unit = at(1);
            if let Some(wd) = WEEKDAYS.iter().position(|d| *d == unit) {
                let wd = wd as i64;
                let today = weekday(z);
                let day = match dir {
                    -1 => z - ((today - wd - 1).rem_euclid(7) + 1),
                    1 => z + ((wd - today - 1).rem_euclid(7) + 1),
                    _ => z - today + wd,
                };
                found = Some((2, Span::day(day)));
            } else {
                let span = match unit {
                    "week" => Some(Span {
                        first: week_of(z).first + 7 * dir,
                        last: week_of(z).last + 7 * dir,
                    }),
                    "weekend" => {
                        let sat = week_of(z).first + 5 + 7 * dir;
                        Some(Span {
                            first: sat,
                            last: sat + 1,
                        })
                    }
                    "month" => Some(month_of(z, dir)),
                    "year" => Some(year_of(z, dir)),
                    _ => None,
                };
                if let Some(s) = span {
                    found = Some((2, s));
                }
            }
        } else if let Some(n) = number(at(if at(0) == "in" { 1 } else { 0 })) {
            // "<n> days ago", "a week ago", "two days from now", "in two months"
            let o = if at(0) == "in" { 1 } else { 0 };
            let unit = at(o + 1).trim_end_matches('s');
            let ago = o == 0 && at(2) == "ago";
            let later = o == 1 || (at(2) == "from" && at(3) == "now");
            if ago || later {
                let sign = if ago { -1 } else { 1 };
                let used = if o == 1 {
                    3
                } else if ago {
                    3
                } else {
                    4
                };
                let span = match unit {
                    "day" => Some(Span::day(z + sign * n)),
                    "week" => Some(week_of(z + sign * 7 * n)),
                    "month" => Some(month_of(z, sign * n)),
                    "year" => Some(year_of(z, sign * n)),
                    _ => None,
                };
                if let Some(s) = span {
                    found = Some((used, s));
                }
            }
        }
        // Only dates the calendar can write as YYYY-MM-DD.
        let in_range = |s: &Span| {
            s.first >= days_from_civil(0, 1, 1) && s.last <= days_from_civil(9999, 12, 31)
        };
        match found.filter(|(_, s)| in_range(s)) {
            Some((len, span)) => {
                out.push((w[i..i + len].join(" "), span));
                i += len;
            }
            None => i += 1,
        }
    }
    out
}

// ------------------------------------------------------------------ units

struct Unit {
    kind: &'static str,
    /// Episode id or claim id.
    item: String,
    /// The event that recorded it.
    event: String,
    /// Turn index within its episode (claims: 0).
    turn: usize,
    speaker: String,
    time: String,
    /// Code-point offsets in the episode (claims: none).
    span: Option<(usize, usize)>,
    /// What does not change once recorded: its words and terms, dates,
    /// speaker words. Worked out once and cached by content.
    a: Arc<Analysis>,
    /// Its place in the analysis cache.
    slot: u32,
    /// The registered writer the speaker is, when the record says so.
    speaker_is: Option<String>,
    /// Passes the request's filters (speaker, after, before). Filters pick
    /// which hits are returned; everything the reader can see is still
    /// ranked, so a reply is found through the question before it.
    wanted: bool,
}

/// The fixed analysis of one turn or claim.
struct Analysis {
    text: String,
    /// Number of terms (the document length for BM25).
    len: usize,
    /// Each distinct term (by its id in the cache) with its count.
    tf: Vec<(u32, u32)>,
    speaker_terms: Vec<String>,
    /// Ends with a question mark (its answer is likely the next turn).
    question: bool,
    dates: Vec<(String, Span)>,
    /// Holds characters that do not show when printed (see schema).
    hidden: bool,
}

fn analyse(
    text: String,
    speaker: &str,
    day: Option<i64>,
    joins: bool,
    ids: &mut Interner,
) -> Analysis {
    let terms = if joins {
        terms_joined(&text)
    } else {
        terms(&text)
    };
    let mut counts: BTreeMap<u32, u32> = BTreeMap::new();
    for t in &terms {
        *counts.entry(ids.id(t)).or_default() += 1;
    }
    Analysis {
        len: terms.len(),
        tf: counts.into_iter().collect(),
        speaker_terms: words(speaker),
        question: text.trim_end().ends_with('?'),
        dates: day.map(|z| resolve(&text, z)).unwrap_or_default(),
        hidden: crate::schema::has_hidden_chars(&text),
        text,
    }
}

/// Terms by number, both ways.
#[derive(Default)]
struct Interner {
    ids: HashMap<String, u32>,
    names: Vec<String>,
}

impl Interner {
    fn id(&mut self, t: &str) -> u32 {
        if let Some(&i) = self.ids.get(t) {
            return i;
        }
        let i = self.names.len() as u32;
        self.names.push(t.to_string());
        self.ids.insert(t.to_string(), i);
        i
    }
}

/// Analyses by content: an episode's turns by the hash of the event that
/// recorded it, a claim by its id and recording time. Content never
/// changes, so entries never go stale; the cache is cleared when large.
type EpisodeTurns = Arc<Vec<(String, usize, usize, u32)>>;

/// Every analysed turn and claim lives once in `arena`; `postings` says,
/// for each term, which of them hold it and how often. A query then
/// touches only the entries holding its terms.
#[derive(Default)]
struct AnalysisCache {
    episodes: HashMap<(String, bool), EpisodeTurns>,
    claims: HashMap<(String, String, bool), u32>,
    arena: Vec<Arc<Analysis>>,
    terms: Interner,
    postings: Vec<Vec<(u32, u32)>>,
    size: usize,
    /// Estimated heap bytes held: the text, its term counts, the postings
    /// they add, speaker words and dates. The cache is cleared when this
    /// passes ANALYSIS_CACHE_BYTES, so a long-lived process over large or
    /// many records stays within a fixed amount of memory.
    bytes: usize,
}

impl AnalysisCache {
    fn add(&mut self, a: Analysis) -> u32 {
        let idx = self.arena.len() as u32;
        for &(t, f) in &a.tf {
            let t = t as usize;
            if self.postings.len() <= t {
                self.postings.resize_with(t + 1, Vec::new);
            }
            self.postings[t].push((idx, f));
        }
        self.bytes += a.text.len()
            + a.tf.len() * 16 // the term counts and the postings entries they add
            + a.speaker_terms.iter().map(|w| w.len() + 24).sum::<usize>()
            + a.dates.len() * 64
            + 160; // the Analysis, its Arc and the arena slot
        self.arena.push(Arc::new(a));
        self.size += 1;
        idx
    }
}

const ANALYSIS_CACHE_MAX: usize = 2_000_000; // turns and claims
/// The cache's memory ceiling. Counting entries alone let it grow by about
/// 90 MB for every 14,000 long messages (measured on ConvoMem), so a server
/// over a large record could run out of memory before reaching the count.
/// `GNOMON_RECALL_CACHE_MB` overrides it.
const ANALYSIS_CACHE_BYTES: usize = 512 << 20;

fn cache_bytes_limit() -> usize {
    static L: OnceLock<usize> = OnceLock::new();
    *L.get_or_init(|| {
        std::env::var("GNOMON_RECALL_CACHE_MB")
            .ok()
            .and_then(|v| v.trim().parse::<usize>().ok())
            .filter(|&mb| mb > 0)
            .map(|mb| mb << 20)
            .unwrap_or(ANALYSIS_CACHE_BYTES)
    })
}

fn analysis_cache() -> &'static Mutex<AnalysisCache> {
    static C: OnceLock<Mutex<AnalysisCache>> = OnceLock::new();
    C.get_or_init(|| Mutex::new(AnalysisCache::default()))
}

/// An episode's text and its pieces: the recorded turns, or paragraphs when
/// it has none (by its one participant, if it has exactly one).
fn pieces(e: &crate::ledger::EpisodeInfo) -> (Vec<char>, Vec<(String, usize, usize)>) {
    let content: Vec<char> = e
        .body
        .get("content")
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .chars()
        .collect();
    let p = if !e.turns.is_empty() {
        e.turns
            .iter()
            .map(|(sp, a, b)| (sp.clone(), *a, *b))
            .collect()
    } else {
        let who = if e.participants.len() == 1 {
            e.participants[0].clone()
        } else {
            String::new()
        };
        paragraphs(&content)
            .into_iter()
            .map(|(a, b)| (who.clone(), a, b))
            .collect()
    };
    (content, p)
}

fn slice(content: &[char], a: usize, b: usize) -> String {
    content[a.min(content.len())..b.min(content.len())]
        .iter()
        .collect()
}

fn units(
    ledger: &Ledger,
    visible: &[String],
    req: &Request,
    cache: &mut AnalysisCache,
) -> Vec<Unit> {
    let st = &ledger.state;
    let speaker_ok = |sp: &str| {
        req.speaker
            .as_deref()
            .map(|s| s.eq_ignore_ascii_case(sp))
            .unwrap_or(true)
    };
    let sees = |c: &str| visible.iter().any(|x| x == c);
    let in_time = |t: &str| {
        req.after.as_deref().map(|a| t >= a).unwrap_or(true)
            && req.before.as_deref().map(|b| t <= b).unwrap_or(true)
    };
    let joins = req.tuning.joins;
    let mut out = Vec::new();
    if req.turns {
        // Episodes in log order, so ties break by when things were recorded.
        let mut eps: Vec<(&String, &crate::ledger::EpisodeInfo)> = st
            .episodes
            .iter()
            .filter(|(_, e)| sees(&e.compartment))
            .collect();
        eps.sort_by_key(|(_, e)| e.seq);
        for (id, e) in eps {
            let hash = ledger
                .events
                .get(e.seq)
                .and_then(|ev| ev.get("hash"))
                .and_then(|h| h.as_str())
                .unwrap_or(id);
            let key = (hash.to_string(), joins);
            let turns = match cache.episodes.get(&key) {
                Some(t) => t.clone(),
                None => {
                    let (content, pieces) = pieces(e);
                    let day = day_of(&e.record_time);
                    let t: EpisodeTurns = Arc::new(
                        pieces
                            .into_iter()
                            .map(|(sp, a, b)| {
                                let an = analyse(
                                    slice(&content, a, b),
                                    &sp,
                                    day,
                                    joins,
                                    &mut cache.terms,
                                );
                                (sp, a, b, cache.add(an))
                            })
                            .collect(),
                    );
                    cache.episodes.insert(key, t.clone());
                    t
                }
            };
            for (n, (speaker, a, b, slot)) in turns.iter().enumerate() {
                out.push(Unit {
                    kind: "turn",
                    item: id.clone(),
                    event: id.clone(),
                    turn: n,
                    speaker: speaker.clone(),
                    time: e.record_time.clone(),
                    span: Some((*a, *b)),
                    a: cache.arena[*slot as usize].clone(),
                    slot: *slot,
                    speaker_is: e.bound(speaker).map(|w| w.to_string()),
                    wanted: in_time(&e.record_time) && speaker_ok(speaker),
                });
            }
        }
    }
    if req.claims {
        let mut claims: Vec<_> = st
            .claims
            .iter()
            .filter(|(_, c)| sees(&c.compartment))
            .collect();
        claims.sort_by_key(|(_, c)| c.seq);
        for (id, c) in claims {
            let speaker = c
                .body
                .get("speaker")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            let key = (id.clone(), c.record_time.clone(), joins);
            let slot = match cache.claims.get(&key) {
                Some(&s) => s,
                None => {
                    let text = c
                        .body
                        .get("statement")
                        .and_then(|s| s.as_str())
                        .unwrap_or("")
                        .to_string();
                    let an = analyse(
                        text,
                        &speaker,
                        day_of(&c.record_time),
                        joins,
                        &mut cache.terms,
                    );
                    let s = cache.add(an);
                    cache.claims.insert(key, s);
                    s
                }
            };
            out.push(Unit {
                kind: "claim",
                item: id.clone(),
                event: c.first_event.clone(),
                turn: 0,
                wanted: in_time(&c.record_time) && speaker_ok(&speaker),
                speaker,
                time: c.record_time.clone(),
                span: None,
                a: cache.arena[slot as usize].clone(),
                slot,
                speaker_is: st.speaker_of(c),
            });
        }
    }
    out
}

fn paragraphs(content: &[char]) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut i = 0;
    let n = content.len();
    while i < n {
        while i < n && content[i].is_whitespace() {
            i += 1;
        }
        if i >= n {
            break;
        }
        let start = i;
        let mut end = i;
        while i < n {
            if content[i] == '\n' {
                let mut j = i + 1;
                while j < n && content[j] != '\n' && content[j].is_whitespace() {
                    j += 1;
                }
                if j < n && content[j] == '\n' {
                    break;
                }
            }
            if !content[i].is_whitespace() {
                end = i + 1;
            }
            i += 1;
        }
        out.push((start, end));
    }
    out
}

// ---------------------------------------------------------------- ranking

/// BM25 over the units the reader can see, using the cache's postings:
/// a query term costs the number of entries holding it, not the size of
/// the memory.
struct Index<'a> {
    units: &'a [Unit],
    cache: &'a AnalysisCache,
    avgdl: f64,
    k1: f64,
    b: f64,
    /// Cache slot -> unit, for the units of this query.
    unit_of: HashMap<u32, usize>,
    postings: std::cell::RefCell<HashMap<String, std::rc::Rc<Vec<(usize, u32)>>>>,
}

impl<'a> Index<'a> {
    fn new(units: &'a [Unit], cache: &'a AnalysisCache, k1: f64, b: f64) -> Self {
        let total: usize = units.iter().map(|u| u.a.len).sum();
        let avgdl = if units.is_empty() {
            1.0
        } else {
            (total as f64 / units.len() as f64).max(1.0)
        };
        let unit_of = units.iter().enumerate().map(|(i, u)| (u.slot, i)).collect();
        Index {
            units,
            cache,
            avgdl,
            k1,
            b,
            unit_of,
            postings: Default::default(),
        }
    }

    fn postings(&self, t: &str) -> std::rc::Rc<Vec<(usize, u32)>> {
        if let Some(p) = self.postings.borrow().get(t) {
            return p.clone();
        }
        let p: Vec<(usize, u32)> = {
            let cache = self.cache;
            match cache.terms.ids.get(t) {
                Some(&id) => {
                    let mut v: Vec<(usize, u32)> = cache
                        .postings
                        .get(id as usize)
                        .map(|ps| {
                            ps.iter()
                                .filter_map(|&(slot, f)| self.unit_of.get(&slot).map(|&u| (u, f)))
                                .collect()
                        })
                        .unwrap_or_default();
                    v.sort_unstable();
                    v.dedup_by_key(|x| x.0);
                    v
                }
                None => Vec::new(),
            }
        };
        let p = std::rc::Rc::new(p);
        self.postings.borrow_mut().insert(t.to_string(), p.clone());
        p
    }

    fn idf(&self, t: &str) -> f64 {
        let n = self.units.len() as f64;
        let d = self.postings(t).len() as f64;
        (1.0 + (n - d + 0.5) / (d + 0.5)).ln()
    }

    /// BM25 of every unit for weighted query terms, with each term's share.
    fn score(&self, q: &[(String, f64)]) -> Vec<(f64, Vec<(String, f64)>)> {
        let mut out: Vec<(f64, Vec<(String, f64)>)> =
            (0..self.units.len()).map(|_| (0.0, Vec::new())).collect();
        for (t, w) in q {
            let idf = self.idf(t);
            for &(i, f) in self.postings(t).iter() {
                let dl = self.units[i].a.len as f64;
                let f = f as f64;
                let (k1, b) = (self.k1, self.b);
                let s = w * idf * f * (k1 + 1.0) / (f + k1 * (1.0 - b + b * dl / self.avgdl));
                out[i].0 += s;
                out[i].1.push((t.clone(), s));
            }
        }
        out
    }
}

/// Ranked recall over the memory as the reader sees it.
pub fn run(ledger: &Ledger, visible: &[String], req: &Request) -> GateResult<Value> {
    let seq_of: HashMap<&str, usize> = ledger
        .events
        .iter()
        .enumerate()
        .filter_map(|(i, e)| e.get("event_id").and_then(|v| v.as_str()).map(|id| (id, i)))
        .collect();
    // The analysis cache is held for the whole question: its slots and
    // postings stay consistent with the units built from it.
    let mut guard = analysis_cache().lock().unwrap_or_else(|p| p.into_inner());
    if guard.size > ANALYSIS_CACHE_MAX || guard.bytes > cache_bytes_limit() {
        *guard = AnalysisCache::default();
    }
    let units = units(ledger, visible, req, &mut guard);
    let cache: &AnalysisCache = &guard;
    let index = Index::new(
        &units,
        cache,
        req.tuning.k1 as f64 / 1000.0,
        req.tuning.b as f64 / 1000.0,
    );
    let tune = req.tuning;

    // A speaker named in the question ("What did Caroline paint?") is who
    // spoke, not a word to find: their turns are favoured, and the name
    // stops counting as a term (people are mostly named when addressed). A
    // speaker is named by their whole name, or by their first name written
    // with a capital. A question that is nothing but names keeps them.
    let qwords = words_cased(&req.query);
    let lower: Vec<&str> = qwords.iter().map(|(w, _)| w.as_str()).collect();
    let mut named: HashSet<Vec<String>> = HashSet::new();
    if tune.speaker_prior {
        let mut seen_names: HashSet<&str> = HashSet::new();
        let speakers: BTreeSet<&Vec<String>> = units
            .iter()
            .filter(|u| seen_names.insert(u.speaker.as_str()))
            .map(|u| &u.a.speaker_terms)
            .filter(|s| !s.is_empty())
            .collect();
        for sp in speakers {
            let whole = lower
                .windows(sp.len())
                .any(|w| w.iter().zip(sp.iter()).all(|(a, b)| *a == b));
            let first = sp[0].chars().count() >= 2
                && !STOP.contains(&sp[0].as_str())
                && qwords.iter().any(|(w, cap)| *cap && *w == sp[0]);
            if whole || first {
                named.insert(sp.clone());
            }
        }
    }
    let mut q_terms: Vec<String> = if tune.joins {
        terms_joined(&req.query)
    } else {
        terms(&req.query)
    };
    if !named.is_empty() {
        let named_stems: HashSet<String> = named.iter().flatten().map(|w| stem(w)).collect();
        let rest: Vec<String> = q_terms
            .iter()
            .filter(|t| !named_stems.contains(*t))
            .cloned()
            .collect();
        if !rest.is_empty() {
            q_terms = rest;
        }
    }
    q_terms.sort();
    q_terms.dedup();
    let mut q: Vec<(String, f64)> = q_terms.iter().map(|t| (t.clone(), 1.0)).collect();

    // The question's own moment: "last weekend" asked on a given day.
    let window: Vec<(String, Span)> = req
        .now
        .as_deref()
        .and_then(day_of)
        .map(|z| resolve(&req.query, z))
        .unwrap_or_default();

    let (prop_q, prop_n) = (tune.prop_q as f64 / 1000.0, tune.prop_n as f64 / 1000.0);
    let (speaker_boost, window_boost) = (tune.speaker as f64 / 1000.0, tune.window as f64 / 1000.0);
    let (session_w, when_boost) = (
        tune.session as f64 / 1000.0,
        1.0 + tune.when as f64 / 1000.0,
    );
    let asks_when = qwords.first().map(|(w, _)| w == "when").unwrap_or(false)
        || lower
            .windows(2)
            .any(|w| w == ["what", "date"] || w == ["what", "time"] || w == ["how", "long"]);
    type Ranked = Vec<(f64, Vec<(String, f64)>, Vec<(&'static str, f64)>)>;
    let rank = |q: &[(String, f64)]| -> Ranked {
        let mut s: Ranked = index
            .score(q)
            .into_iter()
            .map(|(x, p)| (x, p, Vec::new()))
            .collect();
        if tune.propagate {
            // Answers follow questions: a turn takes half the score of a
            // question just before it, and a quarter of any other neighbour.
            // Neighbours are the turns next to it in its conversation; the
            // share is listed among the matches as "(neighbours)".
            let base: Vec<f64> = s.iter().map(|x| x.0).collect();
            for i in 0..units.len() {
                if units[i].kind != "turn" {
                    continue;
                }
                let next_to = |j: usize, before: bool| {
                    units[j].kind == "turn"
                        && units[j].item == units[i].item
                        && if before {
                            units[j].turn + 1 == units[i].turn
                        } else {
                            units[i].turn + 1 == units[j].turn
                        }
                };
                let mut extra = 0.0;
                if i > 0 && next_to(i - 1, true) {
                    extra += base[i - 1]
                        * if units[i - 1].a.question {
                            prop_q
                        } else {
                            prop_n
                        };
                }
                if i + 1 < units.len() && next_to(i + 1, false) {
                    extra += base[i + 1] * prop_n;
                }
                if extra > 0.0 {
                    s[i].0 += extra;
                    s[i].1.push(("(neighbours)".to_string(), extra));
                }
            }
        }
        for (i, u) in units.iter().enumerate() {
            if s[i].0 <= 0.0 {
                continue;
            }
            if named.contains(&u.a.speaker_terms) {
                s[i].0 *= speaker_boost;
                s[i].2
                    .push(("speaker named in the question", speaker_boost));
            }
            if !window.is_empty() {
                let day = day_of(&u.time);
                let hit = window.iter().any(|(_, w)| {
                    day.map(|d| d >= w.first && d <= w.last).unwrap_or(false)
                        || u.a
                            .dates
                            .iter()
                            .any(|(_, sp)| sp.first <= w.last && sp.last >= w.first)
                });
                if hit {
                    s[i].0 *= window_boost;
                    s[i].2.push(("within the question's dates", window_boost));
                }
            }
            if asks_when && !u.a.dates.is_empty() {
                s[i].0 *= when_boost;
                s[i].2
                    .push(("holds a date, and the question asks when", when_boost));
            }
        }
        if session_w > 0.0 {
            // A turn gains from its whole conversation matching: evidence
            // for one question often spans a session.
            let mut agg: HashMap<&str, (f64, usize)> = HashMap::new();
            for (i, u) in units.iter().enumerate() {
                if u.kind == "turn" {
                    let e = agg.entry(u.item.as_str()).or_default();
                    e.0 += s[i].0;
                    e.1 += 1;
                }
            }
            let top = agg
                .values()
                .map(|(x, n)| x / (*n as f64).sqrt())
                .fold(0.0, f64::max);
            if top > 0.0 {
                for (i, u) in units.iter().enumerate() {
                    if u.kind == "turn" && s[i].0 > 0.0 {
                        let (x, n) = agg[u.item.as_str()];
                        let m = 1.0 + session_w * (x / (n as f64).sqrt()) / top;
                        s[i].0 *= m;
                        s[i].2.push(("its conversation matches as a whole", m));
                    }
                }
            }
        }
        s
    };

    let mut scores = rank(&q);
    if tune.prf && !q.is_empty() {
        // RM3-style feedback: the 10 best first results vote for terms by
        // tf x idf; the 10 strongest new terms join the question at 0.3.
        let mut order: Vec<usize> = (0..units.len()).filter(|&i| scores[i].0 > 0.0).collect();
        order.sort_by(|&a, &b| {
            scores[b]
                .0
                .partial_cmp(&scores[a].0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.cmp(&b))
        });
        let mut votes: BTreeMap<String, f64> = BTreeMap::new();
        for &i in order.iter().take(10) {
            let len = units[i].a.len.max(1) as f64;
            let names: Vec<(String, u32)> = units[i]
                .a
                .tf
                .iter()
                .map(|&(t, f)| (cache.terms.names[t as usize].clone(), f))
                .collect();
            for (t, f) in names {
                if !q_terms.iter().any(|x| *x == t) {
                    let w = f as f64 / len * index.idf(&t);
                    *votes.entry(t).or_default() += w;
                }
            }
        }
        let mut cand: Vec<(String, f64)> = votes.into_iter().collect();
        cand.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.0.cmp(&b.0))
        });
        for (t, _) in cand.into_iter().take(10) {
            q.push((t, 0.3));
        }
        scores = rank(&q);
    }

    // Rank first, then do the per-hit work for the top k only.
    let mut order: Vec<(f64, usize)> = (0..units.len())
        .filter(|&i| scores[i].0 > 0.0 && units[i].wanted)
        .map(|i| (scores[i].0, i))
        .collect();
    order.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.1.cmp(&b.1))
    });
    if tune.diverse < 1000 && !order.is_empty() {
        // Greedy: each pick keeps `diverse` of its score per hit already
        // taken from its conversation. Only the best 4k are considered.
        let d = tune.diverse as f64 / 1000.0;
        let mut pool: Vec<(f64, usize)> = order.iter().take(4 * req.k.max(1)).cloned().collect();
        let mut taken: HashMap<&str, i32> = HashMap::new();
        let mut out = Vec::with_capacity(req.k);
        while out.len() < req.k && !pool.is_empty() {
            let (best, _) = pool
                .iter()
                .enumerate()
                .map(|(j, &(x, i))| {
                    (
                        j,
                        x * d.powi(*taken.get(units[i].item.as_str()).unwrap_or(&0)),
                    )
                })
                .fold(
                    (0usize, f64::MIN),
                    |acc, (j, x)| if x > acc.1 { (j, x) } else { acc },
                );
            let (x, i) = pool.remove(best);
            *taken.entry(units[i].item.as_str()).or_default() += 1;
            out.push((x, i));
        }
        order = out;
    }
    order.truncate(req.k);
    let mut scored: Vec<(f64, usize, Vec<(String, f64)>, bool)> = Vec::new();
    for (score, i) in order {
        let u = &units[i];
        let matched = std::mem::take(&mut scores[i].1);
        let in_window = !window.is_empty() && {
            let day = day_of(&u.time);
            window.iter().any(|(_, w)| {
                day.map(|d| d >= w.first && d <= w.last).unwrap_or(false)
                    || u.a
                        .dates
                        .iter()
                        .any(|(_, sp)| sp.first <= w.last && sp.last >= w.first)
            })
        };
        scored.push((score, i, matched, in_window));
    }
    let q = q_terms;

    let event_ref = |id: &str| -> Value {
        match seq_of.get(id) {
            Some(&s) => {
                let e = &ledger.events[s];
                Value::obj(vec![
                    ("event", e.get("event_id").cloned().unwrap_or(Value::Null)),
                    ("kind", e.get("kind").cloned().unwrap_or(Value::Null)),
                    ("hash", e.get("hash").cloned().unwrap_or(Value::Null)),
                ])
            }
            None => Value::Null,
        }
    };
    let dates_json = |d: &[(String, Span)]| -> Value {
        Value::Array(
            d.iter()
                .map(|(p, s)| {
                    Value::obj(vec![
                        ("phrase", Value::str(p.clone())),
                        ("date", Value::str(s.text())),
                    ])
                })
                .collect(),
        )
    };
    let mut hits = Vec::new();
    for (score, i, matched, in_window) in scored {
        let u = &units[i];
        let mut fields = vec![
            ("kind", Value::str(u.kind)),
            (
                "id",
                Value::str(if u.kind == "turn" {
                    format!("{}#{}", u.item, u.turn)
                } else {
                    u.item.clone()
                }),
            ),
            ("source", event_ref(&u.event)),
            ("speaker", Value::str(u.speaker.clone())),
            (
                "speaker_is",
                u.speaker_is.clone().map(Value::str).unwrap_or(Value::Null),
            ),
            ("time", Value::str(u.time.clone())),
            ("text", Value::str(u.a.text.clone())),
            // Scores as integers (millionths): JSON here has no fractions.
            ("score", Value::Int((score * 1e6).round() as i64)),
            (
                "matched",
                Value::Array(
                    matched
                        .iter()
                        .map(|(t, s)| {
                            Value::Array(vec![
                                Value::str(t.clone()),
                                Value::Int((s * 1e6).round() as i64),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("dates", dates_json(&u.a.dates)),
            ("in_window", Value::Bool(in_window)),
            (
                "boosts",
                Value::Array(
                    scores[i]
                        .2
                        .iter()
                        .map(|(why, m)| {
                            Value::obj(vec![
                                ("why", Value::str(*why)),
                                ("times_thousandths", Value::Int((m * 1000.0).round() as i64)),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("hidden_chars", Value::Bool(u.a.hidden)),
        ];
        if let Some((a, b)) = u.span {
            fields.push(("episode", Value::str(u.item.clone())));
            fields.push(("turn", Value::Int(u.turn as i64)));
            fields.push(("start", Value::Int(a as i64)));
            fields.push(("end", Value::Int(b as i64)));
            if req.context > 0 {
                // The turns around it as recorded, whatever the filters.
                let ctx: Vec<Value> = match ledger.state.episodes.get(&u.item) {
                    Some(e) => {
                        let (content, ps) = pieces(e);
                        ps.into_iter()
                            .enumerate()
                            .filter(|(n, _)| {
                                *n != u.turn
                                    && n + req.context >= u.turn
                                    && *n <= u.turn + req.context
                            })
                            .map(|(n, (sp, a, b))| {
                                let text = slice(&content, a, b);
                                Value::obj(vec![
                                    ("turn", Value::Int(n as i64)),
                                    ("speaker", Value::str(sp)),
                                    (
                                        "hidden_chars",
                                        Value::Bool(crate::schema::has_hidden_chars(&text)),
                                    ),
                                    ("text", Value::str(text)),
                                ])
                            })
                            .collect()
                    }
                    None => Vec::new(),
                };
                fields.push(("context", Value::Array(ctx)));
            }
        }
        hits.push(Value::obj(fields));
    }
    let query_terms = Value::Array(q.iter().map(|t| Value::str(t.clone())).collect());
    Ok(Value::obj(vec![
        ("hits", Value::Array(hits)),
        ("query_terms", query_terms),
        ("query_dates", dates_json(&window)),
        (
            "searched",
            Value::Int(units.iter().filter(|u| u.wanted).count() as i64),
        ),
    ]))
}
