//! The Alloy model's theorems (spec/peciaV2.als), asserted over the real code.
//!
//! The model is checked at small scope and says nothing about the code; the
//! fixture tests bind a theorem at one hand-picked instance each. This drives
//! random operation sequences — creations, edits of status and of the
//! blocks/retires/parent edges, forced writes, stale revisions — through the
//! real write gate and the real compare-and-swap, and after every accepted
//! step asserts what the model proves, at scopes Alloy cannot reach
//! (pc-56e7849ddc32). Each check names the theorem it restates.
//!
//! V9 is checked against an ORACLE written from the model's `blockersOf`,
//! not against `compute_blockers`, which `ready` is built on: asserting
//! `ready` against its own ingredient would be circular (GP28).

use pecia_core::canonical::canonical;
use pecia_core::check::{diff_fields, read_log, records_of, run_checks};
use pecia_core::config::Config;
use pecia_core::finding::Severity;
use pecia_core::query::ready;
use pecia_core::record::{as_obj, get, get_str, strict_int, Pairs};
use pecia_core::write::{is_terminal, require_heads, write_gate, Chain};
use pecia_core::Value;
use std::collections::{BTreeSet, HashMap};

/// A small deterministic generator: the test is the same every run.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0 >> 33
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }
    fn chance(&mut self, percent: u64) -> bool {
        self.next() % 100 < percent
    }
}

fn s(v: &str) -> Value {
    Value::Str(v.to_string())
}

fn ids(v: &[String]) -> Value {
    Value::Array(v.iter().map(|x| s(x)).collect())
}

fn record(id: &str, rev: i64, status: &str, blocks: &[String], retires: &[String], parent: Option<&str>) -> Value {
    let terminal = is_terminal(Some(status));
    Value::Object(vec![
        ("id".into(), s(id)),
        ("rev".into(), Value::Int(rev)),
        ("type".into(), s("task")),
        ("title".into(), s(id)),
        ("status".into(), s(status)),
        ("priority".into(), Value::Int(2)),
        ("created".into(), s("2026-09-25")),
        ("updated".into(), s("2026-09-25")),
        (
            "edges".into(),
            Value::Object(vec![
                ("blocks".into(), ids(blocks)),
                ("retires".into(), ids(retires)),
                ("parent".into(), parent.map_or(Value::Null, s)),
                ("duplicate_of".into(), Value::Null),
                ("discovered_from".into(), Value::Null),
                ("caused_by".into(), Value::Null),
                ("validates".into(), Value::Null),
                ("supersedes".into(), Value::Null),
            ]),
        ),
        ("disposition".into(), if terminal { s("finished in the generator") } else { Value::Null }),
        ("evidence".into(), s(if terminal { "true" } else { "unknown" })),
        ("owner".into(), s("test:theorems")),
        ("labels".into(), Value::Array(Vec::new())),
        ("body".into(), s("")),
    ])
}

fn list(h: &Pairs, key: &str) -> Vec<String> {
    match get(h, "edges").and_then(as_obj).and_then(|e| get(e, key)) {
        Some(Value::Array(a)) => a.iter().filter_map(Value::as_str).map(str::to_string).collect(),
        _ => Vec::new(),
    }
}

fn parent(h: &Pairs) -> Option<String> {
    get(h, "edges").and_then(as_obj).and_then(|e| get_str(e, "parent")).map(str::to_string)
}

/// One step: a candidate revision, or a creation. Edges point only at ids the
/// generator has made, plus an occasional one it has not (E003's territory).
fn pick(rng: &mut Rng, made: &[String], n: usize) -> Vec<String> {
    let mut out: Vec<String> = (0..n).filter_map(|_| made.get(rng.below(made.len())).cloned()).collect();
    if rng.chance(3) {
        out.push("pc-never-made".into());
    }
    out.sort();
    out.dedup();
    out
}

