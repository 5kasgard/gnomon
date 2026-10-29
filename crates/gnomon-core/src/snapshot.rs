//! Snapshots: the verified state of a log, saved beside it, so that a new
//! process does not replay the whole log through the gate before it can
//! answer.
//!
//! A snapshot is a cache, never a source of truth:
//!
//! * It names the exact bytes it was made from: their length and SHA-256.
//!   It is used only if the log still begins with exactly those bytes, its
//!   event count is the number of lines there, and its last event's hash is
//!   the one on the last of them. Any other change to the log (an edit, a
//!   truncation) makes it useless, and the log is verified from scratch as
//!   before. Events added after it are checked through the gate as usual.
//! * It names the code that made it: a hash of the source of the gate, the
//!   schema, canonical JSON, hashing, the store and this file. A build with
//!   any change to how state is derived ignores snapshots made by another.
//! * It is sealed with an HMAC-SHA-256 under a secret key of the user's own
//!   (`snapshot.key` in `$GNOMON_STATE_DIR`, else `$XDG_STATE_HOME/gnomon`,
//!   else `~/.local/state/gnomon`; made on first use, readable by its owner
//!   only). Anyone who can write beside the log but not read that key
//!   cannot make a snapshot that will be used: a forged or damaged one is
//!   ignored, and the log is verified in full.
//! * Its directory and file must belong to the user running the code and be
//!   writable by no one else, or they are neither read nor written. The file
//!   is written under a fresh name (never through an existing file or link)
//!   and renamed into place.
//! * Decoding is bounded: nesting no deeper than canonical JSON allows, and
//!   no allocation larger than the file justifies.
//! * The full audit (`store::audit`, `gn_verify`) never reads it.
//!
//! Snapshots need a Unix system (for the key and the ownership checks);
//! elsewhere none are made, and every new process replays the log.
//!
//! The format is a plain binary encoding of the ledger: little-endian
//! LEB128 lengths, then the events and the derived state, field by field.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use crate::json::Value;
use crate::ledger::{
    LeaseInfo,
    AppealInfo, AppointmentInfo, ClaimInfo, EpisodeInfo, HandoffInfo, Ledger, PositionInfo,
    Resolution, State, SupersessionInfo, VerdictInfo, WriterInfo,
};
use crate::sha256::digest;

const MAGIC: &[u8; 8] = b"GNSNAP5\n";
/// Magic, code hash, bytes covered, their hash, HMAC.
const HEADER: usize = 8 + 32 + 8 + 32 + 32;

/// Logs with fewer events than this are cheap to verify: no snapshot.
pub const MIN_EVENTS: usize = 1000;

/// `MIN_EVENTS`, unless `GNOMON_SNAPSHOT_MIN_EVENTS` says otherwise (for
/// tests, which snapshot every log to compare snapshots with replays).
pub fn min_events() -> usize {
    static N: OnceLock<usize> = OnceLock::new();
    *N.get_or_init(|| {
        std::env::var("GNOMON_SNAPSHOT_MIN_EVENTS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(MIN_EVENTS)
            .max(1)
    })
}

/// With `GNOMON_SNAPSHOT_CHECK=1`, every ledger started from a snapshot is
/// also replayed in full and the two compared; a difference aborts the
/// process. For tests.
pub fn checking() -> bool {
    static C: OnceLock<bool> = OnceLock::new();
    *C.get_or_init(|| std::env::var("GNOMON_SNAPSHOT_CHECK").is_ok_and(|v| v == "1"))
}

/// Where the snapshot of the log at `log` lives.
pub fn path_for(log: &Path) -> PathBuf {
    let mut p = log.as_os_str().to_owned();
    p.push(".d");
    PathBuf::from(p).join("snapshot")
}

/// The hash of the code that derives state: snapshots from any other
/// build are ignored.
fn code_hash() -> &'static [u8; 32] {
    static H: OnceLock<[u8; 32]> = OnceLock::new();
    H.get_or_init(|| {
        let mut s = String::new();
        for part in [
            include_str!("ledger.rs"),
            include_str!("schema.rs"),
            include_str!("json.rs"),
            include_str!("sha256.rs"),
            include_str!("store.rs"),
            include_str!("snapshot.rs"),
        ] {
            s.push_str(&part.len().to_string());
            s.push(':');
            s.push_str(part);
        }
        digest(s.as_bytes())
    })
}

