//! The suite's IN-PROCESS arms, restated against this crate.
//!
//! `tests/test_pecia.py` drives whichever implementation `PECIA_TEST_CLI`
//! names through its process boundary, and that is the port's oracle. A few
//! dozen of its arms reach past the boundary instead — into a function, a
//! table, a cap constant of the reference — and skip against a compiled
//! binary. Without this file the properties those arms measure would have no
//! test on the Rust side at all, and a green scoreboard would say nothing
//! about them. Each group names the Python class it restates; the arms keep
//! their kill/control pairing, so none of them can pass over a check that
//! refuses everything or nothing.

use pecia_core::audit::AUDIT_VALUE_CAP;
use pecia_core::check::{run_checks, timeline_checks};
use pecia_core::config::{Config, CORE_STATUSES, CORE_TYPES};
use pecia_core::finding::{error, Code, Finding};
use pecia_core::record::{as_obj, validate_record, Pairs};
use pecia_core::sync::{published_heads, published_revisions};
use pecia_core::text::{capped_array, capped_seq, safe_text, ID_CAP, MESSAGE_CAP, MESSAGE_ITEM_CAP, MESSAGE_VALUE_CAP};
use pecia_core::write::{cas_admissible, evidence_kind, make_entry, project, refusal_message, require_heads, DATE_CAP, OWNER_CAP, TITLE_CAP, VOCAB_CAP};
use pecia_core::{entry_hash, Value};

fn s(v: &str) -> Value {
    Value::Str(v.to_string())
}

