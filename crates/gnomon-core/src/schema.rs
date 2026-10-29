//! Claim schema v0.1: event kinds, body validation and vocabulary.
//!
//! Every enumerated vocabulary here comes from the design: statement kinds
//! (Plexus), origin status (Axiom evidence classes plus "guessed"), stance
//! (decision vs suggestion vs hypothetical, never collapsed), four-valued
//! truth, and the four supersession kinds.

use crate::json::Value;

/// The current protocol version. Version 2 (M6) requires models and tools
/// to state their model and operator and resolves speakers strictly (see
/// section 2a of the spec); version 1 logs replay under the rules they were
/// written with, and move to version 2 with a `schema_change`.
pub const SCHEMA_VERSION: i64 = 6;

pub const STATEMENT_KINDS: &[&str] =
    &["describe", "promise", "permit", "command", "ask", "imagine"];
pub const ORIGINS: &[&str] = &[
    "direct", "manual", "observed", "forecast", "derived", "inferred", "guessed",
];
pub const STANCES: &[&str] = &["asserted", "decided", "suggested", "hypothetical", "asked"];
pub const TRUTH_VALUES: &[&str] = &["true", "false", "contested", "unknown"];
pub const SUPERSESSION_KINDS: &[&str] = &["refuted", "narrowed", "generalised", "contested"];
pub const VERDICT_RESULTS: &[&str] = &["supports", "refutes", "abstain"];
pub const VERDICT_METHODS: &[&str] = &[
    "test",
    "cross_model_review",
    "external_source",
    "scored_prediction",
    "human",
    // Worked out exactly by a native tool (arithmetic, calendar, solver).
    "computed",
];
pub const WRITER_KINDS: &[&str] = &["human", "model", "tool"];
pub const CAPABILITIES: &[&str] = &["owner", "write", "verify", "govern", "spawn", "schedule"];
pub const EVENT_KINDS: &[&str] = &[
    "register",
    "position",
    "appoint",
    "dismiss",
    "episode",
    "claim",
    "verdict",
    "supersede",
    "resolve",
    "appeal",
    "schema_change",
    "handoff",
    "revoke",
    "lease",
    "release",
];
/// Contest rulings: old, new, both. Appeal rulings: reinstated, upheld.
/// Either may be withdrawn by the party that raised it.
pub const RESOLVE_OUTCOMES: &[&str] = &["old", "new", "both", "reinstated", "upheld", "withdrawn"];

pub const MAX_TEXT: usize = 100_000;
pub const MAX_SHORT: usize = 2_000;
/// Highest confidence for a claim whose statement paraphrases its quote.
pub const PARAPHRASE_MAX_CONFIDENCE: i64 = 7_000;
/// Confidence is an integer in basis points, never a float.
pub const MAX_CONFIDENCE: i64 = 10_000;

/// Gate error: a stable machine-readable code plus a human message.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GateError {
    pub code: &'static str,
    pub message: String,
}

impl GateError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        GateError {
            code,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for GateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

pub type GateResult<T> = Result<T, GateError>;

fn body_err(msg: impl Into<String>) -> GateError {
    GateError::new("E_BODY", msg)
}

/// Characters refused in structured fields: every Unicode format character
/// (general category Cf), noncharacters, private-use characters, and other
/// characters that render invisibly or can smuggle data (variation
/// selectors, fillers, blank patterns, combining grapheme joiner, object
/// replacement, invisible musical symbols).
pub fn is_hidden_char(c: char) -> bool {
    let u = c as u32;
    // Noncharacters: U+FDD0..FDEF and the last two code points of each plane.
    if (0xFDD0..=0xFDEF).contains(&u) || (u & 0xFFFE) == 0xFFFE {
        return true;
    }
    matches!(u,
        // Cf (format) characters, Unicode 15.1
        0x00AD | 0x0600..=0x0605 | 0x061C | 0x06DD | 0x070F | 0x0890..=0x0891
        | 0x08E2 | 0x180E | 0x200B..=0x200F | 0x202A..=0x202E | 0x2060..=0x2064
        | 0x2066..=0x206F | 0xFEFF | 0xFFF9..=0xFFFB | 0x110BD | 0x110CD
        | 0x13430..=0x1343F | 0x1BCA0..=0x1BCA3 | 0x1D173..=0x1D17A
        | 0xE0001 | 0xE0020..=0xE007F
        // Private use
        | 0xE000..=0xF8FF | 0xF0000..=0xFFFFD | 0x100000..=0x10FFFD
        // Other invisible or smuggling-capable characters
        | 0x034F | 0x115F | 0x1160 | 0x17B4 | 0x17B5 | 0x180B..=0x180D | 0x180F
        | 0x2028 | 0x2029 | 0x2800 | 0x3164 | 0xFE00..=0xFE0F | 0xFFA0 | 0xFFFC
        | 0x1D159 | 0xE0000 | 0xE0002..=0xE001F | 0xE0100..=0xE01EF)
}

/// Control characters (C0, DEL, C1) other than newline and tab.
pub fn is_disallowed_control(c: char) -> bool {
    let u = c as u32;
    (u < 0x20 && c != '\n' && c != '\t') || (0x7F..=0x9F).contains(&u)
}

pub fn has_hidden_chars(s: &str) -> bool {
    s.chars().any(|c| {
        if c.is_ascii() {
            // The only ASCII ones are controls (none is a format or
            // private-use character).
            (c < ' ' && c != '\n' && c != '\t') || c == '\u{7f}'
        } else {
            is_hidden_char(c) || is_disallowed_control(c)
        }
    })
}

/// Identifier for writers, parties and compartments: lowercase ASCII,
/// digits and hyphens, 1-32 chars, starting with a letter or digit.
/// No '/' so that "writer/seq" event ids can never collide across writers.
pub fn is_ident(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 32
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
}

/// Exact RFC 3339 UTC form `YYYY-MM-DDTHH:MM:SSZ`, so that string order is
/// time order.
pub fn is_timestamp(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 20 {
        return false;
    }
    let digit_at = |i: usize| b[i].is_ascii_digit();
    let shape = [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18]
        .iter()
        .all(|&i| digit_at(i))
        && b[4] == b'-'
        && b[7] == b'-'
        && b[10] == b'T'
        && b[13] == b':'
        && b[16] == b':'
        && b[19] == b'Z';
    if !shape {
        return false;
    }
    let num = |a: usize, n: usize| -> u32 { s[a..a + n].parse().unwrap_or(9999) };
    let (y, mo, d, h, mi, se) = (
        num(0, 4),
        num(5, 2),
        num(8, 2),
        num(11, 2),
        num(14, 2),
        num(17, 2),
    );
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let days_in_month = match mo {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return false,
    };
    y >= 1 && (1..=days_in_month).contains(&d) && h < 24 && mi < 60 && se < 60
}

pub fn is_hex64(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

// ------------------------------------------------------------ field checks

pub struct Fields<'a> {
    obj: &'a [(String, Value)],
    what: &'static str,
}

impl<'a> Fields<'a> {
    pub fn of(v: &'a Value, what: &'static str) -> GateResult<Self> {
        match v.as_object() {
            Some(obj) => Ok(Fields { obj, what }),
            None => Err(body_err(format!("{what} body must be an object"))),
        }
    }

