//! C ABI for the Python package (loaded with ctypes, no third-party tools).
//!
//! Every function takes UTF-8 C strings and returns a newly allocated UTF-8
//! C string holding a JSON result, which the caller must release with
//! `gn_free`. Results are `{"ok":true,...}` or
//! `{"ok":false,"code":...,"message":...}`. Panics never cross the boundary.

use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;

use crate::json::{self, canonical, Value};
use crate::ledger::{claim_id, Draft, VerifyStatus};
use crate::sha256::hex_digest;
use crate::store::{self, StoreError};

fn out(v: Value) -> *mut c_char {
    // canonical() never emits interior NULs: \u0000 is always escaped.
    CString::new(canonical(&v))
        .expect("no interior NUL")
        .into_raw()
}

fn fail(code: &str, message: impl Into<String>) -> Value {
    Value::obj(vec![
        ("ok", Value::Bool(false)),
        ("code", Value::str(code)),
        ("message", Value::str(message.into())),
    ])
}

fn ok(mut members: Vec<(&str, Value)>) -> Value {
    members.insert(0, ("ok", Value::Bool(true)));
    Value::obj(members)
}

fn store_fail(e: StoreError) -> Value {
    match e {
        StoreError::Io(e) => fail("E_IO", e.to_string()),
        StoreError::NotFound => fail("E_NOT_FOUND", "log file does not exist"),
        StoreError::Concurrent => fail("E_CONCURRENT", "log changed during append"),
        StoreError::Malformed { line, reason } => fail("E_MALFORMED", format!("line {line}: {reason}")),
        StoreError::TornTail { line } => fail("E_TORN_TAIL", format!("partial write at line {line}")),
        StoreError::Invalid { seq, error } => {
            fail(error.code, format!("event {seq}: {}", error.message))
        }
        StoreError::Exhausted { checked, total } => fail(
            "E_EXHAUSTED",
            format!("budget exhausted after {checked} of {total} events: not verified"),
        ),
        StoreError::CheckpointMismatch {
            expected_count,
            found_count,
        } => fail(
            "E_CHECKPOINT",
            format!("checkpoint expects {expected_count} events, log has {found_count} or a different head"),
        ),
        StoreError::Rejected(e) => fail(e.code, e.message),
    }
}

unsafe fn arg<'a>(p: *const c_char) -> Result<&'a str, Value> {
    if p.is_null() {
        return Err(fail("E_ARG", "null argument"));
    }
    CStr::from_ptr(p)
        .to_str()
        .map_err(|_| fail("E_ARG", "argument is not UTF-8"))
}

fn guarded(f: impl FnOnce() -> Value) -> *mut c_char {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(v) => out(v),
        Err(_) => out(fail("E_PANIC", "internal error")),
    }
}

/// # Safety
/// `p` must come from one of this library's functions and be freed once.
#[no_mangle]
pub unsafe extern "C" fn gn_free(p: *mut c_char) {
    if !p.is_null() {
        drop(CString::from_raw(p));
    }
}

/// # Safety
/// `data` must point to `len` readable bytes (or be null with len 0).
#[no_mangle]
pub unsafe extern "C" fn gn_sha256_hex(data: *const u8, len: usize) -> *mut c_char {
    guarded(|| {
        let bytes: &[u8] = if len == 0 {
            &[]
        } else {
            std::slice::from_raw_parts(data, len)
        };
        ok(vec![("hex", Value::str(hex_digest(bytes)))])
    })
}

/// # Safety
/// `input` must be a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn gn_canonicalize(input: *const c_char) -> *mut c_char {
    guarded(|| {
        let s = match arg(input) {
            Ok(s) => s,
            Err(e) => return e,
        };
        match json::parse(s) {
            Ok(v) => ok(vec![("canonical", Value::str(canonical(&v)))]),
            Err(e) => fail("E_JSON", e.to_string()),
        }
    })
}

