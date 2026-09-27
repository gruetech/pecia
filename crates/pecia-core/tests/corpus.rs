//! The acceptance test: real data, and a chain built the way the format says.
//!
//! `.pecia/work.jsonl` is the committed projection — every record-revision of
//! this repository's own ledger, each line already in the canonical form. So
//! the strongest available statement about this crate is a fixed point: parse
//! each line and re-serialize it, and the bytes must come back identical. It
//! is in-repo, so this runs on any clone; the live log lives in the git common
//! dir and is NOT committed, which is why the cross-implementation differential
//! over it is a dev script (`dev/rust-differential.py`) rather than a test here.

use pecia_core::{canonical, entry_hash, parse, Value};
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("repo root")
}

#[test]
fn every_committed_projection_line_is_a_fixed_point_of_the_canonical_form() {
    let path = repo_root().join(".pecia/work.jsonl");
    let text = std::fs::read_to_string(&path).expect("the projection is committed");
    // LF-delimited physical lines (v2.12, pc-d96d): `str::lines()` would also
    // split on U+2028, which v3.0 stores raw inside strings.
    let lines: Vec<&str> = text.split('\n').filter(|l| !l.is_empty()).collect();

    // The denominator, asserted. This repository has shipped gates that were
    // green over an empty one; a corpus test that silently read nothing would
    // be the same defect in the port's first test.
    assert!(lines.len() > 1000, "corpus too small to mean anything: {}", lines.len());

    for (n, line) in lines.iter().enumerate() {
        let value = parse(line)
            .unwrap_or_else(|e| panic!("{}:{} refused: {e}", path.display(), n + 1));
        assert_eq!(
            &canonical(&value),
            line,
            "{}:{} is not a fixed point of the canonical form",
            path.display(),
            n + 1
        );
    }
}

#[test]
fn a_chain_links_each_entry_to_the_canonical_form_of_the_one_before() {
    // The chain as §3.2 states it, built and verified here rather than read
    // from a store: `prev` is the hash of the PRECEDING entry's canonical
    // form, and entry 1 carries null.
    let mut entries: Vec<Value> = Vec::new();
    let mut prev: Option<String> = None;
    for seq in 1..=3i64 {
        let entry = Value::Object(vec![
            ("prev".into(), prev.clone().map_or(Value::Null, Value::Str)),
            ("seq".into(), Value::Int(seq)),
            ("rec".into(), Value::Object(vec![
                ("id".into(), Value::Str(format!("pc-{seq:04x}"))),
                ("rev".into(), Value::Int(1)),
            ])),
        ]);
        prev = Some(entry_hash(&entry));
        entries.push(entry);
    }

    let mut expected: Option<String> = None;
    for entry in &entries {
        let declared = entry.get("prev").expect("prev");
        match &expected {
            None => assert_eq!(declared, &Value::Null, "entry 1 carries null"),
            Some(h) => assert_eq!(declared.as_str(), Some(h.as_str()), "chain break"),
        }
        expected = Some(entry_hash(entry));
    }

    // The kill: a chain that cannot break is not a chain. Move one byte of
    // content and the link must stop matching.
    let mut tampered = entries[0].clone();
    if let Value::Object(pairs) = &mut tampered {
        pairs.push(("x_tamper".into(), Value::Int(1)));
    }
    assert_ne!(entry_hash(&tampered), entry_hash(&entries[0]));
}

