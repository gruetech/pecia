//! The local write gate against the full one.
//!
//! `write_gate_local` computes a write's new errors from what one appended
//! revision can change; `write_gate` diffs the whole checker before and
//! after. They must give the same findings in the same order for every write
//! the local one is used for. This drives both over the committed corpus —
//! and over the corpus damaged the ways the graph checks care about, so the
//! "before" side is not empty — with thousands of candidates bent at random
//! in the ways a write can bend a record.

use pecia_core::check::records_of;
use pecia_core::config::Config;
use pecia_core::finding::Finding;
use pecia_core::index::query_view;
use pecia_core::record::{as_obj, get, get_str, strict_int, Pairs};
use pecia_core::write::{require_heads, write_gate, write_gate_local, Chain};
use pecia_core::{parse, Value};
use std::path::PathBuf;

fn corpus() -> Vec<Value> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("root");
    let text = std::fs::read_to_string(root.join(".pecia/work.jsonl")).expect("projection");
    text.split('\n').filter(|l| !l.is_empty()).map(|l| parse(l).expect("record")).collect()
}

/// A small deterministic generator: the test is the same every run.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0 >> 33
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
    fn pick<'a, T>(&mut self, xs: &'a [T]) -> &'a T {
        &xs[self.below(xs.len())]
    }
}

fn set(rec: &mut Vec<(String, Value)>, key: &str, v: Value) {
    match rec.iter_mut().find(|(k, _)| k == key) {
        Some(slot) => slot.1 = v,
        None => rec.push((key.to_string(), v)),
    }
}

fn edges(rec: &mut Vec<(String, Value)>) -> &mut Vec<(String, Value)> {
    if !matches!(rec.iter().find(|(k, _)| k == "edges"), Some((_, Value::Object(_)))) {
        set(rec, "edges", Value::Object(Vec::new()));
    }
    match rec.iter_mut().find(|(k, _)| k == "edges") {
        Some((_, Value::Object(e))) => e,
        _ => unreachable!(),
    }
}

fn push_list(rec: &mut Vec<(String, Value)>, key: &str, t: &str) {
    let e = edges(rec);
    let mut items = match e.iter().find(|(k, _)| k == key) {
        Some((_, Value::Array(a))) => a.clone(),
        _ => Vec::new(),
    };
    items.push(Value::Str(t.to_string()));
    set(e, key, Value::Array(items));
}

/// Bend a record the ways a write can: status, type, every edge, evidence,
/// context, priority — and now and then into an unsound shape.
fn bend(rng: &mut Rng, rec: &mut Vec<(String, Value)>, ids: &[String], decisions: &[String], self_id: &str) {
    let target = |rng: &mut Rng| -> String {
        match rng.below(10) {
            0 => "pc-ghost".into(),
            1 => self_id.into(),
            _ => rng.pick(ids).clone(),
        }
    };
    for _ in 0..1 + rng.below(3) {
        match rng.below(14) {
            0 | 1 => {
                let st = *rng.pick(&["open", "in-progress", "done", "dropped", "superseded", "parked"]);
                set(rec, "status", Value::Str(st.into()));
                let disp = *rng.pick(&["", "   ", "closed because the work landed", "x"]);
                set(rec, "disposition", if disp.is_empty() { Value::Null } else { Value::Str(disp.into()) });
            }
            2 => set(rec, "type", Value::Str((*rng.pick(&["task", "defect", "decision", "milestone", "question", "saga"])).into())),
            3 | 4 => {
                let t = target(rng);
                push_list(rec, "blocks", &t);
            }
            5 => {
                let t = target(rng);
                push_list(rec, "retires", &t);
            }
            6 => {
                let t = target(rng);
                set(edges(rec), "parent", Value::Str(t));
            }
            7 => {
                let t = if decisions.is_empty() || rng.below(3) == 0 { target(rng) } else { rng.pick(decisions).clone() };
                set(edges(rec), "supersedes", Value::Str(t));
            }
            8 => set(rec, "evidence", Value::Str((*rng.pick(&["unknown", "true", "claims:x", "nope:x", "/abs/run.sh --flag", "prose words only", "sh ../escape.sh", "cargo test"])).into())),
            9 => set(rec, "context", Value::Str((*rng.pick(&["nope:y", "claims:z"])).into())),
            10 => set(rec, "priority", rng.pick(&[Value::Int(0), Value::Int(3), Value::Int(9), Value::Str("high".into())]).clone()),
            11 => set(edges(rec), "no_edges", Value::Bool(true)),
            12 => set(rec, "labels", Value::Array(vec![Value::Str("l".into())])),
            _ => {
                if rng.below(4) == 0 {
                    // Unsound: the gate still reports it, and nothing else moves.
                    set(rec, "title", Value::Int(7));
                } else {
                    set(rec, "x-extension", Value::Int(rng.below(3) as i64));
                }
            }
        }
    }
}

