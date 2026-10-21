//! A small, exact Datalog engine with proofs.
//!
//! Datalog is logic programming without function symbols: every program
//! terminates, and the answer is the same whatever order rules are written
//! in. This engine adds:
//!
//! * **Stratified negation** (`not p(X)`), checked before evaluation.
//! * **Comparisons** (`=`, `!=`, `<`, `<=`, `>`, `>=`) and `contains/2`.
//! * **Provenance.** Every fact remembers how it was first derived: the rule
//!   and the facts it used, or for a base fact the sources it came from. An
//!   answer's proof is therefore a finite tree ending in sources.
//! * **Hard limits** on program size, facts and work, so no query can exhaust
//!   the machine.
//!
//! Syntax (one clause per `.`; `%` starts a comment):
//!
//! ```text
//! path(X, Y) :- edge(X, Y).
//! path(X, Z) :- path(X, Y), edge(Y, Z).
//! lonely(X)  :- node(X), not path(X, _).
//! ?- path("a", Y), Y != "a".
//! ```
//!
//! Variables start with an upper-case letter or `_`; a lone `_` matches
//! anything. Constants are "strings", integers, or bare lower-case words
//! (`decided` is the string "decided"). Literals in a body are checked left
//! to right: a variable must be bound by an earlier positive literal before a
//! negation or comparison uses it (`X = c` may bind `X`).

use std::collections::HashMap;
use std::fmt::Write as _;
use std::rc::Rc;

use crate::json::{canonical, Value};
use crate::schema::GateError;
use crate::sha256::hex_digest;

// ------------------------------------------------------------------ values

#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Val {
    Int(i64),
    Str(Rc<str>),
}

impl Val {
    pub fn str(s: &str) -> Val {
        Val::Str(Rc::from(s))
    }
    fn to_json(&self) -> Value {
        match self {
            Val::Int(i) => Value::Int(*i),
            Val::Str(s) => Value::str(s.to_string()),
        }
    }
    fn write(&self, out: &mut String) {
        match self {
            Val::Int(i) => {
                let _ = write!(out, "{i}");
            }
            Val::Str(s) => {
                out.push('"');
                for c in s.chars() {
                    match c {
                        '"' => out.push_str("\\\""),
                        '\\' => out.push_str("\\\\"),
                        '\n' => out.push_str("\\n"),
                        '\t' => out.push_str("\\t"),
                        '\r' => out.push_str("\\r"),
                        c if (c as u32) < 0x20 => {
                            let _ = write!(out, "\\u{{{:x}}}", c as u32);
                        }
                        c => out.push(c),
                    }
                }
                out.push('"');
            }
        }
    }
}

// ----------------------------------------------------------------- limits

#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub max_program_bytes: usize,
    pub max_clauses: usize,
    pub max_body: usize,
    pub max_arity: usize,
    pub max_facts: usize,
    pub max_steps: u64,
    pub max_answers: usize,
    /// Values held in facts and indexes together (a memory bound).
    pub max_cells: usize,
    /// Size of the result, in bytes of values written (roughly).
    pub max_output: usize,
    /// Answers given a proof (the first this many, in answer order); the
    /// rest carry `"proof": null`. Proofs are most of a result's cost.
    pub max_proofs: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Limits {
            max_program_bytes: 100_000,
            max_clauses: 10_000,
            max_body: 32,
            max_arity: 16,
            max_facts: 500_000,
            max_steps: 20_000_000,
            max_answers: 10_000,
            max_cells: 10_000_000,
            max_output: 64 << 20,
            max_proofs: 10_000,
        }
    }
}

fn qerr(msg: impl Into<String>) -> GateError {
    GateError::new("E_QUERY", msg)
}

fn limit(msg: impl Into<String>) -> GateError {
    GateError::new("E_QUERY_LIMIT", msg)
}

// --------------------------------------------------------------- the AST

#[derive(Clone, Debug)]
enum Term {
    Var(usize),
    Const(Val),
    Wild,
}

#[derive(Clone, Debug)]
struct Atom {
    pred: usize,
    args: Vec<Term>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Op {
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
}

impl Op {
    fn text(self) -> &'static str {
        match self {
            Op::Eq => "=",
            Op::Ne => "!=",
            Op::Lt => "<",
            Op::Le => "<=",
            Op::Gt => ">",
            Op::Ge => ">=",
        }
    }
}

#[derive(Clone, Debug)]
enum Lit {
    Pos(Atom),
    Neg(Atom),
    Cmp(Op, Term, Term),
    Contains(Term, Term),
}

#[derive(Clone, Debug)]
struct Rule {
    head: Atom,
    body: Vec<Lit>,
    vars: Vec<String>,
    /// Where it came from: "view" or "line N".
    origin: String,
}

#[derive(Clone, Debug)]
struct Pred {
    name: String,
    arity: usize,
    base: bool,
    /// Defined by the built-in view library: users may not add rules to it.
    view: bool,
}

// ---------------------------------------------------------------- lexing

#[derive(Clone, Debug, PartialEq)]
enum Tok {
    Ident(String),
    Var(String),
    Str(String),
    Int(i64),
    LParen,
    RParen,
    Comma,
    Dot,
    If,
    Query,
    Op(Op),
}

struct Lexer<'a> {
    chars: std::iter::Peekable<std::str::CharIndices<'a>>,
    line: usize,
}

impl<'a> Lexer<'a> {
    fn new(src: &'a str) -> Self {
        Lexer {
            chars: src.char_indices().peekable(),
            line: 1,
        }
    }

    fn tokens(mut self) -> Result<Vec<(Tok, usize)>, GateError> {
        let mut out = Vec::new();
        while let Some(t) = self.next()? {
            out.push(t);
        }
        Ok(out)
    }

    fn peek_char(&mut self) -> Option<char> {
        self.chars.peek().map(|(_, c)| *c)
    }

