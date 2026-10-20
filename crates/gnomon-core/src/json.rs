//! Strict JSON and canonical serialisation.
//!
//! Parsing is deliberately stricter than RFC 8259 so that the same claim can
//! only ever have one canonical form and one hash:
//!
//! * numbers must be integers in the I-JSON safe range (|n| <= 2^53 - 1);
//!   fractions, exponents, `-0`, NaN and infinities are rejected, never rounded
//!   (carries forward the overflow / non-finite lesson from The Field);
//! * duplicate object keys are rejected;
//! * lone surrogates and raw control characters in strings are rejected;
//! * nesting depth is bounded.
//!
//! Canonical output follows RFC 8785 (JCS) for this subset: object members
//! sorted by UTF-16 code units, minimal string escaping, no whitespace.

use std::fmt;

pub const MAX_DEPTH: usize = 64;
pub const MAX_SAFE_INT: i64 = (1i64 << 53) - 1;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Array(Vec<Value>),
    /// Members in source order; canonical output sorts them.
    Object(Vec<(String, Value)>),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JsonError {
    UnexpectedEnd,
    UnexpectedChar { at: usize, found: char },
    TrailingContent { at: usize },
    FloatNotAllowed { at: usize },
    NegativeZero { at: usize },
    LeadingZero { at: usize },
    IntegerOutOfSafeRange { at: usize },
    DuplicateKey { key: String },
    LoneSurrogate { at: usize },
    ControlCharInString { at: usize },
    BadEscape { at: usize },
    TooDeep,
}

impl fmt::Display for JsonError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}", self)
    }
}

impl std::error::Error for JsonError {}