fn candidate(rng: &mut Rng, chain: &Chain, made: &[String], next_id: &mut usize) -> Value {
    if made.is_empty() || rng.chance(25) {
        *next_id += 1;
        let id = format!("pc-t{:03}", *next_id);
        let blocks = if rng.chance(40) {
            let n = 1 + rng.below(2);
            pick(rng, made, n)
        } else {
            Vec::new()
        };
        let par = if rng.chance(20) { made.get(rng.below(made.len())).cloned() } else { None };
        return record(&id, 1, "open", &blocks, &[], par.as_deref());
    }
    let id = made[rng.below(made.len())].clone();
    let head = chain.head(&id).expect("made ids have heads");
    let rev = strict_int(get(head, "rev")).expect("rev");
    let mut status = get_str(head, "status").unwrap_or("open").to_string();
    let (mut blocks, mut retires, mut par) = (list(head, "blocks"), list(head, "retires"), parent(head));
    match rng.below(6) {
        0 => status = ["open", "in-progress", "done", "dropped"][rng.below(4)].to_string(),
        1 => {
            let n = rng.below(3);
            blocks = pick(rng, made, n);
        }
        2 => {
            let n = rng.below(2);
            retires = pick(rng, made, n);
        }
        3 => par = if rng.chance(50) { made.get(rng.below(made.len())).cloned() } else { None },
        4 => {
            let n = rng.below(2);
            blocks = pick(rng, made, n);
            status = ["open", "done"][rng.below(2)].to_string();
        }
        _ => retires = Vec::new(),
    }
    record(&id, rev + 1, &status, &blocks, &retires, par.as_deref())
}

fn set_forced(rec: &mut Value) {
    if let Value::Object(p) = rec {
        p.push(("forced".into(), Value::Bool(true)));
    }
}

/// The model's `blockersOf`, written from spec/peciaV2.als rather than from
/// the code: the non-terminal heads naming `i` in blocks or retires, or
/// having `i` as their parent.
fn blockers_of(heads: &[(String, &Pairs)], i: &str) -> BTreeSet<String> {
    heads
        .iter()
        .filter(|(_, h)| !is_terminal(get_str(h, "status")))
        .filter(|(_, h)| list(h, "blocks").iter().chain(list(h, "retires").iter()).any(|t| t == i) || parent(h).as_deref() == Some(i))
        .map(|(j, _)| j.clone())
        .collect()
}

fn check_theorems(chain: &Chain, cfg: &Config, forced_ever: bool, step: usize, seed: u64) {
    let at = format!("seed {seed}, step {step}");
    let entries = chain.entries();

    // V1 / V2 — fork-freedom and one tip: the log as stored reads back as one
    // unbroken chain, with no chain or derivation finding.
    let bytes: String = entries.iter().map(|e| canonical(e) + "\n").collect();
    let (read, read_findings) = read_log(bytes.as_bytes());
    assert!(read_findings.is_empty(), "{at}: the stored log does not read back clean: {:?}", read_findings.iter().map(|f| &f.message).collect::<Vec<_>>());
    assert_eq!(read.len(), entries.len(), "{at}");

    // V3 — last-in-chain coincides with max rev: per id, revisions run 1..n
    // in chain order, so the last entry for an id is its head.
    let mut seen: HashMap<String, i64> = HashMap::new();
    for e in entries {
        let r = e.get("rec").and_then(as_obj).expect("rec");
        let (id, rev) = (get_str(r, "id").expect("id").to_string(), strict_int(get(r, "rev")).expect("rev"));
        let before = seen.insert(id.clone(), rev).unwrap_or(0);
        assert_eq!(rev, before + 1, "{at}: {id} rev {rev} follows {before}");
    }

    // touchedDerived — each entry's `touched` is what the diff of its record
    // against the head before it says, recomputed here.
    let mut heads_so_far: HashMap<String, &Pairs> = HashMap::new();
    for e in entries {
        let r = e.get("rec").and_then(as_obj).expect("rec");
        let id = get_str(r, "id").expect("id").to_string();
        let expect = diff_fields(heads_so_far.get(&id).copied(), r);
        let got: Vec<String> = match e.get("touched") {
            Some(Value::Array(a)) => a.iter().filter_map(Value::as_str).map(str::to_string).collect(),
            _ => panic!("{at}: an entry without touched"),
        };
        assert_eq!(got, expect, "{at}: {id}'s touched is not derived");
        heads_so_far.insert(id, r);
    }

    let records = records_of(entries);
    let errors: Vec<_> = run_checks(&records, Vec::new(), cfg, None).into_iter().filter(|f| f.severity == Severity::Error).collect();

    // V4 — publishing preserves clean: from an empty timeline, a history with
    // no forced step is clean.
    if !forced_ever {
        assert!(errors.is_empty(), "{at}: an unforced history is dirty: {:?}", errors.iter().map(|f| (f.code.as_str(), &f.message)).collect::<Vec<_>>());
    }
    // V7 — blame containment: dirt exists only where a forced entry does.
    if !errors.is_empty() {
        assert!(
            entries.iter().any(|e| e.get("rec").and_then(as_obj).and_then(|r| get(r, "forced")) == Some(&Value::Bool(true))),
            "{at}: dirty with no forced entry: {:?}",
            errors.iter().map(|f| f.code.as_str()).collect::<Vec<_>>()
        );
        return; // V8 and V9 are stated over clean ledgers.
    }

    let heads = require_heads(&records, &[]).expect("a clean ledger has heads");
    let ready_ids: BTreeSet<String> = ready(&heads, None).iter().map(|h| get_str(h, "id").expect("id").to_string()).collect();
    let open: Vec<&(String, &Pairs)> = heads.iter().filter(|(_, h)| get_str(h, "status") == Some("open")).collect();

    // V9 — ready and blocked partition the open heads, against the oracle.
    for (id, _) in &open {
        assert_eq!(ready_ids.contains(id), blockers_of(&heads, id).is_empty(), "{at}: {id} is ready={} but has blockers {:?}", ready_ids.contains(id), blockers_of(&heads, id));
    }
    // V8 — no deadlock: a clean ledger whose every non-terminal head is open,
    // with at least one, has something ready.
    let all_open = heads.iter().all(|(_, h)| is_terminal(get_str(h, "status")) || get_str(h, "status") == Some("open"));
    if all_open && !open.is_empty() {
        assert!(!ready_ids.is_empty(), "{at}: deadlock — {} open heads and none ready", open.len());
    }
}