    fn next(&mut self) -> Result<Option<(Tok, usize)>, GateError> {
        loop {
            let c = match self.peek_char() {
                None => return Ok(None),
                Some(c) => c,
            };
            if c == '\n' {
                self.line += 1;
                self.chars.next();
            } else if c == ' ' || c == '\t' || c == '\r' {
                self.chars.next();
            } else if c == '%' {
                while let Some(c) = self.peek_char() {
                    if c == '\n' {
                        break;
                    }
                    self.chars.next();
                }
            } else {
                break;
            }
        }
        let line = self.line;
        let c = self.chars.next().map(|(_, c)| c).unwrap();
        let tok = match c {
            '(' => Tok::LParen,
            ')' => Tok::RParen,
            ',' => Tok::Comma,
            '.' => Tok::Dot,
            '=' => Tok::Op(Op::Eq),
            '!' => {
                if self.peek_char() == Some('=') {
                    self.chars.next();
                    Tok::Op(Op::Ne)
                } else {
                    return Err(qerr(format!("line {line}: '!' must be followed by '='")));
                }
            }
            '<' => {
                if self.peek_char() == Some('=') {
                    self.chars.next();
                    Tok::Op(Op::Le)
                } else {
                    Tok::Op(Op::Lt)
                }
            }
            '>' => {
                if self.peek_char() == Some('=') {
                    self.chars.next();
                    Tok::Op(Op::Ge)
                } else {
                    Tok::Op(Op::Gt)
                }
            }
            ':' => {
                if self.peek_char() == Some('-') {
                    self.chars.next();
                    Tok::If
                } else {
                    return Err(qerr(format!("line {line}: ':' must be followed by '-'")));
                }
            }
            '?' => {
                if self.peek_char() == Some('-') {
                    self.chars.next();
                    Tok::Query
                } else {
                    return Err(qerr(format!("line {line}: '?' must be followed by '-'")));
                }
            }
            '"' => {
                let mut s = String::new();
                loop {
                    match self.chars.next().map(|(_, c)| c) {
                        None => return Err(qerr(format!("line {line}: unterminated string"))),
                        Some('"') => break,
                        Some('\n') => {
                            return Err(qerr(format!("line {line}: line break inside a string")))
                        }
                        Some('\\') => match self.chars.next().map(|(_, c)| c) {
                            Some('"') => s.push('"'),
                            Some('\\') => s.push('\\'),
                            Some('n') => s.push('\n'),
                            Some('t') => s.push('\t'),
                            Some('r') => s.push('\r'),
                            // \u{hex}: how the printer writes other control characters.
                            Some('u') => {
                                if self.chars.next().map(|(_, c)| c) != Some('{') {
                                    return Err(qerr(format!("line {line}: write \\u{{hex}}")));
                                }
                                let mut hex = String::new();
                                loop {
                                    match self.chars.next().map(|(_, c)| c) {
                                        Some('}') => break,
                                        Some(c) if c.is_ascii_hexdigit() && hex.len() < 6 => {
                                            hex.push(c)
                                        }
                                        _ => {
                                            return Err(qerr(format!(
                                                "line {line}: bad \\u{{hex}} escape"
                                            )))
                                        }
                                    }
                                }
                                match u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32) {
                                    Some(c) => s.push(c),
                                    None => {
                                        return Err(qerr(format!(
                                            "line {line}: \\u{{{hex}}} is not a character"
                                        )))
                                    }
                                }
                            }
                            _ => {
                                return Err(qerr(format!("line {line}: unknown escape in string")))
                            }
                        },
                        Some(c) => s.push(c),
                    }
                }
                Tok::Str(s)
            }
            c if c.is_ascii_digit() || c == '-' => {
                let mut s = String::from(c);
                while let Some(d) = self.peek_char() {
                    if d.is_ascii_digit() {
                        s.push(d);
                        self.chars.next();
                    } else {
                        break;
                    }
                }
                if s == "-" {
                    return Err(qerr(format!("line {line}: '-' must start a number")));
                }
                match s.parse::<i64>() {
                    Ok(n) => Tok::Int(n),
                    Err(_) => return Err(qerr(format!("line {line}: integer out of range"))),
                }
            }
            c if c.is_ascii_alphabetic() || c == '_' => {
                let mut s = String::from(c);
                while let Some(d) = self.peek_char() {
                    if d.is_ascii_alphanumeric() || d == '_' {
                        s.push(d);
                        self.chars.next();
                    } else {
                        break;
                    }
                }
                if c.is_ascii_uppercase() || c == '_' {
                    Tok::Var(s)
                } else {
                    Tok::Ident(s)
                }
            }
            c => return Err(qerr(format!("line {line}: unexpected character {c:?}"))),
        };
        Ok(Some((tok, line)))
    }
}

// --------------------------------------------------------------- parsing

/// A clause before predicate resolution.
struct RawAtom {
    name: String,
    args: Vec<RawTerm>,
}

#[derive(Clone)]
enum RawTerm {
    Var(String),
    Const(Val),
    Wild,
}

enum RawLit {
    Pos(RawAtom),
    Neg(RawAtom),
    Cmp(Op, RawTerm, RawTerm),
}

struct RawClause {
    head: Option<RawAtom>, // None for a query
    body: Vec<RawLit>,
    line: usize,
}

struct Parser {
    toks: Vec<(Tok, usize)>,
    pos: usize,
}

impl Parser {
    fn peek(&self) -> Option<&Tok> {
        self.toks.get(self.pos).map(|(t, _)| t)
    }
    fn peek2(&self) -> Option<&Tok> {
        self.toks.get(self.pos + 1).map(|(t, _)| t)
    }
    fn line(&self) -> usize {
        self.toks
            .get(self.pos)
            .or_else(|| self.toks.last())
            .map(|(_, l)| *l)
            .unwrap_or(1)
    }
    fn bump(&mut self) -> Option<Tok> {
        let t = self.toks.get(self.pos).map(|(t, _)| t.clone());
        self.pos += 1;
        t
    }
    fn expect(&mut self, want: Tok, what: &str) -> Result<(), GateError> {
        let line = self.line();
        match self.bump() {
            Some(t) if t == want => Ok(()),
            Some(t) => Err(qerr(format!(
                "line {line}: expected {what}, found {}",
                describe(&t)
            ))),
            None => Err(qerr(format!(
                "line {line}: expected {what}, found the end of the program"
            ))),
        }
    }

    fn clauses(&mut self) -> Result<Vec<RawClause>, GateError> {
        let mut out = Vec::new();
        while self.peek().is_some() {
            out.push(self.clause()?);
        }
        Ok(out)
    }

    fn clause(&mut self) -> Result<RawClause, GateError> {
        let line = self.line();
        if self.peek() == Some(&Tok::Query) {
            self.bump();
            let body = self.body()?;
            self.expect(Tok::Dot, "'.' at the end of the query")?;
            return Ok(RawClause {
                head: None,
                body,
                line,
            });
        }
        let head = match self.literal()? {
            RawLit::Pos(a) => a,
            _ => {
                return Err(qerr(format!(
                    "line {line}: a rule's head must be a plain atom"
                )))
            }
        };
        let body = if self.peek() == Some(&Tok::If) {
            self.bump();
            self.body()?
        } else {
            Vec::new()
        };
        self.expect(Tok::Dot, "'.' at the end of the clause")?;
        Ok(RawClause {
            head: Some(head),
            body,
            line,
        })
    }

    fn body(&mut self) -> Result<Vec<RawLit>, GateError> {
        let mut lits = vec![self.literal()?];
        while self.peek() == Some(&Tok::Comma) {
            self.bump();
            lits.push(self.literal()?);
        }
        Ok(lits)
    }

    fn literal(&mut self) -> Result<RawLit, GateError> {
        let line = self.line();
        if let (Some(Tok::Ident(w)), Some(Tok::Ident(_))) = (self.peek(), self.peek2()) {
            if w == "not" {
                self.bump();
                // No recursion: `not` applies to one atom, once.
                return match self.positive()? {
                    RawLit::Pos(a) => Ok(RawLit::Neg(a)),
                    _ => Err(qerr(format!(
                        "line {line}: 'not' must be followed by an atom"
                    ))),
                };
            }
        }
        self.positive()
    }

