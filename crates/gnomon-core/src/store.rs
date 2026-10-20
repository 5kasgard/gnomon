//! The log on disk: one canonical JSON envelope per line, append-only.
//!
//! Reading is strict. A line that is not exactly the canonical form of its
//! own content counts as tampering, and a final line without a newline is a
//! torn write from a crash, reported rather than silently dropped.
//!
//! Speed: a verified log is cached in memory together with the exact bytes
//! it was verified from. Every later operation re-reads the file and compares
//! it byte-for-byte with those bytes before reusing the cache, so any change
//! to the file (tampering, truncation, another process) forces a full
//! re-verification. Appends are then linear in file size, not quadratic.
//!
//! Concurrency: every operation locks the log file itself (shared for reads,
//! exclusive for appends) across its whole read-check-write. Because the
//! lock is on the file, every path that reaches it (symlinks, `./`
//! spellings) shares one lock, and there is no lock file to delete.
//!
//! Starting fast: a new process does not replay the whole log if a
//! snapshot of its verified state, made from exactly the bytes the log still
//! begins with, is saved beside it (see [`crate::snapshot`]); only events
//! after it go through the gate. Snapshots are refreshed as the log grows.
//!
//! An empty file is treated exactly like a missing one.

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use crate::json::{self, canonical, Value};
use crate::ledger::{Draft, Ledger, VerifyStatus};
use crate::schema::GateError;

#[derive(Debug)]
pub enum StoreError {
    Io(std::io::Error),
    /// The log file does not exist (only appends may create it).
    NotFound,
    /// Line `line` (0-based) is not valid canonical JSON.
    Malformed { line: usize, reason: String },
    /// The file ends with a partial line: a crash interrupted a write.
    TornTail { line: usize },
    /// The log failed verification at `seq`.
    Invalid { seq: usize, error: GateError },
    /// Verification ran out of budget: not a pass.
    Exhausted { checked: usize, total: usize },
    /// A checkpoint expected more events or a different head.
    CheckpointMismatch { expected_count: usize, found_count: usize },
    /// The file changed between verification and write (should be
    /// impossible under the lock; checked anyway).
    Concurrent,
    Rejected(GateError),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}

impl From<std::io::Error> for StoreError {
    fn from(e: std::io::Error) -> Self {
        StoreError::Io(e)
    }
}

fn parse_events(text: &str) -> Result<Vec<Value>, StoreError> {
    Ok(parse_lines(text)?.into_iter().map(|(v, _)| v).collect())
}

/// Each line parsed, checked to be canonical, and its envelope hash (the
/// hash of the event without its "hash" member) worked out from the line
/// itself, in parallel: a canonical line minus its top-level `,"hash":"…"`
/// is exactly the canonical form of the event without it.
fn parse_lines(text: &str) -> Result<Vec<(Value, String)>, StoreError> {
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let lines: Vec<&str> = text.split('\n').collect();
    // text ends with '\n' => last split element is "".
    let complete = lines.len() - 1;
    if !lines[complete].is_empty() {
        return Err(StoreError::TornTail { line: complete });
    }
    let lines = &lines[..complete];
    let one = |i: usize, line: &str| -> Result<(Value, String), StoreError> {
        let v = json::parse(line).map_err(|e| StoreError::Malformed { line: i, reason: e.to_string() })?;
        if canonical(&v) != *line {
            return Err(StoreError::Malformed { line: i, reason: "line is not in canonical form".to_string() });
        }
        let h = match v.get("hash").and_then(|h| h.as_str()) {
            Some(h) => {
                let member = format!(",\"hash\":\"{h}\"");
                // The top-level member is the last: only flat members follow it.
                match line.rfind(&member) {
                    Some(at) => {
                        let mut rest = String::with_capacity(line.len());
                        rest.push_str(&line[..at]);
                        rest.push_str(&line[at + member.len()..]);
                        crate::sha256::hex_digest(rest.as_bytes())
                    }
                    None => String::new(), // no hash member: the gate will say so
                }
            }
            None => String::new(),
        };
        Ok((v, h))
    };
    let threads = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1).min(8);
    if threads < 2 || lines.len() < 2048 {
        return lines.iter().enumerate().map(|(i, l)| one(i, l)).collect();
    }
    let chunk = lines.len().div_ceil(threads);
    let parts: Vec<Result<Vec<(Value, String)>, StoreError>> = std::thread::scope(|scope| {
        let handles: Vec<_> = lines
            .chunks(chunk)
            .enumerate()
            .map(|(k, ls)| {
                let one = &one;
                scope.spawn(move || ls.iter().enumerate().map(|(i, l)| one(k * chunk + i, l)).collect())
            })
            .collect();
        handles.into_iter().map(|h| h.join().expect("parse thread")).collect()
    });
    let mut out = Vec::with_capacity(lines.len());
    for p in parts {
        out.extend(p?);
    }
    Ok(out)
}