fn findings(v: &[Finding]) -> Vec<(String, String, Option<String>, String)> {
    v.iter().map(|f| (f.severity.as_str().to_string(), f.code.as_str().to_string(), f.id.clone(), f.message.clone())).collect()
}

/// A candidate: the next revision of a head, or rev 1 of a new id.
fn candidate(rng: &mut Rng, heads: &[(String, &Pairs)], ids: &[String], decisions: &[String], n: usize) -> Value {
    let mut rec: Vec<(String, Value)>;
    let id: String;
    if rng.below(5) == 0 {
        id = format!("pc-new{n}");
        rec = vec![
            ("id".into(), Value::Str(id.clone())),
            ("rev".into(), Value::Int(1)),
            ("type".into(), Value::Str("task".into())),
            ("title".into(), Value::Str("a new record".into())),
            ("status".into(), Value::Str("open".into())),
            ("priority".into(), Value::Int(2)),
            ("created".into(), Value::Str("2026-09-25".into())),
            ("updated".into(), Value::Str("2026-09-25".into())),
            ("edges".into(), Value::Object(vec![("blocks".into(), Value::Array(vec![])), ("retires".into(), Value::Array(vec![]))])),
            ("disposition".into(), Value::Null),
            ("evidence".into(), Value::Str("unknown".into())),
            ("owner".into(), Value::Str("t".into())),
            ("labels".into(), Value::Array(vec![])),
            ("body".into(), Value::Str(String::new())),
        ];
    } else {
        let (hid, head) = rng.pick(heads);
        id = hid.clone();
        rec = head.to_vec();
        let rev = strict_int(get(head, "rev")).expect("sound") + 1;
        set(&mut rec, "rev", Value::Int(rev));
    }
    bend(rng, &mut rec, ids, decisions, &id);
    Value::Object(rec)
}

/// Build a timeline from records, the way a writer would.
fn timeline(records: Vec<Value>) -> Vec<Value> {
    let mut chain = Chain::new(Vec::new());
    for r in records {
        chain.append(r);
    }
    chain.into_entries().into_vec()
}

fn assert_gates_agree(entries: &[Value], seed: u64, cases: usize) -> usize {
    let cfg = Config::parse("extra_statuses: [parked]\nplanned: [pc-planned]\n");
    let records = records_of(entries);
    let heads = require_heads(&records, &[]).expect("a timeline the index would certify");
    let views: Vec<(String, Value)> = heads.iter().map(|(k, h)| (k.clone(), Value::Object(query_view(h)))).collect();
    let as_pairs: Vec<(&str, &Pairs)> = heads.iter().map(|(k, h)| (k.as_str(), *h)).collect();
    let as_views: Vec<(&str, &Pairs)> = views.iter().map(|(k, v)| (k.as_str(), as_obj(v).expect("view"))).collect();
    let ids: Vec<String> = heads.iter().map(|(k, _)| k.clone()).collect();
    let decisions: Vec<String> = heads.iter().filter(|(_, h)| get_str(h, "type") == Some("decision")).map(|(k, _)| k.clone()).collect();
    // The candidates are drawn in sequence, so the test is the same every
    // run; each is then checked on its own, in parallel — the full gate is
    // the cost the local one exists to avoid.
    let mut rng = Rng(seed);
    let drawn: Vec<Value> = (0..cases).map(|n| candidate(&mut rng, &heads, &ids, &decisions, n)).collect();
    let refused = pecia_core::par::map(cases, |n| {
        let cand = &drawn[n];
        let x = get_str(as_obj(cand).expect("record"), "id").expect("id");
        let head = heads.iter().find(|(k, _)| k == x).map(|(_, h)| *h);
        let full = findings(&write_gate(&records, cand, &cfg));
        let local = findings(&write_gate_local(&as_pairs, head, cand, records.len(), &cfg));
        let viewed = findings(&write_gate_local(&as_views, head, cand, records.len(), &cfg));
        assert_eq!(local, full, "case {n}: {}", pecia_core::canonical(cand));
        assert_eq!(viewed, full, "case {n} over query views");
        !full.is_empty()
    });
    refused.into_iter().filter(|r| *r).count()
}