    fn positive(&mut self) -> Result<RawLit, GateError> {
        let line = self.line();
        if let (Some(Tok::Ident(w)), Some(Tok::Ident(_))) = (self.peek(), self.peek2()) {
            if w == "not" {
                return Err(qerr(format!("line {line}: 'not' cannot be repeated")));
            }
        }
        if let (Some(Tok::Ident(name)), Some(Tok::LParen)) = (self.peek(), self.peek2()) {
            let name = name.clone();
            self.bump();
            self.bump();
            let mut args = Vec::new();
            if self.peek() != Some(&Tok::RParen) {
                args.push(self.term()?);
                while self.peek() == Some(&Tok::Comma) {
                    self.bump();
                    args.push(self.term()?);
                }
            }
            self.expect(Tok::RParen, "')'")?;
            return Ok(RawLit::Pos(RawAtom { name, args }));
        }
        let left = self.term()?;
        if let Some(Tok::Op(op)) = self.peek() {
            let op = *op;
            self.bump();
            let right = self.term()?;
            return Ok(RawLit::Cmp(op, left, right));
        }
        match left {
            // A bare lower-case word on its own is a proposition: `ready`.
            RawTerm::Const(Val::Str(s))
                if self.toks[self.pos - 1].0 == Tok::Ident(s.to_string()) =>
            {
                Ok(RawLit::Pos(RawAtom {
                    name: s.to_string(),
                    args: Vec::new(),
                }))
            }
            _ => Err(qerr(format!(
                "line {line}: expected an atom or a comparison"
            ))),
        }
    }

    fn term(&mut self) -> Result<RawTerm, GateError> {
        let line = self.line();
        match self.bump() {
            Some(Tok::Var(v)) if v == "_" => Ok(RawTerm::Wild),
            Some(Tok::Var(v)) => Ok(RawTerm::Var(v)),
            Some(Tok::Str(s)) => Ok(RawTerm::Const(Val::str(&s))),
            Some(Tok::Int(n)) => Ok(RawTerm::Const(Val::Int(n))),
            Some(Tok::Ident(w)) => Ok(RawTerm::Const(Val::str(&w))),
            Some(t) => Err(qerr(format!(
                "line {line}: expected a term, found {}",
                describe(&t)
            ))),
            None => Err(qerr(format!(
                "line {line}: expected a term, found the end of the program"
            ))),
        }
    }
}

fn describe(t: &Tok) -> String {
    match t {
        Tok::Ident(s) => format!("'{s}'"),
        Tok::Var(s) => format!("variable {s}"),
        Tok::Str(_) => "a string".into(),
        Tok::Int(n) => format!("{n}"),
        Tok::LParen => "'('".into(),
        Tok::RParen => "')'".into(),
        Tok::Comma => "','".into(),
        Tok::Dot => "'.'".into(),
        Tok::If => "':-'".into(),
        Tok::Query => "'?-'".into(),
        Tok::Op(o) => format!("'{}'", o.text()),
    }
}

// ------------------------------------------------------------ programs