/// Replay parsed lines through the gate, with their envelope hashes.
fn verify_lines(mut ledger: Ledger, lines: Vec<(Value, String)>, base: usize) -> Result<Ledger, StoreError> {
    for (i, (ev, h)) in lines.into_iter().enumerate() {
        let known = if h.is_empty() { None } else { Some(h) };
        ledger.apply_hashed(ev, known).map_err(|error| StoreError::Invalid { seq: base + i, error })?;
    }
    Ok(ledger)
}

/// Open an existing, non-empty log for reading under a shared lock.
fn open_for_read(path: &Path) -> Result<(File, Vec<u8>), StoreError> {
    let mut f = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(StoreError::NotFound),
        Err(e) => return Err(e.into()),
    };
    f.lock_shared()?;
    let mut bytes = Vec::new();
    f.read_to_end(&mut bytes)?;
    if bytes.is_empty() {
        return Err(StoreError::NotFound);
    }
    Ok((f, bytes))
}

fn utf8(bytes: Vec<u8>) -> Result<String, StoreError> {
    String::from_utf8(bytes).map_err(|_| StoreError::Malformed {
        line: 0,
        reason: "log is not valid UTF-8".to_string(),
    })
}

/// Read raw events from an existing log without checking the gate rules.
pub fn read_events(path: &Path) -> Result<Vec<Value>, StoreError> {
    let (_f, bytes) = open_for_read(path)?;
    parse_events(&utf8(bytes)?)
}

fn verify_events(events: &[Value], budget: Option<usize>) -> Result<Ledger, StoreError> {
    match Ledger::verify(events, budget) {
        (VerifyStatus::Ok { .. }, ledger) => Ok(ledger),
        (VerifyStatus::Broken { seq, error }, _) => Err(StoreError::Invalid { seq, error }),
        (VerifyStatus::Exhausted { checked, total }, _) => {
            Err(StoreError::Exhausted { checked, total })
        }
    }
}

/// Replay an existing log from scratch through every rule (no cache), at
/// most `budget` events: the full audit. Lines are parsed and hashed in
/// parallel; the gate runs in order.
pub fn audit(path: &Path, budget: Option<usize>) -> Result<VerifyStatus, StoreError> {
    let (_f, bytes) = open_for_read(path)?;
    let lines = parse_lines(&utf8(bytes)?)?;
    let total = lines.len();
    let mut ledger = Ledger::new();
    for (i, (ev, h)) in lines.into_iter().enumerate() {
        if budget.is_some_and(|b| i >= b) {
            return Ok(VerifyStatus::Exhausted { checked: i, total });
        }
        if let Err(error) = ledger.apply_hashed(ev, if h.is_empty() { None } else { Some(h) }) {
            return Ok(VerifyStatus::Broken { seq: i, error });
        }
    }
    Ok(VerifyStatus::Ok { count: total, head: ledger.head() })
}

/// Open and fully verify an existing log. `budget` limits how many events
/// are checked; running out is an error, never a pass.
pub fn open(path: &Path, budget: Option<usize>) -> Result<Ledger, StoreError> {
    verify_events(&read_events(path)?, budget)
}