#[test]
fn write_gate_local_equals_write_gate() {
    let entries = timeline(corpus());
    let refused = assert_gates_agree(&entries, 1, 1500);
    assert!(refused > 300, "the candidates really do get refused: {refused}");
}

#[test]
fn write_gate_local_equals_write_gate_over_a_damaged_timeline() {
    // Pre-existing cross-record damage, so a write can repeat, narrow or
    // widen a finding already there: open records blocking closed ones,
    // retirement claims, cycles, and superseded decision lineages.
    let base = corpus();
    let entries = timeline(base.clone());
    let records = records_of(&entries);
    let heads = require_heads(&records, &[]).expect("clean");
    let ids: Vec<String> = heads.iter().map(|(k, _)| k.clone()).collect();
    let decisions: Vec<String> = heads.iter().filter(|(_, h)| get_str(h, "type") == Some("decision")).map(|(k, _)| k.clone()).collect();
    let mut rng = Rng(7);
    let mut records: Vec<Value> = base;
    let mut latest: std::collections::HashMap<String, Vec<(String, Value)>> = heads.iter().map(|(k, h)| (k.clone(), h.to_vec())).collect();
    for _ in 0..300 {
        let id = rng.pick(&ids).clone();
        let mut rec = latest[&id].clone();
        let rev = strict_int(rec.iter().find(|(k, _)| k == "rev").map(|(_, v)| v)).expect("rev") + 1;
        set(&mut rec, "rev", Value::Int(rev));
        match rng.below(4) {
            0 => push_list(&mut rec, "blocks", rng.pick(&ids)),
            1 => push_list(&mut rec, "retires", rng.pick(&ids)),
            2 if !decisions.is_empty() => set(edges(&mut rec), "supersedes", Value::Str(rng.pick(&decisions).clone())),
            _ => set(edges(&mut rec), "parent", Value::Str(rng.pick(&ids).clone())),
        }
        latest.insert(id, rec.clone());
        records.push(Value::Object(rec));
    }
    // And a cycle for certain: two records blocking each other.
    for (a, b) in [(&ids[0], &ids[1]), (&ids[1], &ids[0])] {
        let mut rec = latest[a].clone();
        let rev = strict_int(rec.iter().find(|(k, _)| k == "rev").map(|(_, v)| v)).expect("rev") + 1;
        set(&mut rec, "rev", Value::Int(rev));
        push_list(&mut rec, "blocks", b);
        latest.insert(a.clone(), rec.clone());
        records.push(Value::Object(rec));
    }
    let damaged = timeline(records);
    let cfg = Config::default();
    let before = pecia_core::check::run_checks(&records_of(&damaged), Vec::new(), &cfg, None);
    assert!(before.iter().any(|f| f.code.as_str() == "E004"), "the damage includes a cycle");
    assert!(before.iter().any(|f| f.code.as_str() == "E016" || f.code.as_str() == "E012"), "and blocking or retirement");
    assert_gates_agree(&damaged, 99, 1500);
}