/// Base relations the caller will supply: (name, arity).
pub type Schema = Vec<(&'static str, usize)>;

pub struct Program {
    preds: Vec<Pred>,
    by_name: HashMap<String, usize>,
    rules: Vec<Rule>,
    answer: usize,
    answer_vars: Vec<String>,
    strata: Vec<Vec<usize>>, // rule indexes per stratum, in order
    reachable: Vec<bool>,
}

const ANSWER: &str = "?";

impl Program {
    /// Compile `views` (trusted library rules) and `user` (the caller's
    /// program, which must contain exactly one `?-` query) against `schema`.
    pub fn compile(
        schema: &Schema,
        views: &str,
        user: &str,
        lim: &Limits,
    ) -> Result<Program, GateError> {
        if user.len() > lim.max_program_bytes {
            return Err(limit(format!(
                "program is longer than {} bytes",
                lim.max_program_bytes
            )));
        }
        let mut p = Program {
            preds: Vec::new(),
            by_name: HashMap::new(),
            rules: Vec::new(),
            answer: 0,
            answer_vars: Vec::new(),
            strata: Vec::new(),
            reachable: Vec::new(),
        };
        for (name, arity) in schema {
            p.declare(name, *arity, true, false);
        }
        let view_clauses = Parser {
            toks: Lexer::new(views).tokens()?,
            pos: 0,
        }
        .clauses()?;
        let user_clauses = Parser {
            toks: Lexer::new(user).tokens()?,
            pos: 0,
        }
        .clauses()?;
        if user_clauses.len() > lim.max_clauses {
            return Err(limit(format!(
                "program has more than {} clauses",
                lim.max_clauses
            )));
        }
        // Declare every head first, so bodies may refer to rules defined later.
        for c in &view_clauses {
            let h = c.head.as_ref().expect("views have no queries");
            p.head_pred(h, true, c.line)?;
        }
        for c in &user_clauses {
            if let Some(h) = &c.head {
                p.head_pred(h, false, c.line)?;
            }
        }
        for c in view_clauses {
            p.add_rule(c, "view".into(), lim)?;
        }
        let mut queries = 0;
        for c in user_clauses {
            if c.head.is_none() {
                queries += 1;
                if queries > 1 {
                    return Err(qerr(format!(
                        "line {}: a program has exactly one query (?-)",
                        c.line
                    )));
                }
            }
            let origin = format!("line {}", c.line);
            p.add_rule(c, origin, lim)?;
        }
        if queries == 0 {
            return Err(qerr("a program needs a query: ?- goal(X)."));
        }
        p.stratify()?;
        Ok(p)
    }

    fn declare(&mut self, name: &str, arity: usize, base: bool, view: bool) -> usize {
        let id = self.preds.len();
        self.preds.push(Pred {
            name: name.to_string(),
            arity,
            base,
            view,
        });
        self.by_name.insert(name.to_string(), id);
        id
    }

    fn head_pred(&mut self, h: &RawAtom, view: bool, line: usize) -> Result<usize, GateError> {
        if h.name == "not" || h.name == "contains" {
            return Err(qerr(format!("line {line}: '{}' is reserved", h.name)));
        }
        match self.by_name.get(&h.name) {
            Some(&id) => {
                let pr = &self.preds[id];
                if pr.base {
                    return Err(qerr(format!(
                        "line {line}: '{}' is a base relation of the memory and cannot be redefined",
                        h.name
                    )));
                }
                if pr.view && !view {
                    return Err(qerr(format!(
                        "line {line}: '{}' is a built-in view and cannot be redefined; use another name",
                        h.name
                    )));
                }
                if pr.arity != h.args.len() {
                    return Err(qerr(format!(
                        "line {line}: '{}' is used with {} and {} arguments",
                        h.name,
                        pr.arity,
                        h.args.len()
                    )));
                }
                Ok(id)
            }
            None => Ok(self.declare(&h.name, h.args.len(), false, view)),
        }
    }

    fn resolve(&self, a: &RawAtom, line: usize) -> Result<usize, GateError> {
        let id = *self.by_name.get(&a.name).ok_or_else(|| {
            qerr(format!(
                "line {line}: unknown relation '{}/{}'",
                a.name,
                a.args.len()
            ))
        })?;
        if self.preds[id].arity != a.args.len() {
            return Err(qerr(format!(
                "line {line}: '{}' takes {} arguments, not {}",
                a.name,
                self.preds[id].arity,
                a.args.len()
            )));
        }
        Ok(id)
    }

    fn add_rule(&mut self, c: RawClause, origin: String, lim: &Limits) -> Result<(), GateError> {
        let line = c.line;
        if c.body.len() > lim.max_body {
            return Err(limit(format!(
                "line {line}: a rule body has more than {} literals",
                lim.max_body
            )));
        }
        let mut vars: Vec<String> = Vec::new();
        let var = |name: &str, vars: &mut Vec<String>| -> usize {
            match vars.iter().position(|v| v == name) {
                Some(i) => i,
                None => {
                    vars.push(name.to_string());
                    vars.len() - 1
                }
            }
        };
        let term = |t: &RawTerm, vars: &mut Vec<String>| match t {
            RawTerm::Var(v) => Term::Var(var(v, vars)),
            RawTerm::Const(c) => Term::Const(c.clone()),
            RawTerm::Wild => Term::Wild,
        };
        let mut body = Vec::new();
        for l in &c.body {
            body.push(match l {
                RawLit::Pos(a) if a.name == "contains" => {
                    if a.args.len() != 2 {
                        return Err(qerr(format!("line {line}: contains takes 2 arguments")));
                    }
                    Lit::Contains(term(&a.args[0], &mut vars), term(&a.args[1], &mut vars))
                }
                RawLit::Neg(a) if a.name == "contains" => {
                    return Err(qerr(format!(
                        "line {line}: write 'not contains' as a separate rule"
                    )));
                }
                RawLit::Pos(a) => {
                    let pred = self.resolve(a, line)?;
                    Lit::Pos(Atom {
                        pred,
                        args: a.args.iter().map(|t| term(t, &mut vars)).collect(),
                    })
                }
                RawLit::Neg(a) => {
                    let pred = self.resolve(a, line)?;
                    Lit::Neg(Atom {
                        pred,
                        args: a.args.iter().map(|t| term(t, &mut vars)).collect(),
                    })
                }
                RawLit::Cmp(op, l, r) => Lit::Cmp(*op, term(l, &mut vars), term(r, &mut vars)),
            });
        }
        let head = match &c.head {
            Some(h) => {
                if h.args.len() > lim.max_arity {
                    return Err(limit(format!(
                        "line {line}: more than {} arguments",
                        lim.max_arity
                    )));
                }
                let pred = self.by_name[&h.name];
                Atom {
                    pred,
                    args: h.args.iter().map(|t| term(t, &mut vars)).collect(),
                }
            }
            None => {
                // The query becomes a rule for "?" over its named variables,
                // in order of first appearance.
                let names: Vec<String> = vars
                    .iter()
                    .filter(|v| !v.starts_with('_'))
                    .cloned()
                    .collect();
                if names.len() > lim.max_arity {
                    return Err(limit(format!(
                        "line {line}: the query has more than {} variables",
                        lim.max_arity
                    )));
                }
                let args = names.iter().map(|n| Term::Var(var(n, &mut vars))).collect();
                let pred = self.declare(ANSWER, names.len(), false, false);
                self.answer = pred;
                self.answer_vars = names;
                Atom { pred, args }
            }
        };
        let rule = Rule {
            head,
            body,
            vars,
            origin,
        };
        check_safety(&rule, &self.preds, line)?;
        self.rules.push(rule);
        Ok(())
    }

    /// Assign strata so every negated relation is complete before use.
    fn stratify(&mut self) -> Result<(), GateError> {
        let n = self.preds.len();
        let mut stratum = vec![0usize; n];
        loop {
            let mut changed = false;
            for r in &self.rules {
                let h = r.head.pred;
                for l in &r.body {
                    let (q, neg) = match l {
                        Lit::Pos(a) => (a.pred, false),
                        Lit::Neg(a) => (a.pred, true),
                        _ => continue,
                    };
                    let need = stratum[q] + usize::from(neg);
                    if stratum[h] < need {
                        stratum[h] = need;
                        changed = true;
                        if need > n {
                            return Err(qerr(format!(
                                "'{}' depends on its own negation (through 'not {}'); \
                                 such programs have no single meaning",
                                self.preds[h].name, self.preds[q].name
                            )));
                        }
                    }
                }
            }
            if !changed {
                break;
            }
        }
        let top = stratum.iter().copied().max().unwrap_or(0);
        let mut strata: Vec<Vec<usize>> = vec![Vec::new(); top + 1];
        for (i, r) in self.rules.iter().enumerate() {
            strata[stratum[r.head.pred]].push(i);
        }
        self.strata = strata;
        // Relations the query can reach, through positive or negated atoms.
        let mut by_head: Vec<Vec<usize>> = vec![Vec::new(); n];
        for (i, r) in self.rules.iter().enumerate() {
            by_head[r.head.pred].push(i);
        }
        let mut reach = vec![false; n];
        let mut todo = vec![self.answer];
        reach[self.answer] = true;
        while let Some(p) = todo.pop() {
            for &i in &by_head[p] {
                for l in &self.rules[i].body {
                    if let Lit::Pos(a) | Lit::Neg(a) = l {
                        if !reach[a.pred] {
                            reach[a.pred] = true;
                            todo.push(a.pred);
                        }
                    }
                }
            }
        }
        self.reachable = reach;
        Ok(())
    }

    pub fn pred_id(&self, name: &str) -> Option<usize> {
        self.by_name.get(name).copied()
    }

    /// Is relation `name` reachable from the query through the rules? Base
    /// facts of unreachable relations cannot affect any answer, so callers
    /// need not load them.
    pub fn needs(&self, name: &str) -> bool {
        self.pred_id(name)
            .map(|p| self.reachable[p])
            .unwrap_or(false)
    }

    fn rule_text(&self, r: &Rule) -> String {
        let mut s = String::new();
        self.atom_text(&r.head, &r.vars, &mut s);
        if !r.body.is_empty() {
            s.push_str(" :- ");
            for (i, l) in r.body.iter().enumerate() {
                if i > 0 {
                    s.push_str(", ");
                }
                match l {
                    Lit::Pos(a) => self.atom_text(a, &r.vars, &mut s),
                    Lit::Neg(a) => {
                        s.push_str("not ");
                        self.atom_text(a, &r.vars, &mut s);
                    }
                    Lit::Cmp(op, a, b) => {
                        term_text(a, &r.vars, &mut s);
                        let _ = write!(s, " {} ", op.text());
                        term_text(b, &r.vars, &mut s);
                    }
                    Lit::Contains(a, b) => {
                        s.push_str("contains(");
                        term_text(a, &r.vars, &mut s);
                        s.push_str(", ");
                        term_text(b, &r.vars, &mut s);
                        s.push(')');
                    }
                }
            }
        }
        s.push('.');
        s
    }

    fn atom_text(&self, a: &Atom, vars: &[String], s: &mut String) {
        s.push_str(&self.preds[a.pred].name);
        if !a.args.is_empty() {
            s.push('(');
            for (i, t) in a.args.iter().enumerate() {
                if i > 0 {
                    s.push_str(", ");
                }
                term_text(t, vars, s);
            }
            s.push(')');
        }
    }
}

fn term_text(t: &Term, vars: &[String], s: &mut String) {
    match t {
        Term::Var(i) => s.push_str(&vars[*i]),
        Term::Const(c) => c.write(s),
        Term::Wild => s.push('_'),
    }
}

/// Range restriction, checked left to right.
fn check_safety(r: &Rule, preds: &[Pred], line: usize) -> Result<(), GateError> {
    let mut bound = vec![false; r.vars.len()];
    let is_bound = |t: &Term, bound: &[bool]| match t {
        Term::Var(i) => bound[*i],
        Term::Const(_) => true,
        Term::Wild => false,
    };
    let name = |t: &Term| match t {
        Term::Var(i) => r.vars[*i].clone(),
        _ => "_".into(),
    };
    for l in &r.body {
        match l {
            Lit::Pos(a) => {
                for t in &a.args {
                    if let Term::Var(i) = t {
                        bound[*i] = true;
                    }
                }
            }
            Lit::Neg(a) => {
                for t in &a.args {
                    if let Term::Var(_) = t {
                        if !is_bound(t, &bound) {
                            return Err(qerr(format!(
                                "line {line}: variable {} in 'not {}' must first appear in a positive literal \
                                 to its left (use _ for 'any value')",
                                name(t),
                                preds[a.pred].name
                            )));
                        }
                    }
                }
            }
            Lit::Cmp(op, a, b) => {
                let (ba, bb) = (is_bound(a, &bound), is_bound(b, &bound));
                if matches!(a, Term::Wild) || matches!(b, Term::Wild) {
                    return Err(qerr(format!("line {line}: _ cannot be compared")));
                }
                match (ba, bb) {
                    (true, true) => {}
                    (false, true) if *op == Op::Eq => {
                        if let Term::Var(i) = a {
                            bound[*i] = true;
                        }
                    }
                    (true, false) if *op == Op::Eq => {
                        if let Term::Var(i) = b {
                            bound[*i] = true;
                        }
                    }
                    _ => {
                        let t = if ba { b } else { a };
                        return Err(qerr(format!(
                            "line {line}: variable {} is compared before a positive literal to its left binds it",
                            name(t)
                        )));
                    }
                }
            }
            Lit::Contains(a, b) => {
                for t in [a, b] {
                    if !is_bound(t, &bound) {
                        return Err(qerr(format!(
                            "line {line}: contains({}, ...) needs both arguments bound by literals to its left",
                            name(t)
                        )));
                    }
                }
            }
        }
    }
    for t in &r.head.args {
        match t {
            Term::Wild => return Err(qerr(format!("line {line}: _ cannot appear in a rule's head"))),
            Term::Var(i) if !bound[*i] => {
                return Err(qerr(format!(
                    "line {line}: variable {} in the head never appears in a positive literal of the body",
                    r.vars[*i]
                )))
            }
            _ => {}
        }
    }
    Ok(())
}

// ------------------------------------------------------------ evaluation

#[derive(Clone, Debug)]
pub enum Prov {
    /// A base fact, with indexes into the caller's source list.
    Base(Vec<usize>),
    /// Derived by `rule` from `premises` (relation, row), with the negated
    /// atoms that were checked absent (wildcards as None).
    Rule {
        rule: usize,
        premises: Vec<(usize, usize)>,
        absent: Vec<(usize, Vec<Option<Val>>)>,
    },
}

#[derive(Default)]
struct Relation {
    rows: Vec<Vec<Val>>,
    prov: Vec<Prov>,
    set: HashMap<Vec<Val>, usize>,
    /// Bound-column mask -> key -> rows (ascending).
    indexes: HashMap<u32, HashMap<Vec<Val>, Vec<usize>>>,
}

impl Relation {
    fn insert(&mut self, row: Vec<Val>, prov: Prov) -> bool {
        if self.set.contains_key(&row) {
            return false;
        }
        let i = self.rows.len();
        for (mask, idx) in self.indexes.iter_mut() {
            idx.entry(key(&row, *mask)).or_default().push(i);
        }
        self.set.insert(row.clone(), i);
        self.rows.push(row);
        self.prov.push(prov);
        true
    }

    /// Values a new row costs: the row, plus its key in every index.
    fn row_cost(&self, arity: usize) -> usize {
        arity
            + 1
            + self
                .indexes
                .keys()
                .map(|m| m.count_ones() as usize + 1)
                .sum::<usize>()
    }

    fn index(&mut self, mask: u32) -> &HashMap<Vec<Val>, Vec<usize>> {
        if !self.indexes.contains_key(&mask) {
            let mut idx: HashMap<Vec<Val>, Vec<usize>> = HashMap::new();
            for (i, row) in self.rows.iter().enumerate() {
                idx.entry(key(row, mask)).or_default().push(i);
            }
            self.indexes.insert(mask, idx);
        }
        &self.indexes[&mask]
    }
}

/// Rows of an index bucket (ascending) within [lo, hi), by binary search.
fn in_range(rows: &[usize], lo: usize, hi: usize) -> &[usize] {
    let a = rows.partition_point(|&x| x < lo);
    let b = rows.partition_point(|&x| x < hi);
    &rows[a..b.max(a)]
}

fn key(row: &[Val], mask: u32) -> Vec<Val> {
    row.iter()
        .enumerate()
        .filter(|(i, _)| mask & (1 << i) != 0)
        .map(|(_, v)| v.clone())
        .collect()
}

pub struct Engine<'p> {
    prog: &'p Program,
    rels: Vec<Relation>,
    facts: usize,
    steps: u64,
    cells: usize,
    lim: Limits,
    /// Relations that gained rows this round (each listed once).
    dirty: Vec<usize>,
    marked: Vec<bool>,
}