/// Verify a log against a checkpoint taken earlier (count and head hash):
/// detects truncation and rewritten history, which a chain alone cannot.
pub fn verify_checkpoint(path: &Path, count: usize, head: &str) -> Result<(), StoreError> {
    let ledger = open(path, None)?;
    if ledger.events.len() < count {
        return Err(StoreError::CheckpointMismatch {
            expected_count: count,
            found_count: ledger.events.len(),
        });
    }
    let at = if count == 0 {
        crate::ledger::GENESIS_PREV.to_string()
    } else {
        ledger.events[count - 1]
            .get("hash")
            .and_then(|h| h.as_str())
            .unwrap_or("")
            .to_string()
    };
    if at != head {
        return Err(StoreError::CheckpointMismatch {
            expected_count: count,
            found_count: ledger.events.len(),
        });
    }
    Ok(())
}

struct Cached {
    bytes: Vec<u8>,
    ledger: Ledger,
    /// Events covered by the newest snapshot this process knows of.
    snap_events: usize,
}

/// Save a new snapshot once the log has grown past the last one by 1% (at
/// least 256 events, at most 4,096), so a new process replays little, and
/// saving, which costs the whole log, happens rarely. Called only with no
/// lock held on the log.
fn maybe_snapshot(key: &Path, c: &mut Cached) {
    let n = c.ledger.events.len();
    let grown = n.saturating_sub(c.snap_events);
    let min = crate::snapshot::min_events();
    if n >= min && grown >= (n / 100).clamp(256, 4096).min(min) {
        crate::snapshot::save(key, &c.ledger, &c.bytes);
        c.snap_events = n;
    }
}

/// Bounded so that touching many logs cannot grow memory without limit.
const CACHE_MAX_ENTRIES: usize = 16;
const CACHE_MAX_BYTES: usize = 256 * 1024 * 1024;

#[derive(Default)]
struct Cache {
    entries: HashMap<PathBuf, (Cached, u64)>,
    tick: u64,
}

fn cache() -> &'static Mutex<Cache> {
    static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(Cache::default()))
}