impl Value {
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Object(m) => m.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_int(&self) -> Option<i64> {
        match self {
            Value::Int(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Value]> {
        match self {
            Value::Array(a) => Some(a),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&[(String, Value)]> {
        match self {
            Value::Object(m) => Some(m),
            _ => None,
        }
    }

    /// Object with `key` removed (no-op for other values).
    pub fn without(&self, key: &str) -> Value {
        match self {
            Value::Object(m) => Value::Object(
                m.iter()
                    .filter(|(k, _)| k != key)
                    .cloned()
                    .collect(),
            ),
            other => other.clone(),
        }
    }

    /// Object with `key` set to `value` (replacing any existing member).
    pub fn with(&self, key: &str, value: Value) -> Value {
        match self {
            Value::Object(m) => {
                let mut out: Vec<(String, Value)> =
                    m.iter().filter(|(k, _)| k != key).cloned().collect();
                out.push((key.to_string(), value));
                Value::Object(out)
            }
            other => other.clone(),
        }
    }

    pub fn str(s: impl Into<String>) -> Value {
        Value::Str(s.into())
    }

    pub fn obj(members: Vec<(&str, Value)>) -> Value {
        Value::Object(
            members
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        )
    }
}

// ---------------------------------------------------------------- parsing

pub fn parse(input: &str) -> Result<Value, JsonError> {
    let mut p = Parser {
        s: input.as_bytes(),
        src: input,
        i: 0,
    };
    p.ws();
    let v = p.value(0)?;
    p.ws();
    if p.i != p.s.len() {
        return Err(JsonError::TrailingContent { at: p.i });
    }
    Ok(v)
}

struct Parser<'a> {
    s: &'a [u8],
    src: &'a str,
    i: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }

    fn unexpected(&self) -> JsonError {
        match self.src[self.i..].chars().next() {
            Some(c) => JsonError::UnexpectedChar { at: self.i, found: c },
            None => JsonError::UnexpectedEnd,
        }
    }

    fn expect(&mut self, b: u8) -> Result<(), JsonError> {
        if self.peek() == Some(b) {
            self.i += 1;
            Ok(())
        } else {
            Err(self.unexpected())
        }
    }

    fn literal(&mut self, word: &[u8], v: Value) -> Result<Value, JsonError> {
        if self.s[self.i..].starts_with(word) {
            self.i += word.len();
            Ok(v)
        } else {
            Err(self.unexpected())
        }
    }

    fn value(&mut self, depth: usize) -> Result<Value, JsonError> {
        if depth > MAX_DEPTH {
            return Err(JsonError::TooDeep);
        }
        match self.peek() {
            None => Err(JsonError::UnexpectedEnd),
            Some(b'{') => self.object(depth),
            Some(b'[') => self.array(depth),
            Some(b'"') => Ok(Value::Str(self.string()?)),
            Some(b't') => self.literal(b"true", Value::Bool(true)),
            Some(b'f') => self.literal(b"false", Value::Bool(false)),
            Some(b'n') => self.literal(b"null", Value::Null),
            Some(b'-') | Some(b'0'..=b'9') => self.number(),
            // NaN / Infinity and anything else.
            Some(_) => Err(self.unexpected()),
        }
    }

    fn object(&mut self, depth: usize) -> Result<Value, JsonError> {
        self.expect(b'{')?;
        let mut members: Vec<(String, Value)> = Vec::new();
        self.ws();
        if self.peek() == Some(b'}') {
            self.i += 1;
            return Ok(Value::Object(members));
        }
        loop {
            self.ws();
            if self.peek() != Some(b'"') {
                return Err(self.unexpected());
            }
            let key = self.string()?;
            if members.iter().any(|(k, _)| *k == key) {
                return Err(JsonError::DuplicateKey { key });
            }
            self.ws();
            self.expect(b':')?;
            self.ws();
            let v = self.value(depth + 1)?;
            members.push((key, v));
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    return Ok(Value::Object(members));
                }
                _ => return Err(self.unexpected()),
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<Value, JsonError> {
        self.expect(b'[')?;
        let mut items = Vec::new();
        self.ws();
        if self.peek() == Some(b']') {
            self.i += 1;
            return Ok(Value::Array(items));
        }
        loop {
            self.ws();
            items.push(self.value(depth + 1)?);
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    return Ok(Value::Array(items));
                }
                _ => return Err(self.unexpected()),
            }
        }
    }

    fn number(&mut self) -> Result<Value, JsonError> {
        let start = self.i;
        let neg = self.peek() == Some(b'-');
        if neg {
            self.i += 1;
        }
        let digits_start = self.i;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.i += 1;
        }
        let digits = &self.src[digits_start..self.i];
        if digits.is_empty() {
            return Err(self.unexpected());
        }
        if matches!(self.peek(), Some(b'.') | Some(b'e') | Some(b'E')) {
            return Err(JsonError::FloatNotAllowed { at: start });
        }
        if digits.len() > 1 && digits.starts_with('0') {
            return Err(JsonError::LeadingZero { at: start });
        }
        if neg && digits == "0" {
            return Err(JsonError::NegativeZero { at: start });
        }
        if digits.len() > 16 {
            return Err(JsonError::IntegerOutOfSafeRange { at: start });
        }
        let magnitude: i64 = digits
            .parse()
            .map_err(|_| JsonError::IntegerOutOfSafeRange { at: start })?;
        if magnitude > MAX_SAFE_INT {
            return Err(JsonError::IntegerOutOfSafeRange { at: start });
        }
        Ok(Value::Int(if neg { -magnitude } else { magnitude }))
    }

    fn hex4(&mut self) -> Result<u32, JsonError> {
        if self.i + 4 > self.s.len() {
            return Err(JsonError::UnexpectedEnd);
        }
        // Check bytes before slicing the str: a multi-byte character inside
        // the four positions must be an error, not a char-boundary panic.
        if !self.s[self.i..self.i + 4].iter().all(u8::is_ascii_hexdigit) {
            return Err(JsonError::BadEscape { at: self.i });
        }
        let h = &self.src[self.i..self.i + 4];
        self.i += 4;
        Ok(u32::from_str_radix(h, 16).expect("validated hex"))
    }