/// Rows of a relation visible to one evaluation: [lo, hi).
#[derive(Clone, Copy)]
struct Range {
    lo: usize,
    hi: usize,
}

impl<'p> Engine<'p> {
    pub fn new(prog: &'p Program, lim: Limits) -> Self {
        let rels = (0..prog.preds.len()).map(|_| Relation::default()).collect();
        let marked = vec![false; prog.preds.len()];
        Engine {
            prog,
            rels,
            facts: 0,
            steps: 0,
            cells: 0,
            lim,
            dirty: Vec::new(),
            marked,
        }
    }

    /// Add a base fact. Duplicates merge their sources.
    pub fn add_base(
        &mut self,
        pred: usize,
        row: Vec<Val>,
        sources: Vec<usize>,
    ) -> Result<(), GateError> {
        debug_assert!(self.prog.preds[pred].base && self.prog.preds[pred].arity == row.len());
        let rel = &mut self.rels[pred];
        if let Some(&i) = rel.set.get(&row) {
            if let Prov::Base(s) = &mut rel.prov[i] {
                for x in sources {
                    if !s.contains(&x) {
                        s.push(x);
                    }
                }
            }
            return Ok(());
        }
        self.store(pred, row, Prov::Base(sources))
    }

    /// Store a new fact, charging its memory.
    fn store(&mut self, pred: usize, row: Vec<Val>, prov: Prov) -> Result<(), GateError> {
        self.facts += 1;
        if self.facts > self.lim.max_facts {
            return Err(limit(format!("more than {} facts", self.lim.max_facts)));
        }
        let cost =
            self.rels[pred].row_cost(row.len()) + row.iter().map(val_size).sum::<usize>() / 64;
        self.charge_cells(cost)?;
        self.charge_steps(cost as u64)?;
        self.rels[pred].insert(row, prov);
        if !self.marked[pred] {
            self.marked[pred] = true;
            self.dirty.push(pred);
        }
        Ok(())
    }