/// # Safety
/// `body` must be a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn gn_claim_id(body: *const c_char) -> *mut c_char {
    guarded(|| {
        let s = match arg(body) {
            Ok(s) => s,
            Err(e) => return e,
        };
        match json::parse(s) {
            Ok(v) => match crate::schema::claim(&v) {
                Ok(_) => ok(vec![("id", Value::str(claim_id(&v)))]),
                Err(e) => fail(e.code, e.message),
            },
            Err(e) => fail("E_JSON", e.to_string()),
        }
    })
}

/// Append a draft `{"writer","record_time","kind","body"}` to the log.
///
/// # Safety
/// Both arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_append(path: *const c_char, draft: *const c_char) -> *mut c_char {
    guarded(|| {
        let (path, draft) = match (arg(path), arg(draft)) {
            (Ok(p), Ok(d)) => (p, d),
            (Err(e), _) | (_, Err(e)) => return e,
        };
        let d = match json::parse(draft) {
            Ok(v) => v,
            Err(e) => return fail("E_JSON", e.to_string()),
        };
        let field = |k: &str| d.get(k).and_then(|v| v.as_str()).map(str::to_string);
        let (writer, record_time, kind, body) = match (
            field("writer"),
            field("record_time"),
            field("kind"),
            d.get("body"),
        ) {
            (Some(w), Some(t), Some(k), Some(b)) => (w, t, k, b.clone()),
            _ => return fail("E_ARG", "draft needs writer, record_time, kind and body"),
        };
        if d.as_object().map(|o| o.len()).unwrap_or(0) != 4 {
            return fail("E_ARG", "draft has unknown fields");
        }
        match store::append(
            Path::new(path),
            Draft {
                writer,
                record_time,
                kind,
                body,
            },
        ) {
            Ok(env) => ok(vec![("event", env)]),
            Err(e) => store_fail(e),
        }
    })
}

/// Verify the whole log; `budget` < 0 means unlimited.
///
/// # Safety
/// `path` must be a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn gn_verify(path: *const c_char, budget: i64) -> *mut c_char {
    guarded(|| {
        let path = match arg(path) {
            Ok(p) => p,
            Err(e) => return e,
        };
        let budget = if budget < 0 {
            None
        } else {
            Some(budget as usize)
        };
        let status = match store::audit(Path::new(path), budget) {
            Ok(st) => st,
            Err(e) => return store_fail(e),
        };
        match status {
            VerifyStatus::Ok { count, head } => ok(vec![
                ("status", Value::str("ok")),
                ("count", Value::Int(count as i64)),
                ("head", Value::str(head)),
            ]),
            VerifyStatus::Broken { seq, error } => {
                fail(error.code, format!("event {seq}: {}", error.message))
            }
            VerifyStatus::Exhausted { checked, total } => fail(
                "E_EXHAUSTED",
                format!("budget exhausted after {checked} of {total} events: not verified"),
            ),
        }
    })
}

/// Derived state. `as_of` may be null (latest). `compartments` may be null
/// (all) or a JSON array of compartment names.
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_state(
    path: *const c_char,
    as_of: *const c_char,
    compartments: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let path = match arg(path) {
            Ok(p) => p,
            Err(e) => return e,
        };
        let as_of = if as_of.is_null() {
            None
        } else {
            match arg(as_of) {
                Ok(t) if crate::schema::is_timestamp(t) => Some(t.to_string()),
                Ok(_) => return fail("E_TIME_FORMAT", "as_of must be YYYY-MM-DDTHH:MM:SSZ"),
                Err(e) => return e,
            }
        };
        let comps = match compartments_arg(compartments) {
            Ok(c) => c,
            Err(e) => return e,
        };
        let result = store::with_ledger(Path::new(path), |full| {
            let view;
            let ledger = match &as_of {
                Some(t) => {
                    view = full.as_of(t);
                    &view
                }
                None => full,
            };
            let state = ledger.state.to_value(comps.as_deref());
            // The digest covers exactly what is shown. Count and head describe
            // the whole log, so a filtered view leaves them out.
            let digest = hex_digest(canonical(&state).as_bytes());
            let mut members = vec![("state", state), ("digest", Value::str(digest))];
            if comps.is_none() {
                members.push(("count", Value::Int(ledger.events.len() as i64)));
                members.push(("head", Value::str(ledger.head())));
            }
            ok(members)
        });
        result.unwrap_or_else(store_fail)
    })
}