    /// Reject members outside `allowed`: unknown fields are not silently kept.
    pub fn only(&self, allowed: &[&str]) -> GateResult<()> {
        for (k, _) in self.obj {
            if !allowed.contains(&k.as_str()) {
                return Err(body_err(format!("{}: unknown field '{k}'", self.what)));
            }
        }
        Ok(())
    }

    fn raw(&self, key: &str) -> Option<&'a Value> {
        self.obj.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    pub fn has(&self, key: &str) -> bool {
        self.raw(key).is_some()
    }

    pub fn text(&self, key: &str, max: usize, hidden_ok: bool) -> GateResult<&'a str> {
        let s = self
            .raw(key)
            .ok_or_else(|| body_err(format!("{}: missing '{key}'", self.what)))?
            .as_str()
            .ok_or_else(|| body_err(format!("{}: '{key}' must be a string", self.what)))?;
        if s.is_empty() {
            return Err(body_err(format!(
                "{}: '{key}' must not be empty",
                self.what
            )));
        }
        if s.chars().count() > max {
            return Err(body_err(format!("{}: '{key}' too long", self.what)));
        }
        if !hidden_ok && has_hidden_chars(s) {
            return Err(GateError::new(
                "E_HIDDEN_CHARS",
                format!(
                    "{}: '{key}' contains invisible or reordering characters",
                    self.what
                ),
            ));
        }
        Ok(s)
    }

