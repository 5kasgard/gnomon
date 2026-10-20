//! Snapshots: a new process starts from the verified state saved beside the
//! log instead of replaying it, and gets exactly what a replay would give.
//! A snapshot that no longer matches the log, the code or itself is never
//! used.

mod common;
use common::*;

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use gnomon_core::json::{canonical, Value};
use gnomon_core::ledger::{Draft, Ledger};
use gnomon_core::{snapshot, store};

fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("gnomon-snap-{}-{}", std::process::id(), name));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir.join("log.jsonl")
}

fn eid(env: &Value) -> String {
    env.get("event_id").unwrap().as_str().unwrap().to_string()
}

fn obj(fields: Vec<(&str, Value)>) -> Value {
    Value::obj(fields)
}

fn strs(xs: &[&str]) -> Value {
    Value::Array(xs.iter().map(|x| Value::str(*x)).collect())
}

/// Every kind of event and every part of the state: writers and a spawned
/// instance, episodes with turns and bindings, claims quoting them,
/// corroboration, verdicts, a refutation, a contest and its ruling, an
/// appeal and its decision, a handoff, and enough claims to pass the
/// snapshot threshold.
fn world() -> Ledger {
    let mut l = lab();
    let reg = |w: &str, p: &str, caps: &[&str]| register_body(w, p, "model", caps, &["lab"]);
    l.append(draft("john", 6, "register", reg("mistral-m1", "mistral", &["write", "verify"]))).unwrap();
    l.append(draft("john", 7, "register", reg("judge", "judge-co", &["verify"]))).unwrap();
    l.append(draft("john", 8, "register", reg("claude", "anthropic", &["write", "verify", "spawn"]).with("name", Value::str("Claude"))))
        .unwrap();
    let inst = register_plain("claude-1", "anthropic", "model", &["write"], &["lab"])
        .with("operator", Value::str("john"))
        .with("model", Value::str("claude"))
        .with("name", Value::str("Claude"))
        .with("session", Value::str("s1"));
    l.append(draft("claude", 9, "register", inst)).unwrap();

    let text = "John: Café first. Use Rust.\nClaude: Agreed.";
    let cut = text.find("\nClaude").map(|b| text[..b].chars().count()).unwrap() as i64;
    let turn = |sp: &str, a: i64, b: i64| obj(vec![("speaker", Value::str(sp)), ("start", Value::Int(a)), ("end", Value::Int(b))]);
    let ep = l
        .append(draft(
            "claude-1",
            10,
            "episode",
            obj(vec![
                ("compartment", Value::str("lab")),
                ("content", Value::str(text)),
                ("medium", Value::str("chat")),
                ("participants", strs(&["John", "Claude"])),
                ("turns", Value::Array(vec![turn("John", 0, cut), turn("Claude", cut + 1, text.chars().count() as i64)])),
                ("bindings", obj(vec![("John", Value::str("john")), ("Claude", Value::str("claude-1"))])),
            ]),
        ))
        .unwrap();
    let start = text.find("Use Rust.").map(|b| text[..b].chars().count()).unwrap() as i64;
    let quoted = obj(vec![
        ("statement", Value::str("Use Rust.")),
        ("statement_kind", Value::str("describe")),
        ("origin", Value::str("direct")),
        ("stance", Value::str("decided")),
        ("speaker", Value::str("John")),
        ("truth", Value::str("true")),
        ("confidence", Value::Int(7000)),
        ("frame", Value::str("core")),
        ("compartment", Value::str("lab")),
        ("source_episode", Value::str(eid(&ep))),
        ("quote", Value::str("Use Rust.")),
        ("quote_start", Value::Int(start)),
    ]);
    add_claim(&mut l, "claude-1", 11, quoted);

    let verdict = |c: &str, r: &str| obj(vec![("claim", Value::str(c)), ("result", Value::str(r)), ("method", Value::str("test"))]);
    let sup = |old: &str, new: &str, kind: &str, ev: Vec<String>| {
        obj(vec![
            ("old", Value::str(old)),
            ("new", Value::str(new)),
            ("kind", Value::str(kind)),
            ("evidence", Value::Array(ev.into_iter().map(Value::str).collect())),
            ("reason", Value::str("because")),
        ])
    };
    let decide = |d: &str, o: &str| obj(vec![("dispute", Value::str(d)), ("outcome", Value::str(o)), ("reason", Value::str("decision"))]);

    // Refuted, then appealed on new evidence and reinstated by a neutral party.
    let t = add_claim(&mut l, "claude-c1", 12, claim_body("T", "asserted", "true"));
    let k = add_claim(&mut l, "gpt-g1", 12, claim_body("K", "asserted", "true"));
    let v = l.append(draft("kimi-k1", 13, "verdict", verdict(&t, "refutes"))).unwrap();
    l.append(draft("gpt-g1", 13, "supersede", sup(&t, &k, "refuted", vec![eid(&v)]))).unwrap();
    let s = l.append(draft("mistral-m1", 14, "verdict", verdict(&t, "supports"))).unwrap();
    let appeal = obj(vec![("claim", Value::str(&t)), ("evidence", strs(&[&eid(&s)])), ("reason", Value::str("new evidence"))]);
    let a = l.append(draft("claude-c1", 14, "appeal", appeal)).unwrap();
    l.append(draft("judge", 15, "resolve", decide(&eid(&a), "reinstated"))).unwrap();

    // A contest, ruled.
    let x = add_claim(&mut l, "claude-c1", 16, claim_body("X", "asserted", "true"));
    let y = add_claim(&mut l, "gpt-g1", 16, claim_body("Y", "asserted", "true"));
    let c = l.append(draft("gpt-g1", 17, "supersede", sup(&x, &y, "contested", vec![]))).unwrap();
    l.append(draft("judge", 18, "resolve", decide(&eid(&c), "new"))).unwrap();

    // Corroboration, a forum question and answer, a handoff.
    l.append(draft("kimi-k1", 19, "claim", claim_body("K", "asserted", "true"))).unwrap();
    let q = add_claim(&mut l, "claude-c1", 20, claim_body("Which database?", "asked", "unknown"));
    let ans = claim_body("SQLite.", "suggested", "unknown").with("about", Value::Array(vec![obj(vec![
        ("relation", Value::str("answers")),
        ("claim", Value::str(&q)),
    ])]));
    add_claim(&mut l, "gpt-g1", 21, ans);
    let h = obj(vec![
        ("compartment", Value::str("lab")),
        ("frame", Value::str("core")),
        ("summary", Value::str("Rust chosen; the schema is next.")),
        ("next", strs(&["write the schema"])),
    ]);
    l.append(draft("claude-c1", 22, "handoff", h)).unwrap();

    // Enough to be worth a snapshot.
    for i in 0..1100 {
        let w = ["claude-c1", "gpt-g1", "kimi-k1"][i % 3];
        add_claim(&mut l, w, 30, claim_body(&format!("Fact {i} with an unusual ünïcödé key \u{1F600}"), "asserted", "true"));
    }
    l
}

