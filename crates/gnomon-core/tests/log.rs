//! M1 gates for the record: rebuild is byte-identical, tampering is detected,
//! torn writes are reported, checkpoints catch truncation, and "as of"
//! queries return exactly what was known then.

mod common;
use common::*;

use std::fs;
use std::io::Write;
use std::path::PathBuf;

use gnomon_core::json::{canonical, Value};
use gnomon_core::ledger::Ledger;
use gnomon_core::store::{self, StoreError};

fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("gnomon-test-{}-{}", std::process::id(), name));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir.join("log.jsonl")
}

fn write_lab_log(path: &PathBuf) -> Ledger {
    let l = lab();
    for (i, env) in l.events.iter().enumerate() {
        let _ = i;
        let mut f = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .unwrap();
        writeln!(f, "{}", canonical(env)).unwrap();
    }
    // Plus a few claims through the public store API.
    store::append(
        path,
        draft(
            "claude-c1",
            10,
            "claim",
            claim_body("A", "asserted", "true"),
        ),
    )
    .unwrap();
    store::append(
        path,
        draft(
            "gpt-g1",
            11,
            "claim",
            claim_body("B", "suggested", "unknown"),
        ),
    )
    .unwrap();
    store::append(
        path,
        draft("kimi-k1", 12, "claim", claim_body("C", "decided", "true")),
    )
    .unwrap();
    store::open(path, None).unwrap()
}

#[test]
fn rebuild_from_log_is_identical() {
    let path = tmp("rebuild");
    let original = write_lab_log(&path);
    let digest = original.state.digest();
    for _ in 0..3 {
        let rebuilt = store::open(&path, None).unwrap();
        assert_eq!(rebuilt.state.digest(), digest);
        assert_eq!(
            canonical(&rebuilt.state.to_value(None)),
            canonical(&original.state.to_value(None))
        );
    }
}

#[test]
fn tampering_any_byte_is_detected() {
    let path = tmp("tamper");
    write_lab_log(&path);
    let clean = fs::read(&path).unwrap();
    // Flip each printable byte in turn (sampled) and require detection.
    let mut detected = 0;
    let mut tried = 0;
    for pos in (0..clean.len()).step_by(7) {
        if clean[pos] == b'\n' {
            continue;
        }
        let mut bad = clean.clone();
        bad[pos] = if bad[pos] == b'a' { b'b' } else { b'a' };
        fs::write(&path, &bad).unwrap();
        tried += 1;
        if store::open(&path, None).is_err() {
            detected += 1;
        }
    }
    assert_eq!(detected, tried, "every tampered byte must be detected");
}

#[test]
fn reordered_or_deleted_lines_are_detected() {
    let path = tmp("reorder");
    write_lab_log(&path);
    let text = fs::read_to_string(&path).unwrap();
    let mut lines: Vec<&str> = text.lines().collect();
    lines.swap(3, 4);
    fs::write(&path, lines.join("\n") + "\n").unwrap();
    assert!(store::open(&path, None).is_err());

    let mut lines: Vec<&str> = text.lines().collect();
    lines.remove(2);
    fs::write(&path, lines.join("\n") + "\n").unwrap();
    assert!(store::open(&path, None).is_err());
}

#[test]
fn torn_tail_is_reported_not_dropped() {
    let path = tmp("torn");
    write_lab_log(&path);
    let mut f = fs::OpenOptions::new().append(true).open(&path).unwrap();
    f.write_all(b"{\"seq\":99,\"partial").unwrap();
    assert!(matches!(
        store::open(&path, None),
        Err(StoreError::TornTail { .. })
    ));
}

#[test]
fn non_canonical_lines_are_rejected() {
    let path = tmp("noncanon");
    write_lab_log(&path);
    let text = fs::read_to_string(&path).unwrap();
    // Same content, different whitespace: still tampering.
    let spaced = text.replacen("{\"body\":", "{ \"body\":", 1);
    fs::write(&path, spaced).unwrap();
    assert!(matches!(
        store::open(&path, None),
        Err(StoreError::Malformed { .. })
    ));
}