/// An optional JSON array of compartment names.
unsafe fn compartments_arg(compartments: *const c_char) -> Result<Option<Vec<String>>, Value> {
    if compartments.is_null() {
        return Ok(None);
    }
    let c = match arg(compartments).map(json::parse) {
        Ok(Ok(v)) => v,
        Ok(Err(e)) => return Err(fail("E_JSON", e.to_string())),
        Err(e) => return Err(e),
    };
    match c.as_array() {
        Some(items) if items.iter().all(|i| i.as_str().is_some()) => Ok(Some(
            items
                .iter()
                .map(|i| i.as_str().unwrap().to_string())
                .collect(),
        )),
        _ => Err(fail("E_ARG", "compartments must be an array of strings")),
    }
}

/// Just the writers, as `gn_state` shows them (all, or those visible
/// through `compartments`): what most callers of a full state dump need,
/// without rendering every claim.
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_writers(
    path: *const c_char,
    compartments: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let path = match arg(path) {
            Ok(p) => p,
            Err(e) => return e,
        };
        let comps = match compartments_arg(compartments) {
            Ok(c) => c,
            Err(e) => return e,
        };
        let result = store::with_ledger(Path::new(path), |l| {
            let none = crate::ledger::Selection::default();
            let v = l.state.to_value_sel(comps.as_deref(), Some(&none));
            ok(vec![(
                "writers",
                v.get("writers").cloned().unwrap_or(Value::Null),
            )])
        });
        result.unwrap_or_else(store_fail)
    })
}

/// Part of the state, as `gn_state` shows it: the writers, plus only the
/// claims, episodes and handoffs named in `select` (a JSON object with
/// optional arrays "claims", "episodes", "handoffs"), as seen through
/// `compartments` (null: all). Ids that do not exist or are not visible are
/// simply absent.
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_select(
    path: *const c_char,
    compartments: *const c_char,
    select: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let (path, select) = match (arg(path), arg(select)) {
            (Ok(p), Ok(s)) => (p, s),
            (Err(e), _) | (_, Err(e)) => return e,
        };
        let comps = match compartments_arg(compartments) {
            Ok(c) => c,
            Err(e) => return e,
        };
        let sel = match json::parse(select) {
            Ok(v) if v.as_object().is_some() => v,
            Ok(_) => return fail("E_ARG", "select must be an object"),
            Err(e) => return fail("E_JSON", e.to_string()),
        };
        let mut chosen = crate::ledger::Selection::default();
        for (k, v) in sel.as_object().unwrap() {
            let set = match k.as_str() {
                "claims" => &mut chosen.claims,
                "episodes" => &mut chosen.episodes,
                "handoffs" => &mut chosen.handoffs,
                _ => return fail("E_ARG", format!("select: unknown part '{k}'")),
            };
            match v.as_array() {
                Some(ids) if ids.iter().all(|i| i.as_str().is_some()) => {
                    set.extend(ids.iter().map(|i| i.as_str().unwrap().to_string()))
                }
                _ => return fail("E_ARG", format!("select: '{k}' must be an array of ids")),
            }
        }
        let result = store::with_ledger(Path::new(path), |l| {
            ok(vec![(
                "state",
                l.state.to_value_sel(comps.as_deref(), Some(&chosen)),
            )])
        });
        result.unwrap_or_else(store_fail)
    })
}

/// Answer a Datalog query over the memory as `reader` sees it: only the
/// reader's compartments, as of `as_of` (null = latest).
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_query(
    path: *const c_char,
    as_of: *const c_char,
    reader: *const c_char,
    program: *const c_char,
) -> *mut c_char {
    gn_query_opts(path, as_of, reader, program, std::ptr::null())
}