fn write_log(path: &Path, l: &Ledger) {
    let mut f = fs::File::create(path).unwrap();
    for env in &l.events {
        writeln!(f, "{}", canonical(env)).unwrap();
    }
}

fn replay(path: &Path) -> Ledger {
    store::open(path, None).unwrap()
}

#[test]
fn a_snapshot_decodes_to_exactly_what_a_replay_gives() {
    let path = tmp("roundtrip");
    let l = world();
    write_log(&path, &l);
    let bytes = fs::read(&path).unwrap();
    let full = replay(&path);
    // From a ledger built by appends (members in insertion order) and from
    // one replayed from the file: the same snapshot, the same ledger.
    let a = snapshot::encode(&l, &bytes).unwrap();
    let b = snapshot::encode(&full, &bytes).unwrap();
    assert!(a == b, "a ledger built by appends and its replay must snapshot identically");
    let (decoded, covered) = snapshot::decode(&a, &bytes).expect("usable");
    assert_eq!(covered, bytes.len());
    assert!(decoded == full, "decoded snapshot differs from replay");
    assert_eq!(canonical(&decoded.state.to_value(None)), canonical(&full.state.to_value(None)));
}

#[test]
fn opening_writes_a_snapshot_and_the_next_process_would_use_it() {
    let path = tmp("written");
    write_log(&path, &world());
    assert!(!snapshot::path_for(&path).exists());
    store::with_ledger(&path, |_| ()).unwrap();
    let snap = fs::read(snapshot::path_for(&path)).expect("a snapshot is saved after a full verification");
    let bytes = fs::read(&path).unwrap();
    let (l, _) = snapshot::decode(&snap, &bytes).expect("usable");
    assert!(l == replay(&path));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(snapshot::path_for(&path)).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0, "only its owner can read it");
    }
}