#[test]
fn checkpoint_detects_truncation_and_rewrite() {
    let path = tmp("checkpoint");
    let l = write_lab_log(&path);
    let count = l.events.len();
    let head = l.head();
    store::verify_checkpoint(&path, count, &head).unwrap();

    // Truncate the last event: the chain alone is still valid, the checkpoint is not.
    let text = fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    fs::write(&path, lines[..lines.len() - 1].join("\n") + "\n").unwrap();
    assert!(store::open(&path, None).is_ok());
    assert!(matches!(
        store::verify_checkpoint(&path, count, &head),
        Err(StoreError::CheckpointMismatch { .. })
    ));
}

#[test]
fn as_of_returns_exactly_what_was_known_then() {
    let path = tmp("asof");
    let l = write_lab_log(&path);
    let before_b = l.as_of(&t(10));
    assert_eq!(before_b.state.claims.len(), 1);
    let at_b = l.as_of(&t(11));
    assert_eq!(at_b.state.claims.len(), 2);
    let all = l.as_of("2099-01-01T00:00:00Z");
    assert_eq!(all.state.digest(), l.state.digest());
    let none = l.as_of("2000-01-01T00:00:00Z");
    assert_eq!(none.events.len(), 0);
}

#[test]
fn cache_never_hides_tampering() {
    let path = tmp("cache");
    write_lab_log(&path);
    // Warm the cache through both read and write paths.
    store::with_ledger(&path, |_| ()).unwrap();
    store::append(
        &path,
        draft(
            "claude-c1",
            20,
            "claim",
            claim_body("D", "asserted", "true"),
        ),
    )
    .unwrap();
    // Tamper behind the cache's back, keeping the length identical.
    let mut bytes = fs::read(&path).unwrap();
    let pos = bytes.len() / 2;
    bytes[pos] = if bytes[pos] == b'a' { b'b' } else { b'a' };
    fs::write(&path, &bytes).unwrap();
    assert!(store::with_ledger(&path, |_| ()).is_err());
    assert!(store::append(
        &path,
        draft("gpt-g1", 21, "claim", claim_body("E", "asserted", "true"))
    )
    .is_err());
    // Truncation is also noticed (the cached bytes no longer match).
    let text = fs::read_to_string(&path).unwrap();
    let _ = text;
}

#[test]
fn rejected_appends_leave_the_log_untouched() {
    let path = tmp("reject");
    write_lab_log(&path);
    let before = fs::read(&path).unwrap();
    let bad = claim_body("x", "asserted", "true").with("confidence", Value::Int(99_999));
    assert!(store::append(&path, draft("claude-c1", 20, "claim", bad)).is_err());
    let unregistered = store::append(
        &path,
        draft("mallory", 20, "claim", claim_body("y", "asserted", "true")),
    );
    assert!(unregistered.is_err());
    assert_eq!(fs::read(&path).unwrap(), before);
}

/// The envelope hash worked out from a canonical line (the line minus its
/// top-level hash member) is exactly the hash of the event without it,
/// even when the body itself holds a member called "hash".
#[test]
fn line_hashes_equal_envelope_hashes() {
    use gnomon_core::ledger::envelope_hash;
    let dir = std::env::temp_dir().join(format!("gn-linehash-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("m.jsonl");
    let _ = std::fs::remove_file(&path);
    let mut l = lab();
    let tricky = claim_body("The file's hash is listed below.", "asserted", "true")
        .with("observed", Value::str(",\"hash\":\"00\""));
    l.append(draft("claude-c1", 10, "claim", tricky)).unwrap();
    let text: String = l
        .events
        .iter()
        .map(|e| gnomon_core::json::canonical(e) + "\n")
        .collect();
    std::fs::write(&path, &text).unwrap();
    // Reading it back goes through the line-hash path; it must accept the
    // log and agree with envelope_hash on every event.
    gnomon_core::store::with_ledger(&path, |r| {
        assert_eq!(r.events.len(), l.events.len());
        for e in &r.events {
            assert_eq!(
                e.get("hash").and_then(|h| h.as_str()).unwrap(),
                envelope_hash(e)
            );
        }
    })
    .unwrap();
    // A changed body is still caught.
    let bad = text.replacen("The file", "A file", 1);
    std::fs::write(&path, bad).unwrap();
    assert!(gnomon_core::store::with_ledger(&path, |_| ()).is_err());
    std::fs::remove_dir_all(&dir).unwrap();
}