    pub fn opt_text(&self, key: &str, max: usize) -> GateResult<Option<&'a str>> {
        if self.has(key) {
            Ok(Some(self.text(key, max, false)?))
        } else {
            Ok(None)
        }
    }

    pub fn one_of(&self, key: &str, allowed: &[&str]) -> GateResult<&'a str> {
        let s = self.text(key, MAX_SHORT, false)?;
        if allowed.contains(&s) {
            Ok(s)
        } else {
            Err(body_err(format!(
                "{}: '{key}' must be one of {:?}, got '{s}'",
                self.what, allowed
            )))
        }
    }

    pub fn ident(&self, key: &str) -> GateResult<&'a str> {
        let s = self.text(key, 32, false)?;
        if is_ident(s) {
            Ok(s)
        } else {
            Err(body_err(format!(
                "{}: '{key}' is not a valid identifier",
                self.what
            )))
        }
    }

    pub fn time(&self, key: &str) -> GateResult<Option<&'a str>> {
        match self.raw(key) {
            None => Ok(None),
            Some(v) => {
                let s = v.as_str().filter(|s| is_timestamp(s)).ok_or_else(|| {
                    GateError::new(
                        "E_TIME_FORMAT",
                        format!("{}: '{key}' must be YYYY-MM-DDTHH:MM:SSZ", self.what),
                    )
                })?;
                Ok(Some(s))
            }
        }
    }

    pub fn int_in(&self, key: &str, lo: i64, hi: i64) -> GateResult<i64> {
        let n = self
            .raw(key)
            .ok_or_else(|| body_err(format!("{}: missing '{key}'", self.what)))?
            .as_int()
            .ok_or_else(|| body_err(format!("{}: '{key}' must be an integer", self.what)))?;
        if n < lo || n > hi {
            return Err(body_err(format!(
                "{}: '{key}' = {n} outside {lo}..={hi}",
                self.what
            )));
        }
        Ok(n)
    }

    pub fn str_list(
        &self,
        key: &str,
        allowed: Option<&[&str]>,
        min: usize,
    ) -> GateResult<Vec<&'a str>> {
        let arr = self
            .raw(key)
            .ok_or_else(|| body_err(format!("{}: missing '{key}'", self.what)))?
            .as_array()
            .ok_or_else(|| body_err(format!("{}: '{key}' must be an array", self.what)))?;
        let mut out = Vec::new();
        for v in arr {
            let s = v
                .as_str()
                .ok_or_else(|| body_err(format!("{}: '{key}' items must be strings", self.what)))?;
            if has_hidden_chars(s) {
                return Err(GateError::new(
                    "E_HIDDEN_CHARS",
                    format!("{}: '{key}' item", self.what),
                ));
            }
            if let Some(allowed) = allowed {
                if !allowed.contains(&s) {
                    return Err(body_err(format!(
                        "{}: '{key}' item '{s}' not allowed",
                        self.what
                    )));
                }
            }
            if out.contains(&s) {
                return Err(body_err(format!(
                    "{}: duplicate '{s}' in '{key}'",
                    self.what
                )));
            }
            out.push(s);
        }
        if out.len() < min {
            return Err(body_err(format!(
                "{}: '{key}' needs at least {min} item(s)",
                self.what
            )));
        }
        Ok(out)
    }
}

// ------------------------------------------------------- body definitions

/// A writer is one acting identity: one person, or one running instance of
/// a model or tool (a session, an agent). Two sessions of the same model
/// are two writers: they say different things. What they share is recorded
/// too, because it matters when they agree.
pub struct Register<'a> {
    pub writer: &'a str,
    pub party: &'a str,
    pub kind: &'a str,
    pub capabilities: Vec<&'a str>,
    pub compartments: Vec<&'a str>,
    /// How to address it ("John Doe", "Claude").
    pub name: Option<&'a str>,
    /// Models: the model it runs, as its runtime reports it. Tools: their
    /// version, if any. Instances of one model make correlated mistakes.
    pub model: Option<&'a str>,
    /// Models and tools: the writer they act for (a registered human).
    pub operator: Option<&'a str>,
    /// Where it runs (e.g. "claude-code", "claude-app", "api").
    pub surface: Option<&'a str>,
    /// The runtime's own id for this session or agent, if it has one.
    pub session: Option<&'a str>,
    /// Genesis only: the protocol version the memory starts at (default 1).
    pub schema_version: Option<i64>,
}

/// An opaque identifier from outside (a model name, a session id): printable
/// ASCII without spaces, 1-128 characters, so it shows exactly as it is.
pub fn is_token(s: &str) -> bool {
    !s.is_empty() && s.len() <= 128 && s.bytes().all(|b| b.is_ascii_graphic())
}

/// `strict`: the identity rules of version 2 apply. A genesis registration
/// that starts the memory at version 2 is checked under them too.
pub fn register(body: &Value, strict: bool) -> GateResult<Register<'_>> {
    let f = Fields::of(body, "register")?;
    f.only(&[
        "writer",
        "party",
        "kind",
        "capabilities",
        "compartments",
        "name",
        "model",
        "operator",
        "surface",
        "session",
        "schema_version",
    ])?;
    let schema_version = if f.has("schema_version") {
        Some(f.int_in("schema_version", 1, SCHEMA_VERSION)?)
    } else {
        None
    };
    let strict = strict || schema_version.unwrap_or(1) >= 2;
    let compartments = f.str_list("compartments", None, 1)?;
    for c in &compartments {
        if !is_ident(c) {
            return Err(body_err(format!(
                "register: compartment '{c}' is not a valid identifier"
            )));
        }
    }
    let kind = f.one_of("kind", WRITER_KINDS)?;
    let token = |key: &str| -> GateResult<Option<&str>> {
        match f.opt_text(key, 128)? {
            Some(t) if !is_token(t) => Err(body_err(format!(
                "register: '{key}' must be printable ASCII without spaces"
            ))),
            other => Ok(other),
        }
    };
    let name = f.opt_text("name", 200)?;
    if let Some(n) = name {
        check_name(n, "register: name")?;
    }
    let model = token("model")?;
    let operator = if f.has("operator") {
        Some(f.ident("operator")?)
    } else {
        None
    };
    let surface = token("surface")?;
    let session = token("session")?;
    let capabilities = f.str_list("capabilities", Some(CAPABILITIES), 1)?;
    match kind {
        "human" => {
            if model.is_some() || operator.is_some() || surface.is_some() || session.is_some() {
                return Err(body_err(
                    "register: a human has no model, operator, surface or session",
                ));
            }
            if capabilities.contains(&"spawn") {
                return Err(body_err(
                    "register: a person has no instances: 'spawn' is for models and tools",
                ));
            }
        }
        _ if !strict => {}
        _ => {
            if operator.is_none() {
                return Err(body_err(format!(
                    "register: a {kind} must name its 'operator', the human it acts for"
                )));
            }
            if kind == "model" && model.is_none() {
                return Err(body_err(
                    "register: a model must name its 'model', as its runtime reports it",
                ));
            }
        }
    }
    Ok(Register {
        writer: f.ident("writer")?,
        party: f.ident("party")?,
        kind,
        capabilities,
        compartments,
        name,
        model,
        operator,
        surface,
        session,
        schema_version,
    })
}