/// As gn_query, with options as JSON: {"proofs": n} gives proofs for the
/// first n answers only (the rest carry a null proof).
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_query_opts(
    path: *const c_char,
    as_of: *const c_char,
    reader: *const c_char,
    program: *const c_char,
    opts: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let (path, reader, program) = match (arg(path), arg(reader), arg(program)) {
            (Ok(p), Ok(r), Ok(q)) => (p, r, q),
            (Err(e), _, _) | (_, Err(e), _) | (_, _, Err(e)) => return e,
        };
        let mut lim = crate::datalog::Limits::default();
        if !opts.is_null() {
            let o = match arg(opts).map(crate::json::parse) {
                Ok(Ok(v)) => v,
                Ok(Err(_)) => return fail("E_ARG", "options must be JSON"),
                Err(e) => return e,
            };
            for (k, _) in o.as_object().unwrap_or(&[]) {
                if k != "proofs" {
                    return fail("E_ARG", format!("unknown option '{k}'"));
                }
            }
            if let Some(n) = o.get("proofs") {
                match n.as_int() {
                    Some(n) if n >= 0 => lim.max_proofs = n as usize,
                    _ => return fail("E_ARG", "'proofs' must be a non-negative integer"),
                }
            }
        }
        let as_of = if as_of.is_null() {
            None
        } else {
            match arg(as_of) {
                Ok(t) if crate::schema::is_timestamp(t) => Some(t.to_string()),
                Ok(_) => return fail("E_TIME_FORMAT", "as_of must be YYYY-MM-DDTHH:MM:SSZ"),
                Err(e) => return e,
            }
        };
        let result = store::with_ledger(Path::new(path), |full| {
            // The reader's current compartments decide what it may see, at
            // any moment it asks about.
            let comps = match full.state.writers.get(reader) {
                Some(w) => w.compartments.clone(),
                None => return fail("E_REF", format!("writer '{reader}' does not exist")),
            };
            let view;
            let ledger = match &as_of {
                Some(t) => {
                    view = full.as_of(t);
                    &view
                }
                None => full,
            };
            match crate::query::run(ledger, &comps, program, lim.clone()) {
                Ok(v) => {
                    // The moment the result describes: the one asked for, or
                    // else the time of the last event in the reader's view.
                    let view_time = v.get("through").and_then(|t| t.get("time")).cloned();
                    let at = as_of
                        .clone()
                        .map(Value::str)
                        .or(view_time)
                        .unwrap_or(Value::Null);
                    // Whose view this is, so a checker can tell which events
                    // the reader could see (the reader's own compartments).
                    let view = Value::obj(vec![
                        ("reader", Value::str(reader)),
                        (
                            "compartments",
                            Value::Array(comps.iter().map(|c| Value::str(c.clone())).collect()),
                        ),
                    ]);
                    ok(vec![("result", v.with("as_of", at).with("view", view))])
                }
                Err(e) => fail(e.code, e.message),
            }
        });
        result.unwrap_or_else(store_fail)
    })
}