    fn charge_cells(&mut self, n: usize) -> Result<(), GateError> {
        self.cells += n;
        if self.cells > self.lim.max_cells {
            return Err(limit(format!(
                "the query needs more than {} values in memory",
                self.lim.max_cells
            )));
        }
        Ok(())
    }

    fn charge_steps(&mut self, n: u64) -> Result<(), GateError> {
        self.steps += n;
        if self.steps > self.lim.max_steps {
            return Err(limit(format!(
                "the query needs more than {} steps",
                self.lim.max_steps
            )));
        }
        Ok(())
    }

    /// The index for `mask`, charging its memory and work when first built.
    fn index_of(
        &mut self,
        pred: usize,
        mask: u32,
    ) -> Result<&HashMap<Vec<Val>, Vec<usize>>, GateError> {
        if !self.rels[pred].indexes.contains_key(&mask) {
            let n = self.rels[pred].rows.len();
            self.charge_cells(n * (mask.count_ones() as usize + 1))?;
            self.charge_steps(n as u64)?;
        }
        Ok(self.rels[pred].index(mask))
    }

    pub fn run(&mut self) -> Result<(), GateError> {
        // Only rules whose head the query can reach: a view the query does
        // not use costs nothing, however large it would be.
        let strata: Vec<Vec<usize>> = self
            .prog
            .strata
            .iter()
            .map(|rules| {
                rules
                    .iter()
                    .copied()
                    .filter(|&r| self.prog.reachable[self.prog.rules[r].head.pred])
                    .collect()
            })
            .collect();
        for rules in &strata {
            if !rules.is_empty() {
                self.run_stratum(rules)?;
            }
        }
        Ok(())
    }

    fn run_stratum(&mut self, rules: &[usize]) -> Result<(), GateError> {
        // Relations defined in this stratum; lower strata no longer change.
        let mut here: Vec<usize> = rules
            .iter()
            .map(|&r| self.prog.rules[r].head.pred)
            .collect();
        here.sort_unstable();
        here.dedup();
        // For each relation of this stratum, the (rule, atom) pairs that read
        // it: only those need re-running when it gains rows.
        let mut users: HashMap<usize, Vec<(usize, usize)>> = HashMap::new();
        for &r in rules {
            for (j, l) in self.prog.rules[r].body.iter().enumerate() {
                if let Lit::Pos(a) = l {
                    if here.binary_search(&a.pred).is_ok() {
                        users.entry(a.pred).or_default().push((r, j));
                    }
                }
            }
        }
        // Row counts at the start of the current round, and of the last.
        let mut snap: HashMap<usize, usize> =
            here.iter().map(|&p| (p, self.rels[p].rows.len())).collect();
        let mut delta_lo = snap.clone();
        for p in std::mem::take(&mut self.dirty) {
            self.marked[p] = false;
        }
        // First round: every rule over everything known so far.
        for &r in rules {
            self.charge_steps(1)?;
            let ranges = self.ranges(r, &snap);
            self.eval_rule(r, &ranges)?;
        }
        // Then semi-naive rounds: one recursive atom ranges over only the
        // rows its relation gained in the previous round.
        loop {
            let mut changed = std::mem::take(&mut self.dirty);
            for &p in &changed {
                self.marked[p] = false;
            }
            changed.sort_unstable();
            if changed.is_empty() {
                break;
            }
            for &p in &changed {
                delta_lo.insert(p, snap[&p]);
                snap.insert(p, self.rels[p].rows.len());
            }
            for &p in &changed {
                let Some(us) = users.get(&p) else { continue };
                for &(r, j) in us {
                    self.charge_steps(1)?;
                    let mut ranges = self.ranges(r, &snap);
                    ranges[j] = Range {
                        lo: delta_lo[&p],
                        hi: snap[&p],
                    };
                    self.eval_rule(r, &ranges)?;
                }
            }
        }
        Ok(())
    }

    /// The rows each body atom of rule `r` may read this round.
    fn ranges(&self, r: usize, snap: &HashMap<usize, usize>) -> Vec<Range> {
        self.prog.rules[r]
            .body
            .iter()
            .map(|l| match l {
                Lit::Pos(a) | Lit::Neg(a) => Range {
                    lo: 0,
                    hi: snap
                        .get(&a.pred)
                        .copied()
                        .unwrap_or(self.rels[a.pred].rows.len()),
                },
                _ => Range { lo: 0, hi: 0 },
            })
            .collect()
    }

    fn eval_rule(&mut self, r: usize, ranges: &[Range]) -> Result<(), GateError> {
        let nvars = self.prog.rules[r].vars.len();
        let mut env: Vec<Option<Val>> = vec![None; nvars];
        let mut premises: Vec<(usize, usize)> = Vec::new();
        let mut absent: Vec<(usize, Vec<Option<Val>>)> = Vec::new();
        // New facts go straight into their relation: every read is capped at
        // the round's snapshot, so they are not seen until the next round.
        self.join(r, 0, ranges, &mut env, &mut premises, &mut absent)
    }