/// The parser's "already canonical" answer is exactly `canonical(v) == text`.
/// `read_log` hashes the stored bytes on that answer, so a false yes would
/// put a wrong hash in the chain and a false no only costs a re-serialize —
/// both directions are asserted, over the corpus and over each corpus line
/// bent every way a legal JSON text can differ from its canonical form.
#[test]
fn the_parser_knows_exactly_when_a_line_is_already_canonical() {
    use pecia_core::parse::parse_noting_canonical;
    let text = std::fs::read_to_string(repo_root().join(".pecia/work.jsonl")).expect("projection");
    let lines: Vec<&str> = text.split('\n').filter(|l| !l.is_empty()).collect();
    assert!(lines.len() > 1000);
    let agrees = |t: &str| -> bool {
        let (v, flag) = parse_noting_canonical(t).unwrap_or_else(|e| panic!("{t}: {e}"));
        assert_eq!(flag, canonical(&v) == t, "the flag is wrong for {t}");
        flag
    };
    let mut bent = 0;
    for line in &lines {
        assert!(agrees(line), "a committed line read as non-canonical");
        // Certainly structural, so certainly not canonical.
        assert!(!agrees(&format!(" {line}")));
        assert!(!agrees(&line.replacen("\"id\":", "\"id\" :", 1)));
        // Wherever these land — between tokens, or inside a string, where
        // some leave the text canonical — the answer must still be exact.
        for v in [
            line.replacen(":", ": ", 1),
            line.replacen(",", " ,", 1),
            line.replacen("/", "\\/", 1),
            line.replacen("a", "\\u0061", 1),
            line.replacen("\\n", "\\u000a", 1),
            line.replacen("\\n", "\\u000A", 1),
        ] {
            if v != *line && !agrees(&v) {
                bent += 1;
            }
        }
    }
    assert!(bent > lines.len() * 3, "the escape variants really were exercised: {bent}");
    // Key order: the same record with two keys swapped.
    let swapped = lines[0].replacen("{\"anchor\"", "{\"zzz\":0,\"anchor\"", 1);
    if swapped != lines[0] {
        assert!(!agrees(&swapped));
    }
    // The escapes the canonical writer DOES produce stay canonical.
    for t in [r#"{"a":"\u001f"}"#, r#"{"a":"\u0000\b\t\n\f\r\"\\"}"#, r#"{"a":"é  ","b":[1,-2,{"c":null}]}"#] {
        assert!(agrees(t), "{t}");
    }
    for t in [r#"{"b":1,"a":2}"#, r#"{"a":"\ud83d\ude00"}"#, r#"{"a":"\u0041"}"#, r#"{"a": 1}"#, "{\"a\":1}\n"] {
        assert!(!agrees(t), "{t}");
    }
}

/// A `Chain` keeps each head and the last hash current instead of deriving
/// them per append; over the whole corpus it must build exactly the entries
/// the single-shot `make_entry` builds, and admit exactly what
/// `cas_admissible` admits — including the refusal of a replayed revision.
#[test]
fn a_chain_builds_what_make_entry_builds() {
    use pecia_core::write::{cas_admissible, make_entry, Chain};
    let text = std::fs::read_to_string(repo_root().join(".pecia/work.jsonl")).expect("projection");
    let records: Vec<Value> = text.split('\n').filter(|l| !l.is_empty()).map(|l| parse(l).expect("record")).collect();
    assert!(records.len() > 1000);
    let (mut chain, mut entries) = (Chain::new(Vec::new()), Vec::new());
    for rec in &records {
        let obj = pecia_core::record::as_obj(rec).expect("object");
        assert_eq!(chain.admit(obj).is_ok(), cas_admissible(&entries, obj).is_ok());
        assert!(chain.admit(obj).is_ok(), "the committed history is CAS-clean");
        entries.push(make_entry(&entries, rec));
        chain.append(rec.clone());
    }
    assert_eq!(chain.entries(), &entries[..]);
    assert_eq!(chain.head_hash(), Some(entry_hash(entries.last().expect("entry")).as_str()));
    // The control: every revision, replayed, is refused by both.
    let rebuilt = Chain::new(entries.clone());
    for rec in records.iter().step_by(97) {
        let obj = pecia_core::record::as_obj(rec).expect("object");
        assert!(rebuilt.admit(obj).is_err() && cas_admissible(&entries, obj).is_err());
    }
}