// ------------------------------------------------------------------ codec

struct Reader<'a> {
    b: &'a [u8],
    at: usize,
    depth: usize,
}

impl<'a> Reader<'a> {
    fn byte(&mut self) -> Option<u8> {
        let x = *self.b.get(self.at)?;
        self.at += 1;
        Some(x)
    }
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let end = self.at.checked_add(n)?;
        let s = self.b.get(self.at..end)?;
        self.at = end;
        Some(s)
    }
    fn len(&mut self) -> Option<usize> {
        let n = u64::dec(self)?;
        // No collection can hold more items than there are bytes left.
        usize::try_from(n)
            .ok()
            .filter(|n| *n <= self.b.len() - self.at)
    }
}

trait Codec: Sized {
    fn enc(&self, w: &mut Vec<u8>);
    fn dec(r: &mut Reader) -> Option<Self>;
}

impl Codec for u64 {
    fn enc(&self, w: &mut Vec<u8>) {
        let mut x = *self;
        loop {
            let b = (x & 0x7f) as u8;
            x >>= 7;
            if x == 0 {
                w.push(b);
                return;
            }
            w.push(b | 0x80);
        }
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        let mut x = 0u64;
        for shift in (0..64).step_by(7) {
            let b = r.byte()?;
            x |= u64::from(b & 0x7f).checked_shl(shift)?;
            if b & 0x80 == 0 {
                return Some(x);
            }
        }
        None
    }
}

impl Codec for usize {
    fn enc(&self, w: &mut Vec<u8>) {
        (*self as u64).enc(w)
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        usize::try_from(u64::dec(r)?).ok()
    }
}

impl Codec for i64 {
    fn enc(&self, w: &mut Vec<u8>) {
        (((*self << 1) ^ (*self >> 63)) as u64).enc(w) // zigzag
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        let u = u64::dec(r)?;
        Some(((u >> 1) as i64) ^ -((u & 1) as i64))
    }
}

impl Codec for bool {
    fn enc(&self, w: &mut Vec<u8>) {
        w.push(*self as u8)
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        match r.byte()? {
            0 => Some(false),
            1 => Some(true),
            _ => None,
        }
    }
}

impl Codec for String {
    fn enc(&self, w: &mut Vec<u8>) {
        self.len().enc(w);
        w.extend_from_slice(self.as_bytes());
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        let n = r.len()?;
        std::str::from_utf8(r.take(n)?).ok().map(str::to_string)
    }
}

impl<T: Codec> Codec for Option<T> {
    fn enc(&self, w: &mut Vec<u8>) {
        match self {
            None => w.push(0),
            Some(x) => {
                w.push(1);
                x.enc(w)
            }
        }
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        match r.byte()? {
            0 => Some(None),
            1 => Some(Some(T::dec(r)?)),
            _ => None,
        }
    }
}

impl<T: Codec> Codec for Vec<T> {
    fn enc(&self, w: &mut Vec<u8>) {
        self.len().enc(w);
        for x in self {
            x.enc(w);
        }
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        let n = r.len()?;
        // A length is only a claim: never allocate more than a little ahead.
        let mut v = Vec::with_capacity(n.min(1024));
        for _ in 0..n {
            v.push(T::dec(r)?);
        }
        Some(v)
    }
}

impl<A: Codec, B: Codec> Codec for (A, B) {
    fn enc(&self, w: &mut Vec<u8>) {
        self.0.enc(w);
        self.1.enc(w);
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        Some((A::dec(r)?, B::dec(r)?))
    }
}