#[test]
fn events_after_the_snapshot_go_through_the_gate() {
    let path = tmp("grown");
    let l = world();
    write_log(&path, &l);
    let bytes = fs::read(&path).unwrap();
    let snap = snapshot::encode(&replay(&path), &bytes).unwrap();
    // The log grows by a valid event and by an invalid one.
    store::append(&path, draft("claude-c1", 40, "claim", claim_body("After.", "asserted", "true"))).unwrap();
    let grown = fs::read(&path).unwrap();
    let (from_snap, covered) = snapshot::decode(&snap, &grown).expect("still a prefix");
    assert_eq!(covered, bytes.len());
    assert_eq!(from_snap.events.len() + 1, replay(&path).events.len());
    let mut bad = grown.clone();
    let forged = canonical(&grown_last(&grown).with("writer_seq", Value::Int(999)));
    bad.extend_from_slice(forged.as_bytes());
    bad.push(b'\n');
    let p2 = tmp("grown-bad");
    fs::write(&p2, &bad).unwrap();
    fs::create_dir_all(snapshot::path_for(&p2).parent().unwrap()).unwrap();
    fs::write(snapshot::path_for(&p2), &snap).unwrap();
    assert!(store::with_ledger(&p2, |_| ()).is_err(), "a bad event after the snapshot is still refused");
}

fn grown_last(bytes: &[u8]) -> Value {
    let text = std::str::from_utf8(bytes).unwrap();
    gnomon_core::json::parse(text.trim_end().rsplit('\n').next().unwrap()).unwrap()
}

#[test]
fn a_snapshot_is_never_used_for_a_log_that_changed() {
    let path = tmp("tampered");
    let l = world();
    write_log(&path, &l);
    let bytes = fs::read(&path).unwrap();
    let snap = snapshot::encode(&replay(&path), &bytes).unwrap();
    // Any byte changed, the log truncated, a line removed: not used.
    let mut edited = bytes.clone();
    let at = bytes.len() / 2;
    edited[at] = if edited[at] == b'a' { b'b' } else { b'a' };
    assert!(snapshot::decode(&snap, &edited).is_none());
    assert!(snapshot::decode(&snap, &bytes[..bytes.len() - 1]).is_none());
    let cut = bytes[..bytes.len() - 1].iter().rposition(|b| *b == b'\n').unwrap() + 1;
    assert!(snapshot::decode(&snap, &bytes[..cut]).is_none());
    // And through the store: the edit is found by the full verification
    // the snapshot no longer spares it.
    let p2 = tmp("tampered-store");
    fs::write(&p2, &edited).unwrap();
    fs::create_dir_all(snapshot::path_for(&p2).parent().unwrap()).unwrap();
    fs::write(snapshot::path_for(&p2), &snap).unwrap();
    assert!(store::with_ledger(&p2, |_| ()).is_err());
}

#[test]
fn a_damaged_or_foreign_snapshot_is_ignored() {
    let path = tmp("damaged");
    write_log(&path, &world());
    let bytes = fs::read(&path).unwrap();
    let snap = snapshot::encode(&replay(&path), &bytes).unwrap();
    // A flipped byte anywhere: header, code hash, log hash, payload.
    for at in [0, 9, 45, 60, 100, snap.len() / 2, snap.len() - 1] {
        let mut s = snap.clone();
        s[at] ^= 0x40;
        assert!(snapshot::decode(&s, &bytes).is_none(), "flipped byte {at}");
    }
    assert!(snapshot::decode(&snap[..snap.len() - 1], &bytes).is_none());
    assert!(snapshot::decode(&[], &bytes).is_none());
    // A damaged snapshot on disk: the log is verified in full instead.
    let mut s = snap.clone();
    let n = s.len();
    s[n - 3] ^= 1;
    fs::create_dir_all(snapshot::path_for(&path).parent().unwrap()).unwrap();
    fs::write(snapshot::path_for(&path), &s).unwrap();
    let n = store::with_ledger(&path, |l| l.events.len()).unwrap();
    assert_eq!(n, replay(&path).events.len());
}

#[test]
fn a_forged_snapshot_cannot_survive_a_change_to_its_log_hash() {
    // A snapshot names its log by SHA-256: a state built for one log is
    // never applied to another, even one with the same length.
    let p1 = tmp("forge-a");
    let p2 = tmp("forge-b");
    let mut w = world();
    write_log(&p1, &w);
    w.append(Draft { writer: "gpt-g1".into(), record_time: t(50), kind: "claim".into(), body: claim_body("Z", "asserted", "true") })
        .unwrap();
    write_log(&p2, &w);
    let s1 = snapshot::encode(&replay(&p1), &fs::read(&p1).unwrap()).unwrap();
    let b2 = fs::read(&p2).unwrap();
    // p2 begins with p1's bytes, so p1's snapshot is a valid start for p2...
    assert!(snapshot::decode(&s1, &b2).is_some());
    // ...but not for a log whose first part differs.
    let mut other = b2.clone();
    other[10] ^= 1;
    assert!(snapshot::decode(&s1, &other).is_none());
}

