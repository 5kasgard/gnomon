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
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let lines: Vec<&str> = text.split('\n').collect();
    // text ends with '\n' => last split element is "".
    let complete = lines.len() - 1;
    if !lines[complete].is_empty() {
        return Err(StoreError::TornTail { line: complete });
    }
    let mut events = Vec::with_capacity(complete);
    for (i, line) in lines[..complete].iter().enumerate() {
        let v = json::parse(line).map_err(|e| StoreError::Malformed {
            line: i,
            reason: e.to_string(),
        })?;
        if canonical(&v) != *line {
            return Err(StoreError::Malformed {
                line: i,
                reason: "line is not in canonical form".to_string(),
            });
        }
        events.push(v);
    }
    Ok(events)
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
        }
    }
    let text = utf8(bytes)?;
    let ledger = verify_events(&parse_events(&text)?, None)?;
    Ok(Cached {
        bytes: text.into_bytes(),
        ledger,
    })
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
    let c = verified_for(&key, bytes)?;
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