/// A name must be told apart from other names by how it looks.
fn check_name(p: &str, what: &str) -> GateResult<()> {
    if p.is_empty() || p.chars().any(|c| c == '\n' || c == '\t' || c == '\r') {
        return Err(body_err(format!(
            "{what} {p:?} is empty or has a line break or tab"
        )));
    }
    if p.trim() != p {
        return Err(body_err(format!(
            "{what} {p:?} has leading or trailing spaces"
        )));
    }
    if p.chars().any(is_combining_mark) {
        return Err(body_err(format!(
            "{what} {p:?} has combining marks (use the composed form)"
        )));
    }
    if mixes_scripts(p) {
        return Err(body_err(format!(
            "{what} {p:?} mixes Latin, Greek and Cyrillic letters"
        )));
    }
    Ok(())
}

pub struct Episode<'a> {
    pub compartment: &'a str,
    pub content: &'a str,
    pub participants: Vec<&'a str>,
    /// Who said which part: (speaker, start, end) in code points, in order,
    /// not overlapping. Empty if the episode records no turn structure.
    pub turns: Vec<(&'a str, usize, usize)>,
    /// Which registered writer each name refers to, as the recorder says.
    /// A name without a binding is just a name.
    pub bindings: Vec<(&'a str, &'a str)>,
}

/// Episodes are kept whole and losslessly: hidden characters are allowed in
/// `content` (they are flagged in state, never stripped or rejected).
/// Combining marks: a name written with them ("Zoe" + U+0308) renders like
/// its composed form ("Zoë"), so two such names could not be told apart.
fn is_combining_mark(c: char) -> bool {
    matches!(c as u32, 0x0300..=0x036F | 0x1AB0..=0x1AFF | 0x1DC0..=0x1DFF | 0x20D0..=0x20FF | 0xFE20..=0xFE2F)
}

/// A name using letters from more than one of Latin, Greek and Cyrillic is
/// the classic look-alike ("Аlex" with a Cyrillic А).
fn mixes_scripts(name: &str) -> bool {
    let (mut latin, mut greek, mut cyrillic) = (false, false, false);
    for c in name.chars().filter(|c| c.is_alphabetic()) {
        match c as u32 {
            0x0041..=0x024F | 0x1E00..=0x1EFF => latin = true,
            0x0370..=0x03FF | 0x1F00..=0x1FFF => greek = true,
            0x0400..=0x052F => cyrillic = true,
            _ => {}
        }
    }
    (latin as u8 + greek as u8 + cyrillic as u8) > 1
}

/// Case-insensitive comparison key, including the common full case folds
/// that lowercase alone misses ("Straße" and "STRASSE").
fn fold(name: &str) -> String {
    name.to_lowercase().replace('ß', "ss").replace('ς', "σ")
}

pub fn episode(body: &Value) -> GateResult<Episode<'_>> {
    let f = Fields::of(body, "episode")?;
    f.only(&[
        "compartment",
        "content",
        "medium",
        "participants",
        "turns",
        "bindings",
    ])?;
    f.text("medium", MAX_SHORT, false)?;
    let participants = f.str_list("participants", None, 0)?;
    // Speakers must be told apart by more than letter case, and a name is a
    // name: no line breaks or tabs smuggling a second one in.
    for (i, p) in participants.iter().enumerate() {
        check_name(p, "episode: participant name")?;
        if participants[..i].iter().any(|q| fold(q) == fold(p)) {
            return Err(body_err(format!(
                "episode: participants '{p}' differ only in letter case"
            )));
        }
    }
    let content = f.text("content", MAX_TEXT, true)?;
    let mut turns = Vec::new();
    if let Some(raw) = f.raw("turns") {
        let items = raw
            .as_array()
            .ok_or_else(|| body_err("episode: 'turns' must be an array"))?;
        let len = content.chars().count();
        let mut prev_end = 0usize;
        for item in items {
            let t = Fields::of(item, "episode turn")?;
            t.only(&["speaker", "start", "end"])?;
            let speaker = t.text("speaker", MAX_SHORT, false)?;
            let start = t.int_in("start", 0, MAX_TEXT as i64)? as usize;
            let end = t.int_in("end", 0, MAX_TEXT as i64)? as usize;
            if !participants.contains(&speaker) {
                return Err(body_err(format!(
                    "episode: turn speaker '{speaker}' is not a participant"
                )));
            }
            if start >= end || end > len || start < prev_end {
                return Err(body_err("episode: turns must be non-empty, in order, not overlapping, and within the content"));
            }
            prev_end = end;
            turns.push((speaker, start, end));
        }
    }
    let mut bindings = Vec::new();
    if let Some(raw) = f.raw("bindings") {
        let obj = raw
            .as_object()
            .ok_or_else(|| body_err("episode: 'bindings' must be an object"))?;
        for (label, w) in obj {
            let w = w
                .as_str()
                .ok_or_else(|| body_err("episode: each binding must name a writer"))?;
            if !participants.contains(&label.as_str()) {
                return Err(body_err(format!(
                    "episode: binding for '{label}', who is not a participant"
                )));
            }
            if !is_ident(w) {
                return Err(body_err(format!(
                    "episode: binding to '{w}', which is not a writer id"
                )));
            }
            bindings.push((label.as_str(), w));
        }
    }
    Ok(Episode {
        compartment: f.ident("compartment")?,
        content,
        participants,
        turns,
        bindings,
    })
}