/// Recall what was said, ranked, as `reader` sees the memory (as of
/// `as_of`, null = latest). `request` is JSON: {query, k?, kinds?, speaker?,
/// after?, before?, now?, context?}.
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_recall(
    path: *const c_char,
    as_of: *const c_char,
    reader: *const c_char,
    request: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let (path, reader, request) = match (arg(path), arg(reader), arg(request)) {
            (Ok(p), Ok(r), Ok(q)) => (p, r, q),
            (Err(e), _, _) | (_, Err(e), _) | (_, _, Err(e)) => return e,
        };
        let as_of = if as_of.is_null() {
            None
        } else {
            match arg(as_of) {
                Ok(t) if crate::schema::is_timestamp(t) => Some(t.to_string()),
                Ok(_) => return fail("E_TIME_FORMAT", "as_of must be YYYY-MM-DDTHH:MM:SSZ"),
                Err(e) => return e,
            }
        };
        let req = match json::parse(request) {
            Ok(v) => match crate::recall::Request::from_json(&v) {
                Ok(r) => r,
                Err(e) => return fail(e.code, e.message),
            },
            Err(e) => return fail("E_JSON", e.to_string()),
        };
        let result = store::with_ledger(Path::new(path), |full| {
            let comps = match full.state.writers.get(reader) {
                Some(w) => w.compartments.clone(),
                None => return fail("E_REF", format!("writer '{reader}' does not exist")),
            };
            let view;
            let ledger = match &as_of {
                Some(t) => {
                    view = full.as_of(t);
                    &view
                }
                None => full,
            };
            match crate::recall::run(ledger, &comps, &req) {
                Ok(v) => {
                    let through = crate::query::view_end(ledger, &comps);
                    let at = as_of
                        .clone()
                        .map(Value::str)
                        .or(through.get("time").cloned())
                        .unwrap_or(Value::Null);
                    ok(vec![(
                        "result",
                        v.with("through", through).with("as_of", at),
                    )])
                }
                Err(e) => fail(e.code, e.message),
            }
        });
        result.unwrap_or_else(store_fail)
    })
}

/// The built-in views, as Datalog source, so anyone can read and re-check
/// exactly what each one means.
#[no_mangle]
pub extern "C" fn gn_views() -> *mut c_char {
    guarded(|| {
        let schema = crate::query::schema()
            .into_iter()
            .map(|(n, a)| Value::Array(vec![Value::str(n), Value::Int(a as i64)]))
            .collect();
        ok(vec![
            ("views", Value::str(crate::query::VIEWS)),
            ("schema", Value::Array(schema)),
        ])
    })
}

/// Run a self-contained Datalog program (facts written as clauses).
///
/// # Safety
/// `program` must be a valid NUL-terminated string.
#[no_mangle]
pub unsafe extern "C" fn gn_datalog(program: *const c_char) -> *mut c_char {
    guarded(|| {
        let program = match arg(program) {
            Ok(p) => p,
            Err(e) => return e,
        };
        match crate::datalog::run_program(program, crate::datalog::Limits::default()) {
            Ok(v) => ok(vec![("result", v)]),
            Err(e) => fail(e.code, e.message),
        }
    })
}

/// Check the log still contains the checkpoint `count`/`head`.
///
/// # Safety
/// Both strings must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_checkpoint(
    path: *const c_char,
    count: i64,
    head: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let (path, head) = match (arg(path), arg(head)) {
            (Ok(p), Ok(h)) => (p, h),
            (Err(e), _) | (_, Err(e)) => return e,
        };
        if count < 0 {
            return fail("E_ARG", "count must be >= 0");
        }
        match store::verify_checkpoint(Path::new(path), count as usize, head) {
            Ok(()) => ok(vec![]),
            Err(e) => store_fail(e),
        }
    })
}

/// The reader's compartments and the ledger as of `as_of` (or latest).
fn reader_view<T>(
    path: &str,
    as_of: Option<String>,
    reader: &str,
    f: impl FnOnce(&crate::ledger::Ledger, &[String]) -> T,
) -> Result<Result<T, Value>, crate::store::StoreError> {
    store::with_ledger(Path::new(path), |full| {
        let comps = match full.state.writers.get(reader) {
            Some(w) => w.compartments.clone(),
            None => {
                return Err(fail(
                    "E_UNKNOWN_WRITER",
                    format!("reader '{reader}' is not registered"),
                ))
            }
        };
        let view;
        let ledger = match &as_of {
            Some(t) => {
                view = full.as_of(t);
                &view
            }
            None => full,
        };
        Ok(f(ledger, &comps))
    })
}

unsafe fn opt_time(as_of: *const c_char) -> Result<Option<String>, Value> {
    if as_of.is_null() {
        return Ok(None);
    }
    match arg(as_of) {
        Ok(t) if crate::schema::is_timestamp(t) => Ok(Some(t.to_string())),
        Ok(_) => Err(fail("E_TIME_FORMAT", "as_of must be YYYY-MM-DDTHH:MM:SSZ")),
        Err(e) => Err(e),
    }
}

