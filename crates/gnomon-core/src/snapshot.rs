//! Snapshots: the verified state of a log, saved beside it, so that a new
//! process does not replay the whole log through the gate before it can
//! answer.
//!
//! A snapshot is a cache, never a source of truth:
//!
//! * It names the exact bytes it was made from: their length and SHA-256.
//!   It is used only if the log still begins with exactly those bytes; any
//!   other change to the log (an edit, a truncation) makes it useless, and
//!   the log is verified from scratch as before. Events added after it are
//!   checked through the gate as usual.
//! * It names the code that made it: a hash of the source of the gate, the
//!   schema, canonical JSON and this file. A build with any change to how
//!   state is derived ignores snapshots made by another.
//! * Its own contents carry a SHA-256, so a damaged file is ignored.
//! * The full audit (`store::audit`, `gn_verify`) never reads it.
//!
//! The file is written to a temporary name and renamed into place, readable
//! by its owner only. Anyone who can rewrite it could equally rewrite the
//! log; the audit is how either is caught.
//!
//! The format is a plain binary encoding of the ledger: little-endian
//! LEB128 lengths, then the events and the derived state, field by field.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use crate::json::Value;
use crate::ledger::{
    AppealInfo, ClaimInfo, EpisodeInfo, HandoffInfo, Ledger, Resolution, State, SupersessionInfo, VerdictInfo,
    WriterInfo,
};
use crate::sha256::digest;

const MAGIC: &[u8; 8] = b"GNSNAP1\n";

/// Logs with fewer events than this are cheap to verify: no snapshot.
pub const MIN_EVENTS: usize = 1000;

/// `MIN_EVENTS`, unless `GNOMON_SNAPSHOT_MIN_EVENTS` says otherwise (for
/// tests, which snapshot every log to compare snapshots with replays).
pub fn min_events() -> usize {
    static N: OnceLock<usize> = OnceLock::new();
    *N.get_or_init(|| {
        std::env::var("GNOMON_SNAPSHOT_MIN_EVENTS").ok().and_then(|v| v.parse().ok()).unwrap_or(MIN_EVENTS).max(1)
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
        usize::try_from(n).ok().filter(|n| *n <= self.b.len() - self.at)
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
        let mut v = Vec::with_capacity(n);
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
        let mut v: Vec<(String, T)> = Vec::with_capacity(n);
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
        Some(match r.byte()? {
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
    seq, party, kind, capabilities, compartments, last_seq, name, model, operator, surface, session, instance_of
});
codec_struct!(EpisodeInfo { seq, compartment, writer, record_time, hidden_chars, body, participants, turns, bindings });
codec_struct!(ClaimInfo {
    body, compartment, asserted_truth, author, speaker_is, first_event, seq, assertions, record_time,
    corroborated_by, parties, verifier_parties, supports, refutes, abstains, superseded_by, supersedes
});
codec_struct!(Resolution { event, outcome, party, seq, by_refutation });
codec_struct!(SupersessionInfo {
    old, new, kind, compartment, evidence, by, by_party, record_time, seq, dispute_parties, resolution
});
codec_struct!(VerdictInfo { claim, result, writer, seq });
codec_struct!(HandoffInfo { seq, compartment, frame, writer, record_time, body });
codec_struct!(AppealInfo { claim, compartment, evidence, by, by_party, record_time, seq, appeal_parties, resolution });
codec_struct!(State {
    writers, episodes, claims, supersessions, verdicts, appeals, handoffs, appeals_by_claim, schema_version
});
codec_struct!(Ledger { events, state });

// ------------------------------------------------------------ the file

/// The snapshot of `ledger`, verified from exactly `log` (its bytes).
pub fn encode(ledger: &Ledger, log: &[u8]) -> Vec<u8> {
    let mut payload = Vec::with_capacity(log.len());
    ledger.enc(&mut payload);
    let mut out = Vec::with_capacity(payload.len() + 120);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(code_hash());
    out.extend_from_slice(&(log.len() as u64).to_le_bytes());
    out.extend_from_slice(&digest(log));
    out.extend_from_slice(&digest(&payload));
    out.extend_from_slice(&payload);
    out
}

/// How many bytes of the log a snapshot covers, if it was made by this
/// build and is undamaged (without checking the log).
fn header(snap: &[u8]) -> Option<(usize, &[u8], &[u8])> {
    if snap.len() < 112 || &snap[..8] != MAGIC || snap[8..40] != code_hash()[..] {
        return None;
    }
    let covered = usize::try_from(u64::from_le_bytes(snap[40..48].try_into().ok()?)).ok()?;
    let (log_hash, payload) = (&snap[48..80], &snap[112..]);
    if digest(payload)[..] != snap[80..112] {
        return None;
    }
    Some((covered, log_hash, payload))
}

/// The ledger saved in `snap`, and how many bytes of `log` it covers: only
/// if `log` begins with exactly the bytes it was made from.
pub fn decode(snap: &[u8], log: &[u8]) -> Option<(Ledger, usize)> {
    let (covered, log_hash, payload) = header(snap)?;
    if covered == 0 || covered > log.len() || log[covered - 1] != b'\n' || digest(&log[..covered])[..] != *log_hash {
        return None;
    }
    let mut r = Reader { b: payload, at: 0 };
    let ledger = Ledger::dec(&mut r)?;
    (r.at == payload.len()).then_some((ledger, covered))
}

/// Read the snapshot beside `log_path`, if there is a usable one for `log`.
pub fn load(log_path: &Path, log: &[u8]) -> Option<(Ledger, usize)> {
    let snap = std::fs::read(path_for(log_path)).ok()?;
    decode(&snap, log)
}

/// Save a snapshot of `ledger` (verified from exactly `log`) beside
/// `log_path`. Failure to save is not an error: it is only a cache.
pub fn save(log_path: &Path, ledger: &Ledger, log: &[u8]) {
    let _ = try_save(log_path, ledger, log);
}

fn try_save(log_path: &Path, ledger: &Ledger, log: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let target = path_for(log_path);
    let dir = target.parent().expect("snapshot path has a parent");
    std::fs::create_dir_all(dir)?;
    static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = dir.join(format!("snapshot.{}.{n}.tmp", std::process::id()));
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(&tmp)?;
    let res = f.write_all(&encode(ledger, log)).and_then(|_| f.sync_data());
    drop(f);
    match res.and_then(|_| std::fs::rename(&tmp, &target)) {
        Ok(()) => Ok(()),
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e)
        }
    }
}