    fn string(&mut self) -> Result<String, JsonError> {
        self.expect(b'"')?;
        let mut out = String::new();
        loop {
            let start = self.i;
            // Copy a run of plain characters at once.
            while let Some(b) = self.peek() {
                if b == b'"' || b == b'\\' || b < 0x20 {
                    break;
                }
                self.i += 1;
            }
            out.push_str(&self.src[start..self.i]);
            match self.peek() {
                None => return Err(JsonError::UnexpectedEnd),
                Some(b'"') => {
                    self.i += 1;
                    return Ok(out);
                }
                Some(b) if b < 0x20 => return Err(JsonError::ControlCharInString { at: self.i }),
                Some(b'\\') => {
                    let esc_at = self.i;
                    self.i += 1;
                    let c = self.peek().ok_or(JsonError::UnexpectedEnd)?;
                    self.i += 1;
                    match c {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let hi = self.hex4()?;
                            let cp = if (0xD800..0xDC00).contains(&hi) {
                                if !self.s[self.i..].starts_with(b"\\u") {
                                    return Err(JsonError::LoneSurrogate { at: esc_at });
                                }
                                self.i += 2;
                                let lo = self.hex4()?;
                                if !(0xDC00..0xE000).contains(&lo) {
                                    return Err(JsonError::LoneSurrogate { at: esc_at });
                                }
                                0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)
                            } else if (0xDC00..0xE000).contains(&hi) {
                                return Err(JsonError::LoneSurrogate { at: esc_at });
                            } else {
                                hi
                            };
                            out.push(char::from_u32(cp).ok_or(JsonError::BadEscape { at: esc_at })?);
                        }
                        _ => return Err(JsonError::BadEscape { at: esc_at }),
                    }
                }
                Some(_) => unreachable!(),
            }
        }
    }
}

// ------------------------------------------------------- canonical output

/// RFC 8785 canonical form of `v`.
pub fn canonical(v: &Value) -> String {
    let mut out = String::new();
    write_canonical(v, &mut out);
    out
}

/// RFC 8785 member order: by UTF-16 code units. For ASCII keys (nearly
/// all of them) that is byte order; otherwise the code units are compared
/// as they are produced, without allocating.
pub(crate) fn key_order(a: &str, b: &str) -> std::cmp::Ordering {
    if a.is_ascii() && b.is_ascii() {
        a.as_bytes().cmp(b.as_bytes())
    } else {
        a.encode_utf16().cmp(b.encode_utf16())
    }
}

fn write_canonical(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(n) => {
            use std::fmt::Write;
            let _ = write!(out, "{n}");
        }
        Value::Str(s) => write_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_canonical(item, out);
            }
            out.push(']');
        }
        Value::Object(members) => {
            let mut sorted: Vec<&(String, Value)> = members.iter().collect();
            // Parsed canonical input is already in order: check before sorting.
            if !sorted.windows(2).all(|w| key_order(&w[0].0, &w[1].0).is_le()) {
                sorted.sort_by(|a, b| key_order(&a.0, &b.0));
            }
            out.push('{');
            for (i, (k, val)) in sorted.into_iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_string(k, out);
                out.push(':');
                write_canonical(val, out);
            }
            out.push('}');
        }
    }
}