/// The pieces of work the reader can see, most recently active first.
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_frames(
    path: *const c_char,
    as_of: *const c_char,
    reader: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let (path, reader) = match (arg(path), arg(reader)) {
            (Ok(p), Ok(r)) => (p, r),
            (Err(e), _) | (_, Err(e)) => return e,
        };
        let as_of = match opt_time(as_of) {
            Ok(t) => t,
            Err(e) => return e,
        };
        match reader_view(path, as_of, reader, |l, comps| l.state.frames(comps)) {
            Ok(Ok(v)) => ok(vec![("frames", v)]),
            Ok(Err(e)) => e,
            Err(e) => store_fail(e),
        }
    })
}

/// Just the state that resuming a frame needs (see State::resume_selection),
/// as the reader sees it. `frame` may be null (the most recently active).
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_resume_state(
    path: *const c_char,
    as_of: *const c_char,
    reader: *const c_char,
    frame: *const c_char,
    turns: i64,
) -> *mut c_char {
    guarded(|| {
        let (path, reader) = match (arg(path), arg(reader)) {
            (Ok(p), Ok(r)) => (p, r),
            (Err(e), _) | (_, Err(e)) => return e,
        };
        let frame = if frame.is_null() {
            None
        } else {
            match arg(frame) {
                Ok(f) => Some(f.to_string()),
                Err(e) => return e,
            }
        };
        let as_of = match opt_time(as_of) {
            Ok(t) => t,
            Err(e) => return e,
        };
        let turns = turns.clamp(0, 1000) as usize;
        match reader_view(path, as_of, reader, |l, comps| {
            let (sel, fr) = l.state.resume_selection(comps, frame.as_deref(), turns);
            (l.state.to_value_sel(Some(comps), Some(&sel)), fr)
        }) {
            Ok(Ok((st, fr))) => ok(vec![
                ("state", st),
                ("frame", fr.map(Value::str).unwrap_or(Value::Null)),
            ]),
            Ok(Err(e)) => e,
            Err(e) => store_fail(e),
        }
    })
}

/// The full id of the one claim the reader can see whose id starts with
/// `prefix` (at least "c:" and 8 hex digits): a short id, as models are
/// shown them, resolved. Missing, hidden and ambiguous are refused alike
/// unless ambiguous among visible claims.
///
/// # Safety
/// Non-null arguments must be valid NUL-terminated strings.
#[no_mangle]
pub unsafe extern "C" fn gn_resolve(
    path: *const c_char,
    reader: *const c_char,
    prefix: *const c_char,
) -> *mut c_char {
    guarded(|| {
        let (path, reader, prefix) = match (arg(path), arg(reader), arg(prefix)) {
            (Ok(p), Ok(r), Ok(x)) => (p, r, x),
            (Err(e), _, _) | (_, Err(e), _) | (_, _, Err(e)) => return e,
        };
        let hex = prefix.strip_prefix("c:").unwrap_or("");
        if hex.len() < 8
            || hex.len() > 64
            || !hex
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return fail(
                "E_REF",
                "a claim id is c: and at least 8 lower-case hex digits",
            );
        }
        match reader_view(path, None, reader, |l, comps| {
            let st = &l.state;
            let found: Vec<&String> = st
                .claims
                .range(prefix.to_string()..)
                .take_while(|(id, _)| id.starts_with(prefix))
                .filter(|(_, c)| comps.iter().any(|x| *x == c.compartment))
                .map(|(id, _)| id)
                .take(2)
                .collect();
            match found.as_slice() {
                [one] => ok(vec![("id", Value::str((*one).clone()))]),
                [] => fail("E_REF", format!("no claim '{prefix}' that you can see")),
                _ => fail(
                    "E_REF",
                    format!("'{prefix}' names more than one claim: give more of its id"),
                ),
            }
        }) {
            Ok(Ok(v)) => v,
            Ok(Err(e)) => e,
            Err(e) => store_fail(e),
        }
    })
}