impl<A: Codec, B: Codec, C: Codec> Codec for (A, B, C) {
    fn enc(&self, w: &mut Vec<u8>) {
        self.0.enc(w);
        self.1.enc(w);
        self.2.enc(w);
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        Some((A::dec(r)?, B::dec(r)?, C::dec(r)?))
    }
}

impl<A: Codec, B: Codec, C: Codec, D: Codec> Codec for (A, B, C, D) {
    fn enc(&self, w: &mut Vec<u8>) {
        self.0.enc(w);
        self.1.enc(w);
        self.2.enc(w);
        self.3.enc(w);
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        Some((A::dec(r)?, B::dec(r)?, C::dec(r)?, D::dec(r)?))
    }
}

impl<T: Codec> Codec for BTreeMap<String, T> {
    fn enc(&self, w: &mut Vec<u8>) {
        self.len().enc(w);
        for (k, v) in self {
            k.enc(w);
            v.enc(w);
        }
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        let n = r.len()?;
        let mut v: Vec<(String, T)> = Vec::with_capacity(n.min(1024));
        for _ in 0..n {
            let k = String::dec(r)?;
            // Written in order: anything else is not a snapshot of ours.
            if v.last().is_some_and(|(p, _)| *p >= k) {
                return None;
            }
            v.push((k, T::dec(r)?));
        }
        Some(v.into_iter().collect())
    }
}

impl Codec for Value {
    fn enc(&self, w: &mut Vec<u8>) {
        match self {
            Value::Null => w.push(0),
            Value::Bool(false) => w.push(1),
            Value::Bool(true) => w.push(2),
            Value::Int(n) => {
                w.push(3);
                n.enc(w)
            }
            Value::Str(s) => {
                w.push(4);
                s.enc(w)
            }
            Value::Array(a) => {
                w.push(5);
                a.enc(w)
            }
            Value::Object(m) => {
                // Members in canonical order, as a replay of the log (which
                // is canonical) has them, however this ledger was built.
                w.push(6);
                let mut sorted: Vec<&(String, Value)> = m.iter().collect();
                sorted.sort_by(|a, b| crate::json::key_order(&a.0, &b.0));
                sorted.len().enc(w);
                for (k, v) in sorted {
                    k.enc(w);
                    v.enc(w);
                }
            }
        }
    }
    fn dec(r: &mut Reader) -> Option<Self> {
        let tag = r.byte()?;
        if tag >= 5 {
            // As deep as canonical JSON may nest, and no deeper.
            r.depth += 1;
            if r.depth > crate::json::MAX_DEPTH {
                return None;
            }
        }
        let v = Self::dec_tagged(r, tag);
        if tag >= 5 {
            r.depth -= 1;
        }
        v
    }
}

impl Value {
    fn dec_tagged(r: &mut Reader, tag: u8) -> Option<Self> {
        Some(match tag {
            0 => Value::Null,
            1 => Value::Bool(false),
            2 => Value::Bool(true),
            3 => Value::Int(i64::dec(r)?),
            4 => Value::Str(String::dec(r)?),
            5 => Value::Array(Vec::dec(r)?),
            6 => Value::Object(Vec::dec(r)?),
            _ => return None,
        })
    }
}

/// Encode and decode a struct field by field. Listing every field is
/// enforced by the compiler on the decoding side (a struct literal must
/// name them all), so a new field cannot be silently left out.
macro_rules! codec_struct {
    ($t:ident { $($f:ident),* $(,)? }) => {
        impl Codec for $t {
            fn enc(&self, w: &mut Vec<u8>) {
                $(self.$f.enc(w);)*
            }
            fn dec(r: &mut Reader) -> Option<Self> {
                Some($t { $($f: Codec::dec(r)?,)* })
            }
        }
    };
}