#[test]
fn the_models_theorems_hold_over_random_histories() {
    let cfg = Config::default();
    let (mut accepted, mut refused, mut forced, mut stale) = (0usize, 0usize, 0usize, 0usize);
    for seed in 1..=60u64 {
        let mut rng = Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15));
        let mut chain = Chain::new(Vec::new());
        let mut made: Vec<String> = Vec::new();
        let mut next_id = 0usize;
        let mut forced_ever = false;
        for step in 0..70 {
            let mut rec = candidate(&mut rng, &chain, &made, &mut next_id);
            let id = get_str(as_obj(&rec).expect("rec"), "id").expect("id").to_string();

            // V3's other half: a stale revision is refused by the CAS itself.
            if chain.head(&id).is_some() && rng.chance(10) {
                let mut stale_rec = rec.clone();
                if let Value::Object(p) = &mut stale_rec {
                    let slot = p.iter_mut().find(|(k, _)| k == "rev").expect("rev");
                    slot.1 = Value::Int(strict_int(Some(&slot.1)).expect("rev") - 1);
                }
                assert!(chain.admit(as_obj(&stale_rec).expect("rec")).is_err(), "seed {seed}, step {step}: a stale revision was admitted");
                stale += 1;
            }

            let force = rng.chance(4);
            if force {
                set_forced(&mut rec);
            } else {
                let records = records_of(chain.entries());
                if !write_gate(&records, &rec, &cfg).is_empty() {
                    refused += 1;
                    continue;
                }
            }
            chain.admit(as_obj(&rec).expect("rec")).expect("the generator writes head + 1");
            chain.append(rec);
            accepted += 1;
            if force {
                forced += 1;
                forced_ever = true;
            }
            if !made.contains(&id) {
                made.push(id);
            }
            check_theorems(&chain, &cfg, forced_ever, step, seed);
        }
    }
    // Non-vacuity (VP4): every arm of the generator fired, so the theorems
    // were checked over accepted, refused, forced and stale writes alike.
    assert!(accepted > 1000 && refused > 100 && forced > 50 && stale > 50, "accepted {accepted}, refused {refused}, forced {forced}, stale {stale}");
}