    #[allow(clippy::too_many_arguments)]
    fn join(
        &mut self,
        r: usize,
        i: usize,
        ranges: &[Range],
        env: &mut Vec<Option<Val>>,
        premises: &mut Vec<(usize, usize)>,
        absent: &mut Vec<(usize, Vec<Option<Val>>)>,
    ) -> Result<(), GateError> {
        self.charge_steps(1)?;
        let prog = self.prog;
        let rule = &prog.rules[r];
        if i == rule.body.len() {
            let row: Vec<Val> = rule
                .head
                .args
                .iter()
                .map(|t| value(t, env).expect("safe rule binds its head"))
                .collect();
            let head = rule.head.pred;
            if !self.rels[head].set.contains_key(&row) {
                let prov = Prov::Rule {
                    rule: r,
                    premises: premises.clone(),
                    absent: absent.clone(),
                };
                self.store(head, row, prov)?;
            }
            return Ok(());
        }
        match &rule.body[i] {
            Lit::Pos(a) => {
                let range = ranges[i];
                let mut mask = 0u32;
                let mut k = Vec::new();
                for (j, t) in a.args.iter().enumerate() {
                    if let Some(v) = value(t, env) {
                        mask |= 1 << j;
                        k.push(v);
                    }
                }
                let candidates: Vec<usize> = if mask == 0 {
                    (range.lo..range.hi).collect()
                } else {
                    match self.index_of(a.pred, mask)?.get(&k) {
                        None => Vec::new(),
                        Some(rows) => in_range(rows, range.lo, range.hi).to_vec(),
                    }
                };
                self.charge_steps(candidates.len() as u64)?;
                for row_i in candidates {
                    let row = self.rels[a.pred].rows[row_i].clone();
                    let mut newly: Vec<usize> = Vec::new();
                    let mut ok = true;
                    for (t, v) in a.args.iter().zip(&row) {
                        match t {
                            Term::Const(c) => {
                                if c != v {
                                    ok = false;
                                    break;
                                }
                            }
                            Term::Wild => {}
                            Term::Var(x) => match &env[*x] {
                                Some(b) => {
                                    if b != v {
                                        ok = false;
                                        break;
                                    }
                                }
                                None => {
                                    env[*x] = Some(v.clone());
                                    newly.push(*x);
                                }
                            },
                        }
                    }
                    if ok {
                        premises.push((a.pred, row_i));
                        self.join(r, i + 1, ranges, env, premises, absent)?;
                        premises.pop();
                    }
                    for x in newly {
                        env[x] = None;
                    }
                }
                Ok(())
            }
            Lit::Neg(a) => {
                let range = ranges[i];
                let pattern: Vec<Option<Val>> = a.args.iter().map(|t| value(t, env)).collect();
                let mut mask = 0u32;
                let mut k = Vec::new();
                for (j, v) in pattern.iter().enumerate() {
                    if let Some(v) = v {
                        mask |= 1 << j;
                        k.push(v.clone());
                    }
                }
                let present = if mask == 0 {
                    range.hi > range.lo
                } else {
                    self.index_of(a.pred, mask)?
                        .get(&k)
                        .map(|rows| !in_range(rows, range.lo, range.hi).is_empty())
                        .unwrap_or(false)
                };
                if !present {
                    absent.push((a.pred, pattern));
                    self.join(r, i + 1, ranges, env, premises, absent)?;
                    absent.pop();
                }
                Ok(())
            }
            Lit::Cmp(op, a, b) => {
                match (value(a, env), value(b, env)) {
                    (Some(x), Some(y)) => {
                        if compare(*op, &x, &y) {
                            self.join(r, i + 1, ranges, env, premises, absent)?;
                        }
                    }
                    (None, Some(y)) => {
                        let Term::Var(v) = a else { unreachable!() };
                        env[*v] = Some(y);
                        self.join(r, i + 1, ranges, env, premises, absent)?;
                        env[*v] = None;
                    }
                    (Some(x), None) => {
                        let Term::Var(v) = b else { unreachable!() };
                        env[*v] = Some(x);
                        self.join(r, i + 1, ranges, env, premises, absent)?;
                        env[*v] = None;
                    }
                    (None, None) => unreachable!("safety check"),
                }
                Ok(())
            }
            Lit::Contains(a, b) => {
                let (x, y) = (value(a, env).expect("safe"), value(b, env).expect("safe"));
                if let (Val::Str(x), Val::Str(y)) = (&x, &y) {
                    self.charge_steps(((x.len() + y.len()) / 16) as u64)?;
                    // ASCII letters only: the same answer on every platform
                    // and Unicode version.
                    if x.to_ascii_lowercase().contains(&y.to_ascii_lowercase()) {
                        self.join(r, i + 1, ranges, env, premises, absent)?;
                    }
                }
                Ok(())
            }
        }
    }

    /// Answers to the query, sorted, with a proof DAG. `source` turns a base
    /// fact's source index into JSON. Each rule's text appears once, in
    /// `rules`; nodes refer to it by id. The result's size is limited.
    pub fn answers(&self, source: &dyn Fn(usize) -> Value) -> Result<Value, GateError> {
        let prog = self.prog;
        let rel = &self.rels[prog.answer];
        let mut order: Vec<usize> = (0..rel.rows.len()).collect();
        order.sort_by(|&a, &b| rel.rows[a].cmp(&rel.rows[b]));
        let truncated = order.len() > self.lim.max_answers;
        order.truncate(self.lim.max_answers);

        let mut out = Proofs {
            ids: HashMap::new(),
            nodes: Vec::new(),
            rules: Vec::new(),
            rule_ids: HashMap::new(),
            rule_hashes: HashMap::new(),
            hashes: HashMap::new(),
            size: 0,
        };
        let mut answers = Vec::new();
        let mut proved = 0;
        for (n, &a) in order.iter().enumerate() {
            let values = Value::Array(rel.rows[a].iter().map(Val::to_json).collect());
            if n >= self.lim.max_proofs {
                out.charge(
                    rel.rows[a].iter().map(val_size).sum::<usize>() + 32,
                    self.lim.max_output,
                )?;
                answers.push(Value::obj(vec![("values", values), ("proof", Value::Null)]));
                continue;
            }
            let node = self.node(prog.answer, a, &mut out, source)?;
            out.charge(
                rel.rows[a].iter().map(val_size).sum::<usize>() + 32 + 84,
                self.lim.max_output,
            )?;
            let root = out.hashes[&node].clone();
            answers.push(Value::obj(vec![
                ("values", values),
                ("proof", Value::str(node)),
                ("proof_hash", Value::str(root)),
            ]));
            proved += 1;
        }
        Ok(Value::obj(vec![
            (
                "variables",
                Value::Array(
                    prog.answer_vars
                        .iter()
                        .map(|v| Value::str(v.clone()))
                        .collect(),
                ),
            ),
            ("answers", Value::Array(answers)),
            ("truncated", Value::Bool(truncated)),
            ("proved", Value::Int(proved)),
            ("nodes", Value::Object(out.nodes)),
            ("rules", Value::Object(out.rules)),
            ("facts", Value::Int(self.facts as i64)),
        ]))
    }