codec_struct!(WriterInfo {
    seq,
    party,
    kind,
    capabilities,
    compartments,
    last_seq,
    name,
    model,
    operator,
    surface,
    session,
    instance_of
});
codec_struct!(EpisodeInfo {
    seq,
    compartment,
    writer,
    record_time,
    hidden_chars,
    body,
    participants,
    turns,
    bindings
});
codec_struct!(ClaimInfo {
    body,
    compartment,
    asserted_truth,
    author,
    speaker_is,
    first_event,
    seq,
    assertions,
    record_time,
    corroborated_by,
    parties,
    verifier_parties,
    supports,
    refutes,
    abstains,
    superseded_by,
    supersedes
});
codec_struct!(Resolution {
    event,
    outcome,
    party,
    seq,
    by_refutation
});
codec_struct!(SupersessionInfo {
    old,
    new,
    kind,
    compartment,
    evidence,
    by,
    by_party,
    record_time,
    seq,
    dispute_parties,
    resolution
});
codec_struct!(VerdictInfo {
    claim,
    result,
    writer,
    seq
});
codec_struct!(HandoffInfo {
    seq,
    compartment,
    frame,
    writer,
    record_time,
    body
});
codec_struct!(AppealInfo {
    claim,
    compartment,
    evidence,
    by,
    by_party,
    record_time,
    seq,
    appeal_parties,
    resolution
});
codec_struct!(PositionInfo {
    compartment,
    org,
    title,
    reports_to,
    duties,
    by,
    event,
    seq
});
codec_struct!(AppointmentInfo {
    compartment,
    org,
    title,
    holder,
    by,
    record_time,
    seq,
    ended
});
codec_struct!(State {
    writers,
    episodes,
    claims,
    supersessions,
    verdicts,
    appeals,
    handoffs,
    positions,
    appointments,
    appeals_by_claim,
    successors,
    leases,
    schema_version
});
codec_struct!(LeaseInfo {
    writer,
    frame,
    expires,
    by,
    record_time,
    ended,
    scope
});
codec_struct!(Ledger { events, state });

// ------------------------------------------------------------ the file

/// Whether `meta` belongs to the user running this code and no one else
/// may write to it (or, for the key, read it).
#[cfg(unix)]
fn ours(meta: &std::fs::Metadata, forbidden: u32) -> bool {
    use std::os::unix::fs::MetadataExt;
    extern "C" {
        fn geteuid() -> u32;
    }
    // SAFETY: geteuid has no preconditions and cannot fail.
    let me = unsafe { geteuid() };
    meta.uid() == me && meta.mode() & forbidden == 0
}

/// A directory of ours, made (owner only) if it does not exist.
#[cfg(unix)]
fn our_dir(dir: &Path) -> Option<()> {
    use std::os::unix::fs::DirBuilderExt;
    if !dir.exists() {
        let _ = std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir);
    }
    let meta = std::fs::symlink_metadata(dir).ok()?;
    (meta.is_dir() && ours(&meta, 0o022)).then_some(())
}

/// The user's snapshot key: 32 random bytes, made on first use.
#[cfg(unix)]
fn key() -> Option<&'static [u8; 32]> {
    static KEY: OnceLock<Option<[u8; 32]>> = OnceLock::new();
    KEY.get_or_init(|| {
        let dir = match std::env::var_os("GNOMON_STATE_DIR") {
            Some(d) if !d.is_empty() => PathBuf::from(d),
            _ => match std::env::var_os("XDG_STATE_HOME") {
                Some(d) if !d.is_empty() => PathBuf::from(d).join("gnomon"),
                _ => PathBuf::from(std::env::var_os("HOME")?)
                    .join(".local")
                    .join("state")
                    .join("gnomon"),
            },
        };
        our_dir(&dir)?;
        let path = dir.join("snapshot.key");
        read_key(&path).or_else(|| {
            use std::io::{Read, Write};
            use std::os::unix::fs::OpenOptionsExt;
            let mut k = [0u8; 32];
            std::fs::File::open("/dev/urandom")
                .ok()?
                .read_exact(&mut k)
                .ok()?;
            let tmp = dir.join(format!("snapshot.key.{}.tmp", std::process::id()));
            let mut f = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&tmp)
                .ok()?;
            let written = f.write_all(&k).and_then(|_| f.sync_all());
            drop(f);
            // Never replace a key another process made first: its snapshots
            // would stop being used.
            let linked = written.and_then(|_| std::fs::hard_link(&tmp, &path));
            let _ = std::fs::remove_file(&tmp);
            match linked {
                Ok(()) => Some(k),
                Err(_) => read_key(&path),
            }
        })
    })
    .as_ref()
}