pub struct Claim<'a> {
    pub compartment: &'a str,
    pub statement: &'a str,
    pub speaker: &'a str,
    pub origin: &'a str,
    pub confidence: i64,
    pub truth: &'a str,
    pub source_episode: Option<&'a str>,
    /// Exact passage of the source episode the claim was taken from, and
    /// where it starts (in Unicode code points). The gate checks it.
    pub quote: Option<(&'a str, i64)>,
    pub stance: &'a str,
    /// What this claim is about: other claims, each with how it relates.
    /// Because a claim's id is the hash of its body, references can only
    /// point back in time: the threads they form never loop.
    pub about: Vec<(&'a str, &'a str)>,
}

/// How a claim can relate to an earlier one. A verdict judges a claim; these
/// discuss it, and change no one's truth.
pub const RELATIONS: &[&str] = &["answers", "critiques", "replies", "cites"];
pub const MAX_ABOUT: usize = 32;

pub const CLAIM_FIELDS: &[&str] = &[
    "statement",
    "statement_kind",
    "origin",
    "stance",
    "speaker",
    "truth",
    "confidence",
    "frame",
    "lease",
    "validity_domain",
    "valid_from",
    "valid_to",
    "source_episode",
    "quote",
    "quote_start",
    "compartment",
    "intended",
    "delivered",
    "observed",
    "about",
];

pub fn claim(body: &Value) -> GateResult<Claim<'_>> {
    let f = Fields::of(body, "claim")?;
    f.only(CLAIM_FIELDS)?;
    let statement = f.text("statement", MAX_TEXT, false)?;
    f.one_of("statement_kind", STATEMENT_KINDS)?;
    let origin = f.one_of("origin", ORIGINS)?;
    let stance = f.one_of("stance", STANCES)?;
    let speaker = f.text("speaker", MAX_SHORT, false)?;
    let truth = f.one_of("truth", TRUTH_VALUES)?;
    let confidence = f.int_in("confidence", 0, MAX_CONFIDENCE)?;
    f.text("frame", MAX_SHORT, false)?;
    f.opt_text("validity_domain", MAX_SHORT)?;
    f.opt_text("intended", MAX_SHORT)?;
    f.opt_text("delivered", MAX_SHORT)?;
    f.opt_text("observed", MAX_SHORT)?;
    let from = f.time("valid_from")?;
    let to = f.time("valid_to")?;
    if let (Some(a), Some(b)) = (from, to) {
        if a > b {
            return Err(body_err("claim: valid_from is after valid_to"));
        }
    }
    let source_episode = f.opt_text("source_episode", 64)?;
    // A quote and its position come together, and only with their source.
    let quote = match (f.has("quote"), f.has("quote_start")) {
        (false, false) => None,
        (true, true) => {
            if source_episode.is_none() {
                return Err(body_err("claim: a quote needs its source_episode"));
            }
            Some((
                f.text("quote", MAX_TEXT, false)?,
                f.int_in("quote_start", 0, MAX_TEXT as i64)?,
            ))
        }
        _ => return Err(body_err("claim: 'quote' and 'quote_start' go together")),
    };
    // "Direct" means the speaker's own words, verbatim: always quoted, and
    // the statement is the quote. Anything else attributed to a quote is an
    // interpretation, and cannot be stated with near-certainty.
    match quote {
        None if origin == "direct" => {
            return Err(GateError::new(
                "E_QUOTE",
                "claim: a direct claim must quote its source",
            ));
        }
        Some((q, _)) if origin == "direct" && q != statement => {
            return Err(GateError::new(
                "E_QUOTE",
                "claim: a direct claim must state exactly its quote",
            ));
        }
        Some((q, _)) if q != statement && confidence > PARAPHRASE_MAX_CONFIDENCE => {
            return Err(GateError::new(
                "E_QUOTE",
                format!("claim: a paraphrase of a quote cannot carry confidence above {PARAPHRASE_MAX_CONFIDENCE}"),
            ));
        }
        _ => {}
    }
    // A guess cannot be stated as certain, and a question has no truth yet.
    if origin == "guessed" && confidence == MAX_CONFIDENCE {
        return Err(body_err(
            "claim: a guessed claim cannot carry full confidence",
        ));
    }
    if stance == "asked" && truth != "unknown" {
        return Err(body_err("claim: a question must have truth 'unknown'"));
    }
    let mut about = Vec::new();
    if let Some(raw) = f.raw("about") {
        let items = raw
            .as_array()
            .ok_or_else(|| body_err("claim: 'about' must be a list"))?;
        if items.is_empty() || items.len() > MAX_ABOUT {
            return Err(body_err(format!(
                "claim: 'about' holds 1 to {MAX_ABOUT} references"
            )));
        }
        for item in items {
            let r = Fields::of(item, "claim reference")?;
            r.only(&["claim", "relation"])?;
            let target = r.text("claim", 80, false)?;
            if !(target.len() == 66 && target.starts_with("c:") && is_hex64(&target[2..])) {
                return Err(body_err(format!("claim: '{target}' is not a claim id")));
            }
            let relation = r.one_of("relation", RELATIONS)?;
            if about.contains(&(target, relation)) {
                return Err(body_err(
                    "claim: the same reference appears twice in 'about'",
                ));
            }
            if relation == "answers" && stance == "asked" {
                return Err(body_err("claim: an answer is not itself a question"));
            }
            if relation == "answers" && about.iter().any(|(_, r)| *r == "answers") {
                return Err(body_err("claim: an answer answers one question"));
            }
            about.push((target, relation));
        }
    }
    Ok(Claim {
        stance,
        about,
        compartment: f.ident("compartment")?,
        statement,
        speaker,
        origin,
        confidence,
        truth,
        source_episode,
        quote,
    })
}