fn write_string(s: &str, out: &mut String) {
    out.push('"');
    // Runs of bytes that need no escaping are copied whole. Every byte that
    // does is ASCII, so a run never ends inside a character.
    let b = s.as_bytes();
    let mut run = 0;
    for (i, &x) in b.iter().enumerate() {
        if x >= 0x20 && x != b'"' && x != b'\\' {
            continue;
        }
        out.push_str(&s[run..i]);
        run = i + 1;
        let c = x as char;
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => {
                use std::fmt::Write;
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
        }
    }
    out.push_str(&s[run..]);
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc8785_key_order_uses_utf16() {
        // RFC 8785 section 3.2.3 sorting example.
        let input = r#"{"\u20ac":"Euro Sign","\r":"Carriage Return","\ufb33":"Hebrew Letter Dalet With Dagesh","1":"One","\ud83d\ude00":"Emoji: Grinning Face","\u0080":"Control","\u00f6":"Latin Small Letter O With Diaeresis"}"#;
        let v = parse(input).unwrap();
        let keys: Vec<String> = match &v {
            Value::Object(_) => {
                let c = canonical(&v);
                let reparsed = parse(&c).unwrap();
                reparsed
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(k, _)| k.clone())
                    .collect()
            }
            _ => unreachable!(),
        };
        let expected: Vec<String> = ["\r", "1", "\u{80}", "\u{f6}", "\u{20ac}", "\u{1f600}", "\u{fb33}"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(keys, expected);
    }

    #[test]
    fn canonical_is_stable_under_reordering_and_whitespace() {
        let a = parse(r#"{ "b": [1, 2, {"y": null, "x": true}], "a": "s" }"#).unwrap();
        let b = parse(r#"{"a":"s","b":[1,2,{"x":true,"y":null}]}"#).unwrap();
        assert_eq!(canonical(&a), canonical(&b));
        assert_eq!(canonical(&a), r#"{"a":"s","b":[1,2,{"x":true,"y":null}]}"#);
    }

    #[test]
    fn escaping_is_minimal() {
        let v = parse(r#""a\/b\u00e9\u001f\"\n""#).unwrap();
        assert_eq!(canonical(&v), "\"a/b\u{e9}\\u001f\\\"\\n\"");
    }

    #[test]
    fn rejects_floats_and_non_finite() {
        assert!(matches!(parse("1.5"), Err(JsonError::FloatNotAllowed { .. })));
        assert!(matches!(parse("1e3"), Err(JsonError::FloatNotAllowed { .. })));
        assert!(parse("NaN").is_err());
        assert!(parse("Infinity").is_err());
        assert!(parse("-Infinity").is_err());
        assert!(matches!(parse("-0"), Err(JsonError::NegativeZero { .. })));
        assert!(matches!(parse("01"), Err(JsonError::LeadingZero { .. })));
    }

    #[test]
    fn integers_outside_safe_range_are_rejected_not_rounded() {
        assert_eq!(parse("9007199254740991").unwrap(), Value::Int(MAX_SAFE_INT));
        assert_eq!(parse("-9007199254740991").unwrap(), Value::Int(-MAX_SAFE_INT));
        assert!(matches!(
            parse("9007199254740992"),
            Err(JsonError::IntegerOutOfSafeRange { .. })
        ));
        assert!(matches!(
            parse("99999999999999999999999999"),
            Err(JsonError::IntegerOutOfSafeRange { .. })
        ));
    }

    #[test]
    fn rejects_duplicates_surrogates_controls_depth() {
        assert!(matches!(parse(r#"{"a":1,"a":2}"#), Err(JsonError::DuplicateKey { .. })));
        assert!(matches!(parse(r#""\ud800""#), Err(JsonError::LoneSurrogate { .. })));
        assert!(matches!(parse(r#""\udc00""#), Err(JsonError::LoneSurrogate { .. })));
        assert!(matches!(parse("\"a\u{1}b\""), Err(JsonError::ControlCharInString { .. })));
        let deep = "[".repeat(MAX_DEPTH + 2) + &"]".repeat(MAX_DEPTH + 2);
        assert_eq!(parse(&deep), Err(JsonError::TooDeep));
        assert!(matches!(parse("{} x"), Err(JsonError::TrailingContent { .. })));
    }

    #[test]
    fn multibyte_inside_unicode_escape_is_an_error_not_a_panic() {
        assert!(matches!(parse("\"\\u123\u{e9}\""), Err(JsonError::BadEscape { .. })));
        assert!(matches!(parse("\"\\u\u{1f600}\""), Err(JsonError::BadEscape { .. })));
        assert!(matches!(parse("\"\\ud83d\\u12\u{e9}\""), Err(JsonError::BadEscape { .. })));
    }
}