fn obj(pairs: Vec<(&str, Value)>) -> Value {
    Value::Object(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn strs(items: &[&str]) -> Value {
    Value::Array(items.iter().map(|x| s(x)).collect())
}

fn set(v: &mut Value, key: &str, val: Value) {
    let Value::Object(pairs) = v else { panic!("object") };
    match pairs.iter_mut().find(|(k, _)| k == key) {
        Some(slot) => slot.1 = val,
        None => pairs.push((key.to_string(), val)),
    }
}

/// The suite's `record()`: a valid open task, with overrides.
fn record(over: Vec<(&str, Value)>) -> Value {
    let mut edges = obj(vec![
        ("blocks", Value::Array(vec![])),
        ("retires", Value::Array(vec![])),
        ("parent", Value::Null),
        ("duplicate_of", Value::Null),
        ("discovered_from", Value::Null),
        ("caused_by", Value::Null),
        ("validates", Value::Null),
        ("supersedes", Value::Null),
    ]);
    let mut r = obj(vec![
        ("id", s("pc-aaaa")),
        ("rev", Value::Int(1)),
        ("type", s("task")),
        ("title", s("A task")),
        ("status", s("open")),
        ("priority", Value::Int(2)),
        ("created", s("2026-07-20")),
        ("updated", s("2026-07-20")),
        ("disposition", Value::Null),
        ("evidence", s("unknown")),
        ("owner", s("test:unit")),
        ("labels", Value::Array(vec![])),
        ("body", s("")),
    ]);
    for (k, v) in over {
        if k == "edges" {
            let Value::Object(e) = v else { panic!("edges") };
            for (ek, ev) in e {
                set(&mut edges, &ek, ev);
            }
        } else {
            set(&mut r, k, v);
        }
    }
    set(&mut r, "edges", edges);
    r
}

fn pairs(v: &Value) -> &Pairs {
    as_obj(v).expect("record")
}

/// The suite's `build_log`: a well-formed chain with derived `touched`,
/// deliberately NOT enforcing the CAS, so fixtures can hold what a writer
/// refuses.
fn chain(records: Vec<Value>) -> Vec<Value> {
    let mut entries: Vec<Value> = Vec::new();
    for r in records {
        let e = make_entry(&entries, &r);
        entries.push(e);
    }
    entries
}

fn relink(entries: &mut [Value]) {
    let mut prev: Option<String> = None;
    for e in entries.iter_mut() {
        set(e, "prev", prev.map_or(Value::Null, Value::Str));
        prev = Some(entry_hash(e));
    }
}

fn checks(records: &[Value]) -> Vec<Finding> {
    run_checks(&records.iter().collect::<Vec<_>>(), Vec::new(), &Config::default(), None)
}

fn first(findings: &[Finding], code: Code) -> String {
    findings.iter().find(|f| f.code == code).unwrap_or_else(|| panic!("no {code:?} in {findings:?}")).message.clone()
}

fn len(s: &str) -> usize {
    s.chars().count()
}

// --- ABooleanRevIsNotAHead (pc-d89f) --------------------------------------

fn head(rev: Value) -> Value {
    record(vec![("rev", rev), ("created", s("2026-01-01")), ("updated", s("2026-01-01")), ("title", s("t")), ("owner", s("o"))])
}

fn lone_entry(rec: Value) -> Value {
    obj(vec![("seq", Value::Int(1)), ("prev", Value::Null), ("touched", Value::Array(vec![])), ("rec", rec)])
}

#[test]
fn bool_rev_kill_the_cas_does_not_accept_a_boolean_head() {
    let why = cas_admissible(&[lone_entry(head(Value::Bool(true)))], pairs(&head(Value::Int(2)))).unwrap_err();
    assert!(why.contains("no head"), "{why}");
}

#[test]
fn bool_rev_control_a_real_rev_one_head_still_admits_rev_two() {
    cas_admissible(&[lone_entry(head(Value::Int(1)))], pairs(&head(Value::Int(2)))).expect("admitted");
}

#[test]
fn bool_rev_kill_make_entry_does_not_diff_against_a_boolean_head() {
    // The candidate CHANGES a field, so a false baseline would show as
    // ["title"] where the absence of one shows the creation's [].
    let mut candidate = head(Value::Int(2));
    set(&mut candidate, "title", s("moved"));
    let e = make_entry(&[lone_entry(head(Value::Bool(true)))], &candidate);
    assert_eq!(e.get("touched"), Some(&Value::Array(vec![])));
}

#[test]
fn bool_rev_kill_a_boolean_is_never_grouped_as_rev_one() {
    // The reference's group_revisions put `true` in the rev-1 bucket
    // (hash(True) == hash(1)). Here an unsound record is refused whole
    // before any grouping, so it cannot become a duplicate of rev 1.
    let err = require_heads(&[&head(Value::Int(1)), &head(Value::Bool(true))], &[]).unwrap_err();
    assert!(err.contains("malformed"), "{err}");
    assert!(!err.contains("appears 2x"), "{err}");
}

#[test]
fn bool_rev_kill_the_published_identity_excludes_a_boolean_rev() {
    let entries = [lone_entry(head(Value::Bool(true)))];
    assert!(published_heads(&entries).is_empty());
    assert!(published_revisions(&entries).is_empty());
}

#[test]
fn bool_rev_control_the_published_identity_still_sees_a_real_rev() {
    let entries = [lone_entry(head(Value::Int(3)))];
    assert_eq!(published_heads(&entries).into_iter().collect::<Vec<_>>(), [("pc-aaaa".to_string(), 3)]);
    assert_eq!(published_revisions(&entries).into_keys().collect::<Vec<_>>(), [("pc-aaaa".to_string(), 3)]);
}

#[test]
fn bool_rev_the_checker_and_the_resolvers_agree() {
    let bad = head(Value::Bool(true));
    let statuses: Vec<String> = CORE_STATUSES.iter().map(|x| x.to_string()).collect();
    let types: Vec<String> = CORE_TYPES.iter().map(|x| x.to_string()).collect();
    assert!(validate_record(pairs(&bad), &statuses, &types, None).iter().any(|f| f.code == Code::E001));
    assert!(published_heads(&[lone_entry(bad.clone())]).is_empty());
    assert!(cas_admissible(&[lone_entry(bad)], pairs(&head(Value::Int(2)))).is_err());
}

// --- DiagnosticContractSurvivesTheCap -------------------------------------

const LONG: usize = 9000;

#[test]
fn cap_kill_e001_identity_survives_a_long_condemned_value() {
    let m = first(&checks(&[record(vec![("type", s(&"x".repeat(LONG)))])]), Code::E001);
    assert!(m.contains("[rev 1]"), "{m}");
    assert!(len(&m) < MESSAGE_CAP);
}

#[test]
fn cap_control_a_short_e001_value_is_named_in_full() {
    let m = first(&checks(&[record(vec![("type", s("nosuchtype"))])]), Code::E001);
    assert_eq!(m, "unknown type: \"nosuchtype\" [rev 1]");
}

#[test]
fn cap_control_a_valid_record_is_clean() {
    assert!(checks(&[record(vec![])]).iter().all(|f| f.severity != pecia_core::finding::Severity::Error));
}

#[test]
fn cap_the_identity_tag_survives_a_long_malformed_rev() {
    let m = first(&checks(&[record(vec![("rev", s(&"b".repeat(LONG)))])]), Code::E001);
    assert!(m.contains("malformed"), "{m}");
    assert!(len(&m) < MESSAGE_CAP);
}

fn e014_fixture(large: bool) -> Vec<Value> {
    let (mut a, mut b) = (record(vec![]), record(vec![("rev", Value::Int(2)), ("priority", Value::Int(0)), ("title", s("renamed"))]));
    if large {
        for i in 0..1200 {
            set(&mut a, &format!("x{i:04}"), Value::Int(i));
            set(&mut b, &format!("x{i:04}"), Value::Int(i + 1));
        }
    }
    chain(vec![a, b])
}

#[test]
fn cap_kill_e014_names_the_order_on_a_large_field_set() {
    let mut es = e014_fixture(true);
    let Some(Value::Array(t)) = es[1].get("touched").cloned() else { panic!("touched") };
    assert!(t.len() > 1000, "fixture premise: a large derived field set");
    set(&mut es[1], "touched", Value::Array(t.into_iter().rev().collect()));
    relink(&mut es);
    let m = first(&timeline_checks(&es, None, true), Code::E014);
    assert!(m.contains("non-canonical order"), "{m}");
    assert!(m.contains("reorder to"), "{m}");
    assert!(len(&m) < MESSAGE_CAP);
}

#[test]
fn cap_control_e014_names_the_order_on_a_small_field_set() {
    let mut es = e014_fixture(false);
    set(&mut es[1], "touched", strs(&["title", "priority"]));
    relink(&mut es);
    let m = first(&timeline_checks(&es, None, true), Code::E014);
    assert!(m.contains("non-canonical order"), "{m}");
    assert!(m.contains("[\"title\",\"priority\"]"), "a small array is printed whole: {m}");
}

fn one_e016(count: usize) -> Vec<Value> {
    let mut out: Vec<Value> = (0..count)
        .map(|i| record(vec![("id", s(&format!("pc-b{i:04}"))), ("title", s(&format!("blocker {i}"))), ("edges", obj(vec![("blocks", strs(&["pc-shut"]))]))]))
        .collect();
    out.push(record(vec![("id", s("pc-shut")), ("title", s("closed under all of them")), ("status", s("done")), ("disposition", s("closed while blocked")), ("evidence", s("true"))]));
    out
}

#[test]
fn cap_kill_e016_keeps_its_remedy_and_counts_what_it_omits() {
    let m = first(&checks(&one_e016(1000)), Code::E016);
    assert!(m.contains("close the blocker(s)"), "{m}");
    assert!(m.contains("of 1000"), "{m}");
    assert!(m.contains("more of"), "{m}");
    assert!(len(&m) < MESSAGE_CAP);
}

#[test]
fn cap_control_e016_with_one_blocker_names_it_in_full() {
    let m = first(&checks(&one_e016(1)), Code::E016);
    assert!(m.contains("pc-b0000"), "{m}");
    assert!(!m.contains("more of"), "{m}");
}

#[test]
fn cap_control_a_terminal_blocker_is_clean() {
    let mut recs = one_e016(1);
    recs[0] = record(vec![("id", s("pc-b0000")), ("title", s("blocker 0")), ("status", s("done")), ("disposition", s("done")), ("evidence", s("true")), ("edges", obj(vec![("blocks", strs(&["pc-shut"]))]))]);
    assert!(!checks(&recs).iter().any(|f| f.code == Code::E016));
}

#[test]
fn cap_kill_e012_keeps_its_remedy_under_many_claimants() {
    let mut recs: Vec<Value> = (0..1000)
        .map(|i| record(vec![("id", s(&format!("pc-r{i:04}"))), ("title", s(&format!("retirer {i}"))), ("status", s("done")), ("disposition", s("done")), ("evidence", s("true")), ("edges", obj(vec![("retires", strs(&["pc-tgt0"]))]))]))
        .collect();
    recs.push(record(vec![("id", s("pc-tgt0")), ("title", s("still open"))]));
    let m = first(&checks(&recs), Code::E012);
    assert!(m.contains("drop the retires claim"), "{m}");
    assert!(m.contains("of 1000"), "{m}");
    assert!(len(&m) < MESSAGE_CAP);
}

#[test]
fn cap_kill_e004_bounds_a_long_cycle() {
    let ring: Vec<Value> = (0..800)
        .map(|i| record(vec![("id", s(&format!("pc-c{i:04}"))), ("title", s(&format!("ring {i}"))), ("edges", obj(vec![("blocks", strs(&[&format!("pc-c{:04}", (i + 1) % 800)]))]))]))
        .collect();
    let m = first(&checks(&ring), Code::E004);
    assert!(len(&m) < MESSAGE_CAP);
    assert!(m.contains("more of"), "{m}");
}

#[test]
fn cap_control_a_short_cycle_is_printed_whole() {
    let m = first(&checks(&[
        record(vec![("id", s("pc-c1")), ("title", s("one")), ("edges", obj(vec![("blocks", strs(&["pc-c2"]))]))]),
        record(vec![("id", s("pc-c2")), ("title", s("two")), ("edges", obj(vec![("blocks", strs(&["pc-c1"]))]))]),
    ]), Code::E004);
    assert!(m.contains("pc-c1 -> pc-c2"), "{m}");
    assert!(!m.contains("more of"), "{m}");
}

#[test]
fn cap_kill_the_write_gate_keeps_its_force_hint() {
    let wrapped = refusal_message(&"x".repeat(MESSAGE_CAP * 2));
    assert!(wrapped.starts_with("write refused: "));
    assert!(wrapped.ends_with("(--force to override)"));
    assert!(len(&wrapped) <= MESSAGE_CAP);
}

fn ids(n: usize) -> Vec<String> {
    (0..n).map(|i| format!("pc-b{i:04}")).collect()
}

#[test]
fn cap_two_participant_sets_sharing_a_prefix_do_not_render_alike() {
    let one = ids(1000);
    let mut two = one.clone();
    *two.last_mut().expect("last") = "pc-zzzz".into();
    assert_ne!(capped_seq(&one, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", "), capped_seq(&two, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", "));
}

#[test]
fn cap_a_list_inside_the_budget_is_untouched() {
    assert_eq!(capped_seq(&["pc-a".into(), "pc-b".into()], MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", "), "pc-a, pc-b");
    assert_eq!(capped_array(&strs(&["priority", "title"]), MESSAGE_VALUE_CAP), "[\"priority\",\"title\"]");
}

#[test]
fn cap_one_enormous_element_cannot_flood_the_budget() {
    let rendered = capped_seq(&["y".repeat(4000), "pc-b".into()], MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ");
    assert!(len(&rendered) <= MESSAGE_VALUE_CAP);
}

// --- OutputBoundaryIntegral / OutputCaps ----------------------------------

/// Every projected field and the cap that bounds it; the rest emit no
/// ledger-derived string.
const CAPPED: &[(&str, usize)] = &[
    ("id", ID_CAP), ("type", VOCAB_CAP), ("status", VOCAB_CAP), ("title", TITLE_CAP),
    ("owner", OWNER_CAP), ("created", DATE_CAP), ("updated", DATE_CAP), ("target", DATE_CAP),
];
const UNCAPPED: &[&str] = &["rev", "priority", "forced", "edges", "body_chars", "disposition_chars", "disposition_words", "evidence_kind", "labels_count"];
const COMMANDS: &[&str] = &["next", "ready", "blocked", "graph", "gantt", "board", "written"];
const PROSE_FIELDS: &[&str] = &["body", "disposition", "evidence", "labels"];

fn command_for(field: &str) -> &'static str {
    let cfg = Config::default();
    let probe = record(vec![("forced", Value::Bool(true)), ("target", s("2026-01-01"))]);
    COMMANDS.iter().find(|c| project(c, pairs(&probe), &cfg).get(field).is_some()).copied().unwrap_or_else(|| panic!("no command emits {field}"))
}

fn projected(command: &str, rec: &Value, field: &str) -> Value {
    project(command, pairs(rec), &Config::default()).get(field).cloned().expect("field")
}

#[test]
fn boundary_the_cap_declaration_covers_every_projector() {
    let cfg = Config::default();
    let probe = record(vec![("forced", Value::Bool(true))]);
    let Value::Object(all) = project("written", pairs(&probe), &cfg) else { panic!("object") };
    let mut emitted: Vec<&str> = all.iter().map(|(k, _)| k.as_str()).collect();
    emitted.push("target"); // `written` does not carry it; `gantt` does
    let mut declared: Vec<&str> = CAPPED.iter().map(|c| c.0).chain(UNCAPPED.iter().copied()).collect();
    emitted.sort();
    declared.sort();
    assert_eq!(emitted, declared, "every projected field is declared capped (with its cap) or uncapped");
}

#[test]
fn boundary_kill_every_capped_field_is_actually_bounded() {
    for (field, cap) in CAPPED {
        let raw = format!("{}{}", if *field == "id" { "pc-" } else { "" }, "X".repeat(cap * 4));
        let got = projected(command_for(field), &record(vec![(field, s(&raw))]), field);
        let got = got.as_str().expect("text");
        assert_eq!(len(got), *cap, "{field}");
        assert_ne!(got, raw);
    }
}

#[test]
fn boundary_control_a_value_at_the_cap_passes_through_untouched() {
    for (field, cap) in CAPPED {
        let raw = if *field == "id" { format!("pc-{}", "X".repeat(cap - 3)) } else { "X".repeat(*cap) };
        let got = projected(command_for(field), &record(vec![(field, s(&raw))]), field);
        assert_eq!(got.as_str(), Some(raw.as_str()), "{field}");
    }
}

#[test]
fn boundary_kill_edge_targets_are_bounded_too() {
    let over = format!("pc-{}", "X".repeat(ID_CAP * 4));
    let rec = record(vec![("id", s("pc-edge")), ("edges", obj(vec![("parent", s(&over)), ("blocks", Value::Array(vec![s(&over)]))]))]);
    let edges = projected("written", &rec, "edges");
    assert_eq!(len(edges.get("parent").and_then(Value::as_str).expect("parent")), ID_CAP);
    let Some(Value::Array(blocks)) = edges.get("blocks") else { panic!("blocks") };
    assert_eq!(len(blocks[0].as_str().expect("target")), ID_CAP);
}

#[test]
fn boundary_kill_finding_messages_are_bounded() {
    assert_eq!(len(&error(Code::E003, Some("pc-x"), &"Y".repeat(MESSAGE_CAP * 4)).message), MESSAGE_CAP);
}

#[test]
fn boundary_kill_audit_values_are_bounded() {
    assert_eq!(len(&safe_text(&"Z".repeat(AUDIT_VALUE_CAP * 4), AUDIT_VALUE_CAP)), AUDIT_VALUE_CAP);
}

#[test]
fn boundary_prose_fields_appear_in_no_allow_list() {
    let cfg = Config::default();
    let full = record(vec![("body", s("B")), ("disposition", s("D")), ("evidence", s("E")), ("labels", strs(&["L"]))]);
    for c in COMMANDS {
        let Value::Object(fields) = project(c, pairs(&full), &cfg) else { panic!("object") };
        for (k, _) in &fields {
            assert!(!PROSE_FIELDS.contains(&k.as_str()), "{c} projects the prose field {k}");
        }
    }
}

#[test]
fn caps_every_cap_clears_the_largest_legitimate_value() {
    // (cap, the largest legitimate value it must carry, why), with 4x headroom:
    // a cap sized near real data truncates real data, and truncated real data
    // reads like an attack.
    for (cap, legit, why) in [
        (TITLE_CAP, 256, "GitHub issue title"), (OWNER_CAP, 254, "an email-shaped identity"),
        (ID_CAP, 64, "pc- + an adapter-derived id"), (VOCAB_CAP, 32, "a declared extra_type"),
        (DATE_CAP, 32, "RFC 3339 with fraction and offset"), (MESSAGE_CAP, 512, "an E004 naming a long cycle"),
        (AUDIT_VALUE_CAP, 512, "possible-duplicates joining many ids"),
    ] {
        assert!(cap >= legit * 4, "{cap} vs {legit} ({why})");
    }
}

#[test]
fn caps_an_iso_8601_timestamp_survives_the_output_boundary() {
    for stamp in ["2026-08-07T14:30:00Z", "2026-08-07T14:30:00.123456+05:30"] {
        assert_eq!(safe_text(stamp, DATE_CAP), stamp);
        let rec = record(vec![("id", s("pc-iso1")), ("created", s(stamp))]);
        assert_eq!(projected("written", &rec, "created").as_str(), Some(stamp));
    }
}

#[test]
fn caps_a_title_at_the_largest_external_source_width_is_untouched() {
    let title = format!("T{}!", "i".repeat(254));
    assert_eq!(len(&title), 256);
    assert_eq!(safe_text(&title, TITLE_CAP), title);
}

// --- OutputForm ------------------------------------------------------------

#[test]
fn evidence_kind_is_a_classification_not_a_single_bit() {
    let cfg = Config::default();
    let mut kinds = std::collections::BTreeSet::new();
    for ev in [Value::Null, s("unknown"), s("true"), s("not a command at all"), s("fixture:abc"), s("claims:abc"), Value::Int(7)] {
        kinds.insert(evidence_kind(pairs(&obj(vec![("evidence", ev)])), &cfg));
    }
    assert!(kinds.len() >= 6, "{kinds:?}");
    assert!(kinds.contains("reference:fixture"), "{kinds:?}");
}

#[test]
fn title_volume_is_bounded() {
    let rec = record(vec![("title", s(&"W".repeat(TITLE_CAP * 3)))]);
    assert!(len(projected("next", &rec, "title").as_str().expect("title")) <= TITLE_CAP);
}

// --- TouchedIsTypeStrict --------------------------------------------------

#[test]
fn touched_one_and_true_are_different_values() {
    let a = record(vec![("x", Value::Int(1))]);
    let b = record(vec![("rev", Value::Int(2)), ("x", Value::Bool(true))]);
    assert_eq!(pecia_core::check::diff_fields(Some(pairs(&a)), pairs(&b)), ["x"]);
}

#[test]
fn touched_key_order_is_not_a_change() {
    // Unreachable through a canonical log, whose objects are key-sorted, but
    // `Value`'s derived equality is order-sensitive, so it is pinned here.
    let a = record(vec![("x", obj(vec![("a", Value::Int(1)), ("b", Value::Int(2))]))]);
    let b = record(vec![("rev", Value::Int(2)), ("x", obj(vec![("b", Value::Int(2)), ("a", Value::Int(1))]))]);
    assert!(pecia_core::check::diff_fields(Some(pairs(&a)), pairs(&b)).is_empty());
}