/// Where a piece of work stands, written by whoever is doing it, so that
/// anyone (another session, another model, a person) can pick it up.
pub struct Handoff<'a> {
    pub compartment: &'a str,
    pub frame: &'a str,
    /// Claims and episodes it rests on, by id.
    pub refs: Vec<&'a str>,
    /// The handoff this one continues, if any.
    pub follows: Option<&'a str>,
}

pub const MAX_ITEMS: usize = 64;

/// One line of text: no line breaks or tabs, so it cannot pass itself off
/// as another line (another field, another writer) wherever it is shown.
/// Longest title of a position.
pub const MAX_TITLE: usize = 64;

/// A position in an organisation: its title, whom it reports to (none: it
/// answers to the organisation's owners), and its duties in one line.
pub struct Position<'a> {
    pub compartment: &'a str,
    pub org: &'a str,
    pub title: &'a str,
    pub reports_to: Option<&'a str>,
    pub duties: &'a str,
}

pub fn position(body: &Value) -> GateResult<Position<'_>> {
    let f = Fields::of(body, "position")?;
    f.only(&["compartment", "org", "title", "reports_to", "duties"])?;
    let title = f.text("title", MAX_TITLE, false)?;
    tidy_title(title, "position: 'title'")?;
    let reports_to = f.opt_text("reports_to", MAX_TITLE)?;
    if let Some(u) = reports_to {
        tidy_title(u, "position: 'reports_to'")?;
    }
    if reports_to == Some(title) {
        return Err(body_err("position: a position cannot report to itself"));
    }
    let duties = f.text("duties", 500, false)?;
    one_line(duties, "position: 'duties'")?;
    Ok(Position {
        compartment: f.ident("compartment")?,
        org: f.ident("org")?,
        title,
        reports_to,
        duties,
    })
}

/// An appointment of a writer to a position.
pub struct Appoint<'a> {
    pub compartment: &'a str,
    pub org: &'a str,
    pub title: &'a str,
    pub holder: &'a str,
}

pub fn appoint(body: &Value) -> GateResult<Appoint<'_>> {
    let f = Fields::of(body, "appoint")?;
    f.only(&["compartment", "org", "title", "holder"])?;
    let title = f.text("title", MAX_TITLE, false)?;
    Ok(Appoint {
        compartment: f.ident("compartment")?,
        org: f.ident("org")?,
        title,
        holder: f.ident("holder")?,
    })
}

/// The end of an appointment, with the reason in one line.
pub struct Dismiss<'a> {
    pub appointment: &'a str,
    pub reason: &'a str,
}

pub fn dismiss(body: &Value) -> GateResult<Dismiss<'_>> {
    let f = Fields::of(body, "dismiss")?;
    f.only(&["appointment", "reason"])?;
    let reason = f.text("reason", 500, false)?;
    one_line(reason, "dismiss: 'reason'")?;
    Ok(Dismiss {
        appointment: f.text("appointment", 100, false)?,
        reason,
    })
}

/// A title is one line with no leading, trailing or doubled spaces, so two
/// titles that look the same are the same.
fn tidy_title(s: &str, what: &str) -> GateResult<()> {
    one_line(s, what)?;
    if s.is_empty()
        || s.trim() != s
        || s.contains("  ")
        || s.chars().any(|c| c.is_whitespace() && c != ' ')
    {
        return Err(body_err(format!(
            "{what}: no leading, trailing, doubled or unusual spaces"
        )));
    }
    Ok(())
}

pub fn one_line(s: &str, what: &str) -> GateResult<()> {
    if s.chars().any(|c| matches!(c, '\n' | '\r' | '\t')) {
        return Err(body_err(format!(
            "{what} is one line: no line breaks or tabs"
        )));
    }
    Ok(())
}