fn cache_key(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// A verified ledger for exactly `bytes`: reused from the cache only if the
/// cached bytes are identical, otherwise fully verified from scratch.
fn verified_for(key: &Path, bytes: Vec<u8>) -> Result<Cached, StoreError> {
    {
        let mut c = cache().lock().unwrap_or_else(|p| p.into_inner());
        if let Some((entry, _)) = c.entries.remove(key) {
            if entry.bytes == bytes {
                return Ok(entry);
            }
            // Grown by appends (from another process, say): the verified
            // part is byte-identical, so only the new lines need checking,
            // each through the same gate a full replay would use.
            if bytes.len() > entry.bytes.len()
                && bytes.starts_with(&entry.bytes)
                && (entry.bytes.is_empty() || entry.bytes.ends_with(b"\n"))
            {
                drop(c);
                return extend_verified(entry, bytes);
            }
        }
    }
    use crate::snapshot::Loaded;
    let loaded = crate::snapshot::load(key, &bytes);
    if matches!(loaded, Loaded::Unusable) {
        crate::snapshot::discard(key); // so a good one can take its place
    }
    let c = match loaded {
        Loaded::Usable(ledger, covered) => {
            let snap_events = ledger.events.len();
            let start = Cached { bytes: bytes[..covered].to_vec(), ledger, snap_events };
            let c = if covered == bytes.len() { start } else { extend_verified(start, bytes)? };
            if crate::snapshot::checking() {
                let text = utf8(c.bytes.clone())?;
                let full = verify_lines(Ledger::new(), parse_lines(&text)?, 0)?;
                if full != c.ledger {
                    let (a, b) = (&full, &c.ledger);
                    let what = if a.events != b.events { "events" } else if a.state.writers != b.state.writers { "writers" }
                        else if a.state.episodes != b.state.episodes { "episodes" } else if a.state.claims != b.state.claims { "claims" }
                        else if a.state.supersessions != b.state.supersessions { "supersessions" } else if a.state.verdicts != b.state.verdicts { "verdicts" }
                        else if a.state.appeals != b.state.appeals { "appeals" } else if a.state.handoffs != b.state.handoffs { "handoffs" }
                        else if a.state.appeals_by_claim != b.state.appeals_by_claim { "appeals_by_claim" } else { "schema_version" };
                    eprintln!("gnomon: a snapshot of {} differs from a full replay in {what} ({} vs {} events)", key.display(), a.events.len(), b.events.len());
                    std::process::abort();
                }
            }
            c
        }
        Loaded::Absent | Loaded::Unusable => {
            let text = utf8(bytes)?;
            let ledger = verify_lines(Ledger::new(), parse_lines(&text)?, 0)?;
            Cached { bytes: text.into_bytes(), ledger, snap_events: 0 }
        }
    };
    Ok(c)
}

fn extend_verified(mut entry: Cached, bytes: Vec<u8>) -> Result<Cached, StoreError> {
    let base = entry.ledger.events.len();
    let tail = utf8(bytes[entry.bytes.len()..].to_vec())?;
    let lines = parse_lines(&tail).map_err(|e| match e {
        StoreError::Malformed { line, reason } => StoreError::Malformed { line: base + line, reason },
        StoreError::TornTail { line } => StoreError::TornTail { line: base + line },
        other => other,
    })?;
    entry.ledger = verify_lines(entry.ledger, lines, base)?;
    entry.bytes = bytes;
    Ok(entry)
}

fn put_cache(key: PathBuf, entry: Cached) {
    let mut c = cache().lock().unwrap_or_else(|p| p.into_inner());
    if entry.bytes.len() > CACHE_MAX_BYTES {
        return; // too large to keep; it will be re-verified next time
    }
    c.tick += 1;
    let tick = c.tick;
    c.entries.insert(key, (entry, tick));
    loop {
        let total: usize = c.entries.values().map(|(e, _)| e.bytes.len()).sum();
        if c.entries.len() <= CACHE_MAX_ENTRIES && total <= CACHE_MAX_BYTES {
            break;
        }
        let oldest = c
            .entries
            .iter()
            .min_by_key(|(_, (_, t))| *t)
            .map(|(k, _)| k.clone())
            .expect("non-empty");
        c.entries.remove(&oldest);
    }
}

/// Number of logs currently cached (for tests and diagnostics).
pub fn cached_logs() -> usize {
    cache().lock().unwrap_or_else(|p| p.into_inner()).entries.len()
}

/// Run `f` on the verified ledger of an existing log. The file is read
/// under a shared lock, which is released before `f` runs: `f` sees a
/// consistent snapshot and may itself append (from any thread) without
/// deadlocking.
pub fn with_ledger<T>(path: &Path, f: impl FnOnce(&Ledger) -> T) -> Result<T, StoreError> {
    let bytes = {
        let (_file, bytes) = open_for_read(path)?;
        bytes
    }; // lock released here
    let key = cache_key(path);
    let mut c = verified_for(&key, bytes)?;
    maybe_snapshot(&key, &mut c); // no lock is held here
    let out = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(&c.ledger)));
    // Only cache the snapshot if no newer bytes were cached meanwhile.
    {
        let current = cache().lock().unwrap_or_else(|p| p.into_inner()).entries.contains_key(&key);
        if !current {
            put_cache(key, c);
        }
    }
    match out {
        Ok(v) => Ok(v),
        Err(p) => std::panic::resume_unwind(p),
    }
}

/// Append one draft under an exclusive lock on the log file: confirm the
/// file is exactly what was verified (or verify it fully), run the gate,
/// write, fsync. An empty or missing file starts a new log.
pub fn append(path: &Path, draft: Draft) -> Result<Value, StoreError> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .append(true)
        .open(path)?;
    f.lock()?;
    let mut bytes = Vec::new();
    f.read_to_end(&mut bytes)?;
    let len_before = bytes.len() as u64;
    let key = cache_key(path);
    let mut c = verified_for(&key, bytes)?;
    let env = match c.ledger.append(draft) {
        Ok(env) => env,
        Err(e) => {
            put_cache(key, c); // the ledger is unchanged on rejection
            return Err(StoreError::Rejected(e));
        }
    };
    if f.seek(SeekFrom::End(0))? != len_before {
        return Err(StoreError::Concurrent);
    }
    let mut line = canonical(&env);
    line.push('\n');
    f.write_all(line.as_bytes())?;
    f.sync_data()?;
    c.bytes.extend_from_slice(line.as_bytes());
    put_cache(key, c);
    Ok(env)
}