#[test]
fn a_snapshot_sealed_with_another_key_or_reencoded_is_ignored() {
    // The payload is sealed with the user's key: a plain hash recomputed
    // over a changed payload does not make it usable.
    let path = tmp("forged");
    write_log(&path, &world());
    let bytes = fs::read(&path).unwrap();
    let snap = snapshot::encode(&replay(&path), &bytes).unwrap();
    let mut forged = snap.clone();
    let n = forged.len();
    forged[n - 10] ^= 1;
    for at in 80..112 {
        forged[at] ^= 0x55; // any change to the seal itself
    }
    assert!(snapshot::decode(&forged, &bytes).is_none());
}

#[test]
fn a_sealed_snapshot_must_still_agree_with_the_log() {
    // Even under a valid seal: as many events as lines, the same last hash.
    let path = tmp("agree");
    write_log(&path, &world());
    let bytes = fs::read(&path).unwrap();
    let mut l = replay(&path);
    let full = snapshot::encode(&l, &bytes).unwrap();
    assert!(snapshot::decode(&full, &bytes).is_some());
    l.events.pop();
    let fewer = snapshot::encode(&l, &bytes).unwrap();
    assert!(snapshot::decode(&fewer, &bytes).is_none(), "one event short");
    let mut l = replay(&path);
    let last = l.events.pop().unwrap();
    l.events.push(last.with("hash", Value::str("0".repeat(64))));
    let wrong = snapshot::encode(&l, &bytes).unwrap();
    assert!(snapshot::decode(&wrong, &bytes).is_none(), "another last hash");
}

#[test]
fn decoding_is_bounded() {
    let path = tmp("bounded");
    write_log(&path, &world());
    let bytes = fs::read(&path).unwrap();
    // Two million nested arrays: refused, not a stack overflow.
    let mut deep = vec![1u8]; // one event...
    deep.extend(std::iter::repeat_n([5u8, 1u8], 2_000_000).flatten()); // ...an array of one array of...
    deep.push(0);
    assert!(snapshot::decode(&snapshot::seal_raw(&deep, &bytes).unwrap(), &bytes).is_none());
    // A length claiming four hundred million writers in a few bytes:
    // refused without trying to allocate for them.
    let mut huge = vec![0u8]; // no events
    let mut n: u64 = 400_000_000;
    loop {
        let b = (n & 0x7f) as u8;
        n >>= 7;
        if n == 0 {
            huge.push(b);
            break;
        }
        huge.push(b | 0x80);
    }
    huge.extend(std::iter::repeat_n(1u8, 64 << 20)); // 64 MB of filler
    assert!(snapshot::decode(&snapshot::seal_raw(&huge, &bytes).unwrap(), &bytes).is_none());
    // The real payload still decodes when resealed: the test helper is honest.
    let snap = snapshot::encode(&replay(&path), &bytes).unwrap();
    let again = snapshot::seal_raw(snapshot::payload(&snap), &bytes).unwrap();
    assert!(snapshot::decode(&again, &bytes).is_some());
}

#[cfg(unix)]
#[test]
fn snapshots_are_written_only_into_our_own_directory_and_never_through_a_link() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let path = tmp("links");
    write_log(&path, &world());
    let dir = snapshot::path_for(&path).parent().unwrap().to_path_buf();
    fs::create_dir_all(&dir).unwrap();
    let victim = path.parent().unwrap().join("victim.txt");
    fs::write(&victim, "untouched").unwrap();
    for n in 0..64 {
        let _ = symlink(&victim, dir.join(format!("snapshot.{}.{n}.tmp", std::process::id())));
    }
    store::with_ledger(&path, |_| ()).unwrap();
    assert_eq!(fs::read_to_string(&victim).unwrap(), "untouched");
    // A directory others may write to is neither read nor written.
    let p2 = tmp("shared-dir");
    write_log(&p2, &world());
    let d2 = snapshot::path_for(&p2).parent().unwrap().to_path_buf();
    fs::create_dir_all(&d2).unwrap();
    fs::set_permissions(&d2, fs::Permissions::from_mode(0o777)).unwrap();
    store::with_ledger(&p2, |_| ()).unwrap();
    assert!(!snapshot::path_for(&p2).exists());
}