pub fn handoff(body: &Value) -> GateResult<Handoff<'_>> {
    let f = Fields::of(body, "handoff")?;
    f.only(&[
        "compartment",
        "frame",
        "summary",
        "goal",
        "done",
        "next",
        "open",
        "refs",
        "artifacts",
        "follows",
        "lease",
    ])?;
    let frame = f.text("frame", MAX_SHORT, false)?;
    one_line(frame, "handoff: 'frame'")?;
    f.text("summary", MAX_TEXT, false)?;
    if let Some(g) = f.opt_text("goal", MAX_SHORT)? {
        one_line(g, "handoff: 'goal'")?;
    }
    // Lists are measured before anything else is done with them.
    let list = |key: &str| -> GateResult<Option<&[Value]>> {
        match f.raw(key) {
            None => Ok(None),
            Some(v) => {
                let a = v
                    .as_array()
                    .ok_or_else(|| body_err(format!("handoff: '{key}' must be a list")))?;
                if a.is_empty() || a.len() > MAX_ITEMS {
                    return Err(body_err(format!(
                        "handoff: '{key}' holds 1 to {MAX_ITEMS} items"
                    )));
                }
                Ok(Some(a))
            }
        }
    };
    for key in ["done", "next", "open"] {
        for item in list(key)?.unwrap_or(&[]) {
            let t = item
                .as_str()
                .ok_or_else(|| body_err(format!("handoff: items in '{key}' are text")))?;
            if t.is_empty() || t.chars().count() > MAX_SHORT {
                return Err(body_err(format!(
                    "handoff: each item in '{key}' is 1-{MAX_SHORT} characters"
                )));
            }
            if has_hidden_chars(t) {
                return Err(GateError::new(
                    "E_HIDDEN_CHARS",
                    format!("handoff: an item in '{key}' has hidden characters"),
                ));
            }
            one_line(t, &format!("handoff: each item in '{key}'"))?;
        }
    }
    let mut refs = Vec::new();
    if list("refs")?.is_some() {
        refs = f.str_list("refs", None, 1)?;
        let mut sorted = refs.clone();
        sorted.sort_unstable();
        sorted.dedup();
        if sorted.len() != refs.len() {
            return Err(body_err("handoff: an id appears twice in 'refs'"));
        }
    }
    if let Some(items) = list("artifacts")? {
        for item in items {
            let a = Fields::of(item, "handoff artifact")?;
            a.only(&["uri", "note", "sha256"])?;
            let uri = a.text("uri", MAX_SHORT, false)?;
            if uri.chars().any(char::is_whitespace) {
                return Err(body_err("handoff: an artifact's uri has no spaces"));
            }
            if let Some(n) = a.opt_text("note", MAX_SHORT)? {
                one_line(n, "handoff: an artifact's note")?;
            }
            if let Some(h) = a.opt_text("sha256", 64)? {
                if !is_hex64(h) {
                    return Err(body_err(
                        "handoff: an artifact's sha256 is 64 lower-case hex digits",
                    ));
                }
            }
        }
    }
    let follows = f.opt_text("follows", 80)?;
    Ok(Handoff {
        compartment: f.ident("compartment")?,
        frame,
        refs,
        follows,
    })
}

pub struct Verdict<'a> {
    pub claim: &'a str,
    pub result: &'a str,
}

pub fn verdict(body: &Value) -> GateResult<Verdict<'_>> {
    let f = Fields::of(body, "verdict")?;
    f.only(&["claim", "result", "method", "note"])?;
    f.one_of("method", VERDICT_METHODS)?;
    f.opt_text("note", MAX_TEXT)?;
    Ok(Verdict {
        claim: f.text("claim", 80, false)?,
        result: f.one_of("result", VERDICT_RESULTS)?,
    })
}

pub struct Supersede<'a> {
    pub old: &'a str,
    pub new: &'a str,
    pub kind: &'a str,
    pub evidence: Vec<&'a str>,
}

pub fn supersede(body: &Value) -> GateResult<Supersede<'_>> {
    let f = Fields::of(body, "supersede")?;
    f.only(&["old", "new", "kind", "evidence", "reason"])?;
    f.text("reason", MAX_TEXT, false)?;
    let kind = f.one_of("kind", SUPERSESSION_KINDS)?;
    let min_evidence = if kind == "contested" { 0 } else { 1 };
    let s = Supersede {
        old: f.text("old", 80, false)?,
        new: f.text("new", 80, false)?,
        kind,
        evidence: f.str_list("evidence", None, min_evidence)?,
    };
    if s.old == s.new {
        return Err(body_err("supersede: a claim cannot supersede itself"));
    }
    Ok(s)
}

pub struct Resolve<'a> {
    /// A contested supersession or an appeal (by event id).
    pub dispute: &'a str,
    pub outcome: &'a str,
}

pub fn resolve(body: &Value) -> GateResult<Resolve<'_>> {
    let f = Fields::of(body, "resolve")?;
    f.only(&["dispute", "outcome", "reason"])?;
    f.text("reason", MAX_TEXT, false)?;
    Ok(Resolve {
        dispute: f.text("dispute", 80, false)?,
        outcome: f.one_of("outcome", RESOLVE_OUTCOMES)?,
    })
}