#[cfg(unix)]
fn read_key(path: &Path) -> Option<[u8; 32]> {
    let meta = std::fs::symlink_metadata(path).ok()?;
    if !meta.is_file() || !ours(&meta, 0o077) {
        return None;
    }
    std::fs::read(path).ok()?.try_into().ok()
}

#[cfg(not(unix))]
fn key() -> Option<&'static [u8; 32]> {
    None
}

/// The snapshot of `ledger`, verified from exactly `log` (its bytes),
/// sealed under `key`.
fn seal(ledger: &Ledger, log: &[u8], key: &[u8; 32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(log.len() + HEADER);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(code_hash());
    out.extend_from_slice(&(log.len() as u64).to_le_bytes());
    out.extend_from_slice(&digest(log));
    out.extend_from_slice(&[0u8; 32]); // the HMAC, below
    ledger.enc(&mut out);
    let mac = crate::sha256::hmac(key, &[&out[..HEADER - 32], &out[HEADER..]]);
    out[HEADER - 32..HEADER].copy_from_slice(&mac);
    out
}

/// How many bytes of a log `snap` says it covers (unchecked).
fn covers(snap: &[u8]) -> Option<usize> {
    (snap.len() >= HEADER && &snap[..8] == MAGIC)
        .then(|| usize::try_from(u64::from_le_bytes(snap[40..48].try_into().ok()?)).ok())
        .flatten()
}

/// The ledger sealed in `snap` under `key`, and how many bytes of `log` it
/// covers: only if it was made by this build, its seal holds, and `log`
/// begins with exactly the bytes it was made from, as many lines as it has
/// events, the last of them its last event.
fn unseal(snap: &[u8], log: &[u8], key: &[u8; 32]) -> Option<(Ledger, usize)> {
    let covered = covers(snap)?;
    if snap[8..40] != code_hash()[..] {
        return None;
    }
    let mac = crate::sha256::hmac(key, &[&snap[..HEADER - 32], &snap[HEADER..]]);
    // Compared in full whatever the first difference: no timing to learn from.
    if mac
        .iter()
        .zip(&snap[HEADER - 32..HEADER])
        .fold(0u8, |acc, (a, b)| acc | (a ^ b))
        != 0
    {
        return None;
    }
    if covered == 0
        || covered > log.len()
        || log[covered - 1] != b'\n'
        || digest(&log[..covered])[..] != snap[48..80]
    {
        return None;
    }
    let mut r = Reader {
        b: &snap[HEADER..],
        at: 0,
        depth: 0,
    };
    let ledger = Ledger::dec(&mut r)?;
    if r.at != snap.len() - HEADER {
        return None;
    }
    let lines = log[..covered].iter().filter(|b| **b == b'\n').count();
    let last_line = log[..covered - 1].rsplit(|b| *b == b'\n').next()?;
    let last = crate::json::parse(std::str::from_utf8(last_line).ok()?).ok()?;
    let head = |v: &Value| v.get("hash").and_then(|h| h.as_str()).map(str::to_string);
    if ledger.events.len() != lines
        || ledger.events.last().and_then(head) != head(&last)
        || head(&last).is_none()
    {
        return None;
    }
    Some((ledger, covered))
}

/// The snapshot of `ledger` (verified from exactly `log`) as it would be
/// saved, if this system can keep snapshots.
pub fn encode(ledger: &Ledger, log: &[u8]) -> Option<Vec<u8>> {
    Some(seal(ledger, log, key()?))
}

/// A snapshot holding `payload` as its encoded ledger, sealed with this
/// user's key, for `log`: for tests of what decoding refuses. (Anyone able
/// to call this already holds the key.)
#[doc(hidden)]
pub fn seal_raw(payload: &[u8], log: &[u8]) -> Option<Vec<u8>> {
    let key = key()?;
    let mut out = Vec::with_capacity(payload.len() + HEADER);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(code_hash());
    out.extend_from_slice(&(log.len() as u64).to_le_bytes());
    out.extend_from_slice(&digest(log));
    out.extend_from_slice(&[0u8; 32]);
    out.extend_from_slice(payload);
    let mac = crate::sha256::hmac(key, &[&out[..HEADER - 32], &out[HEADER..]]);
    out[HEADER - 32..HEADER].copy_from_slice(&mac);
    Some(out)
}

/// The encoded ledger inside a snapshot (for tests).
#[doc(hidden)]
pub fn payload(snap: &[u8]) -> &[u8] {
    &snap[HEADER.min(snap.len())..]
}

/// The ledger in snapshot `snap` and how many bytes of `log` it covers, if
/// it is usable for `log` (see the module documentation).
pub fn decode(snap: &[u8], log: &[u8]) -> Option<(Ledger, usize)> {
    unseal(snap, log, key()?)
}

/// What the snapshot beside `log_path` holds for `log`.
pub enum Loaded {
    /// A usable snapshot: the ledger and how many bytes of the log it covers.
    Usable(Ledger, usize),
    /// None, or one made from more of the log than there is now.
    Absent,
    /// One that cannot be used for this log (another log's, another
    /// build's, damaged, forged, or not ours).
    Unusable,
}

/// Read the snapshot beside `log_path` for `log`.
pub fn load(log_path: &Path, log: &[u8]) -> Loaded {
    let path = path_for(log_path);
    let Ok(meta) = std::fs::symlink_metadata(&path) else {
        return Loaded::Absent;
    };
    #[cfg(unix)]
    if !meta.is_file()
        || !ours(&meta, 0o022)
        || our_dir(path.parent().expect("has a parent")).is_none()
    {
        return Loaded::Unusable;
    }
    #[cfg(not(unix))]
    let _ = meta;
    let Ok(snap) = std::fs::read(&path) else {
        return Loaded::Absent;
    };
    match covers(&snap) {
        Some(n) if n > log.len() => Loaded::Absent,
        _ => match decode(&snap, log) {
            Some((l, n)) => Loaded::Usable(l, n),
            None => Loaded::Unusable,
        },
    }
}

/// Remove an unusable snapshot (so a fresh one can take its place).
pub fn discard(log_path: &Path) {
    let _ = std::fs::remove_file(path_for(log_path));
}

/// Save a snapshot of `ledger` (verified from exactly `log`) beside
/// `log_path`, unless one there already covers more. Failure to save is
/// not an error: it is only a cache.
pub fn save(log_path: &Path, ledger: &Ledger, log: &[u8]) {
    let _ = try_save(log_path, ledger, log);
}

fn try_save(log_path: &Path, ledger: &Ledger, log: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let fail = || std::io::Error::other("snapshots are not kept here");
    let key = key().ok_or_else(fail)?;
    let target = path_for(log_path);
    let dir = target.parent().expect("snapshot path has a parent");
    #[cfg(unix)]
    our_dir(dir).ok_or_else(fail)?;
    static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = dir.join(format!("snapshot.{}.{n}.tmp", std::process::id()));
    let mut opts = std::fs::OpenOptions::new();
    // A fresh file: never through an existing file or link.
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(&tmp)?;
    let res = f
        .write_all(&seal(ledger, log, key))
        .and_then(|_| f.sync_data());
    drop(f);
    // Keep a snapshot that covers more (from a process that saw more).
    let newer = std::fs::read(&target)
        .ok()
        .and_then(|s| covers(&s))
        .is_some_and(|c| c > log.len());
    let res = res.and_then(|_| {
        if newer {
            Err(fail())
        } else {
            std::fs::rename(&tmp, &target)
        }
    });
    if res.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    res
}