    /// Emit the proof node for a fact (iteratively: proofs can be deep).
    fn node(
        &self,
        pred: usize,
        row: usize,
        out: &mut Proofs,
        source: &dyn Fn(usize) -> Value,
    ) -> Result<String, GateError> {
        let max = self.lim.max_output;
        // Post-order without recursion: premises get ids before their users.
        let mut stack: Vec<((usize, usize), bool)> = vec![((pred, row), false)];
        while let Some((f, expanded)) = stack.pop() {
            if out.ids.contains_key(&f) {
                continue;
            }
            let prov = &self.rels[f.0].prov[f.1];
            if !expanded {
                stack.push((f, true));
                if let Prov::Rule { premises, .. } = prov {
                    for p in premises.iter().rev() {
                        if !out.ids.contains_key(p) {
                            stack.push((*p, false));
                        }
                    }
                }
                continue;
            }
            let id = format!("n{}", out.nodes.len());
            let row = &self.rels[f.0].rows[f.1];
            out.charge(64 + 76 + row.iter().map(val_size).sum::<usize>(), max)?;
            let mut fact = vec![Value::str(self.prog.preds[f.0].name.clone())];
            fact.extend(row.iter().map(Val::to_json));
            // Every node is named by a hash of what it says and of the hashes
            // of what it rests on (a Merkle DAG, like a proof term): equal
            // derivations get equal hashes, and an answer's proof_hash fixes
            // its whole derivation down to the log events.
            let (v, digest) = match prov {
                Prov::Base(srcs) => {
                    out.charge(160 * srcs.len(), max)?;
                    let sources: Vec<Value> = srcs.iter().map(|&s| source(s)).collect();
                    let event_hashes: Vec<Value> = sources
                        .iter()
                        .map(|x| x.get("hash").cloned().unwrap_or(Value::Null))
                        .collect();
                    let digest = hex_digest(
                        canonical(&Value::obj(vec![
                            ("fact", Value::Array(fact.clone())),
                            ("sources", Value::Array(event_hashes)),
                        ]))
                        .as_bytes(),
                    );
                    (
                        Value::obj(vec![
                            ("fact", Value::Array(fact)),
                            ("sources", Value::Array(sources)),
                            ("hash", Value::str(digest.clone())),
                        ]),
                        digest,
                    )
                }
                Prov::Rule {
                    rule,
                    premises,
                    absent,
                } => {
                    let rule_id = match out.rule_ids.get(rule) {
                        Some(r) => r.clone(),
                        None => {
                            let rl = &self.prog.rules[*rule];
                            let text = self.prog.rule_text(rl);
                            out.charge(text.len() + 32 + 76 + rl.origin.len() + 16, max)?;
                            let rid = format!("r{}", out.rules.len());
                            // A rule is named by the hash of its text: a
                            // proof says exactly which rule it used.
                            let rh = hex_digest(text.as_bytes());
                            out.rules.push((
                                rid.clone(),
                                Value::obj(vec![
                                    ("text", Value::str(text)),
                                    ("origin", Value::str(rl.origin.clone())),
                                    ("hash", Value::str(rh.clone())),
                                ]),
                            ));
                            out.rule_ids.insert(*rule, rid.clone());
                            out.rule_hashes.insert(rid.clone(), rh);
                            rid
                        }
                    };
                    let mut absent_v = Vec::new();
                    for (p, pat) in absent {
                        out.charge(
                            16 + pat
                                .iter()
                                .map(|v| v.as_ref().map(val_size).unwrap_or(4))
                                .sum::<usize>(),
                            max,
                        )?;
                        let mut a = vec![Value::str(self.prog.preds[*p].name.clone())];
                        a.extend(
                            pat.iter()
                                .map(|v| v.as_ref().map(Val::to_json).unwrap_or(Value::Null)),
                        );
                        absent_v.push(Value::Array(a));
                    }
                    out.charge(8 * premises.len() + 72, max)?;
                    let premise_hashes: Vec<Value> = premises
                        .iter()
                        .map(|p| Value::str(out.hashes[&out.ids[p]].clone()))
                        .collect();
                    let digest = hex_digest(
                        canonical(&Value::obj(vec![
                            ("fact", Value::Array(fact.clone())),
                            ("rule", Value::str(out.rule_hashes[&rule_id].clone())),
                            ("premises", Value::Array(premise_hashes)),
                            ("absent", Value::Array(absent_v.clone())),
                        ]))
                        .as_bytes(),
                    );
                    (
                        Value::obj(vec![
                            ("fact", Value::Array(fact)),
                            ("rule", Value::str(rule_id)),
                            (
                                "premises",
                                Value::Array(
                                    premises
                                        .iter()
                                        .map(|p| Value::str(out.ids[p].clone()))
                                        .collect(),
                                ),
                            ),
                            ("absent", Value::Array(absent_v)),
                            ("hash", Value::str(digest.clone())),
                        ]),
                        digest,
                    )
                }
            };
            out.hashes.insert(id.clone(), digest);
            out.ids.insert(f, id.clone());
            out.nodes.push((id, v));
        }
        Ok(out.ids[&(pred, row)].clone())
    }
}

struct Proofs {
    ids: HashMap<(usize, usize), String>,
    nodes: Vec<(String, Value)>,
    rules: Vec<(String, Value)>,
    rule_ids: HashMap<usize, String>,
    /// Rule id -> hash of its text.
    rule_hashes: HashMap<String, String>,
    /// Node id -> its hash.
    hashes: HashMap<String, String>,
    size: usize,
}

impl Proofs {
    fn charge(&mut self, n: usize, max: usize) -> Result<(), GateError> {
        self.size += n;
        if self.size > max {
            return Err(limit(format!(
                "the answers and their proofs would be larger than {max} bytes"
            )));
        }
        Ok(())
    }
}

/// Bytes a value takes in the JSON result, escapes included.
fn val_size(v: &Val) -> usize {
    match v {
        Val::Int(_) => 20,
        Val::Str(s) => {
            8 + s
                .bytes()
                .map(|b| match b {
                    b'"' | b'\\' => 2,
                    b if b < 0x20 => 6,
                    _ => 1,
                })
                .sum::<usize>()
        }
    }
}

fn value(t: &Term, env: &[Option<Val>]) -> Option<Val> {
    match t {
        Term::Var(i) => env[*i].clone(),
        Term::Const(c) => Some(c.clone()),
        Term::Wild => None,
    }
}

/// Integers compare as numbers, strings by code point; values of different
/// types are never equal and never ordered.
fn compare(op: Op, a: &Val, b: &Val) -> bool {
    let same = matches!(
        (a, b),
        (Val::Int(_), Val::Int(_)) | (Val::Str(_), Val::Str(_))
    );
    match op {
        Op::Eq => a == b,
        Op::Ne => a != b,
        _ if !same => false,
        Op::Lt => a < b,
        Op::Le => a <= b,
        Op::Gt => a > b,
        Op::Ge => a >= b,
    }
}

/// Run a self-contained program (facts written as clauses) with no base
/// relations: the pure engine, for testing and general use.
pub fn run_program(src: &str, lim: Limits) -> Result<Value, GateError> {
    let prog = Program::compile(&Vec::new(), "", src, &lim)?;
    let mut eng = Engine::new(&prog, lim);
    eng.run()?;
    eng.answers(&|_| Value::Null)
}