pub struct Appeal<'a> {
    pub claim: &'a str,
    pub evidence: Vec<&'a str>,
}

pub fn appeal(body: &Value) -> GateResult<Appeal<'_>> {
    let f = Fields::of(body, "appeal")?;
    f.only(&["claim", "evidence", "reason"])?;
    f.text("reason", MAX_TEXT, false)?;
    Ok(Appeal {
        claim: f.text("claim", 80, false)?,
        evidence: f.str_list("evidence", None, 1)?,
    })
}

pub struct Revoke<'a> {
    pub writer: &'a str,
    pub compartments: Vec<&'a str>,
}

/// `revoke` (protocol version 5, issue #49): end a writer's access to
/// compartments from this point on.
pub fn revoke(body: &Value) -> GateResult<Revoke<'_>> {
    let f = Fields::of(body, "revoke")?;
    f.only(&["writer", "compartments", "reason"])?;
    f.text("reason", MAX_SHORT, false)?;
    Ok(Revoke {
        writer: f.text("writer", MAX_SHORT, false)?,
        compartments: f.str_list("compartments", None, 1)?,
    })
}

pub struct Lease<'a> {
    pub writer: &'a str,
    pub frame: &'a str,
    pub expires: &'a str,
}

/// `lease` (protocol version 6, issue #54): a time-bounded hold on a frame.
pub fn lease(body: &Value) -> GateResult<Lease<'_>> {
    let f = Fields::of(body, "lease")?;
    f.only(&["writer", "frame", "expires", "reason"])?;
    f.opt_text("reason", MAX_SHORT)?;
    let frame = f.text("frame", MAX_SHORT, false)?;
    one_line(frame, "lease: 'frame'")?;
    let expires = f.text("expires", 40, false)?;
    if !is_timestamp(expires) {
        return Err(body_err("lease: 'expires' must be a timestamp like 2026-10-29T12:00:00Z"));
    }
    Ok(Lease { writer: f.text("writer", MAX_SHORT, false)?, frame, expires })
}

/// `release` (protocol version 6): end a lease early.
pub fn release(body: &Value) -> GateResult<&str> {
    let f = Fields::of(body, "release")?;
    f.only(&["lease", "reason"])?;
    f.opt_text("reason", MAX_SHORT)?;
    f.text("lease", MAX_SHORT, false)
}

pub fn schema_change(body: &Value) -> GateResult<i64> {
    let f = Fields::of(body, "schema_change")?;
    f.only(&["version", "description"])?;
    f.text("description", MAX_TEXT, false)?;
    f.int_in("version", 1, 1_000_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamps() {
        assert!(is_timestamp("2026-10-14T22:43:00Z"));
        assert!(!is_timestamp("2026-13-14T22:43:00Z"));
        assert!(!is_timestamp("2026-10-14 22:43:00Z"));
        assert!(!is_timestamp("2026-10-14T22:43:00+10:00"));
        assert!(!is_timestamp("2026-02-31T00:00:00Z"));
        assert!(!is_timestamp("2025-02-29T00:00:00Z"));
        assert!(is_timestamp("2024-02-29T00:00:00Z"));
        assert!(!is_timestamp("1900-02-29T00:00:00Z"));
        assert!(is_timestamp("2000-02-29T00:00:00Z"));
        assert!(!is_timestamp("0000-04-30T00:00:00Z"));
        assert!(!is_timestamp("2026-04-31T00:00:00Z"));
        assert!(!is_timestamp("2026-10-14T24:00:00Z"));
    }

    #[test]
    fn idents() {
        assert!(is_ident("claude-c1"));
        assert!(!is_ident("Claude"));
        assert!(!is_ident("a/b"));
        assert!(!is_ident(""));
    }

    #[test]
    fn hidden_chars() {
        assert!(has_hidden_chars("a\u{200b}b"));
        assert!(has_hidden_chars("x\u{e0041}"));
        assert!(has_hidden_chars("\u{202e}evil"));
        assert!(!has_hidden_chars(
            "plain text, café, 日本\nsecond line\twith tab"
        ));
        for c in [
            '\u{61c}',
            '\u{34f}',
            '\u{fe0f}',
            '\u{e0100}',
            '\u{3164}',
            '\u{fff9}',
            '\u{1d173}',
            '\u{2028}',
            '\u{0}',
            '\u{1b}',
            '\u{85}',
            '\u{7f}',
            '\u{180b}',
            '\u{17b4}',
            '\u{600}',
            '\u{6dd}',
            '\u{70f}',
            '\u{110bd}',
            '\u{13430}',
            '\u{1bca0}',
            '\u{fffc}',
            '\u{2800}',
            '\u{1d159}',
            '\u{fffe}',
            '\u{e000}',
            '\u{10fffd}',
            '\u{fdd0}',
            '\u{1fffe}',
        ] {
            assert!(has_hidden_chars(&format!("a{c}b")), "{:x}", c as u32);
        }
    }
}
