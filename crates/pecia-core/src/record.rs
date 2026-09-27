//! The record contract (E001) and the shape predicates the cross-record checks
//! rely on. Every function here is a pure reading of a parsed value.
//!
//! A record reaching this module has already passed the parser, so it is inside
//! the v3.0 canonical domain by construction; the reference implementation
//! re-checks the domain here because its write path hands in-memory candidates
//! that never went through a parse. That half arrives with the write path.

use crate::config::py_strip;
use crate::finding::{error, Code, Finding};
use crate::text::{capped_seq, capped_value, safe_text, MESSAGE_CAP, MESSAGE_ITEM_CAP, MESSAGE_VALUE_CAP};
use crate::value::{Value, NESTING_BOUND};

pub const SCALAR_EDGES: &[&str] =
    &["parent", "duplicate_of", "discovered_from", "caused_by", "validates", "supersedes"];
pub const LIST_EDGES: &[&str] = &["blocks", "retires"];
pub const EDGE_KEYS: &[&str] = &[
    "blocks", "retires", "parent", "duplicate_of", "discovered_from", "caused_by", "validates",
    "supersedes",
];
pub const REQUIRED_FIELDS: &[&str] = &[
    "id", "rev", "type", "title", "status", "priority", "created", "updated", "edges",
    "disposition", "evidence", "owner", "labels", "body",
];

pub type Pairs = [(String, Value)];

pub fn get<'a>(obj: &'a Pairs, key: &str) -> Option<&'a Value> {
    obj.iter().find(|(k, _)| k == key).map(|(_, v)| v)
}

pub fn has(obj: &Pairs, key: &str) -> bool {
    obj.iter().any(|(k, _)| k == key)
}

pub fn as_obj(v: &Value) -> Option<&Pairs> {
    match v {
        Value::Object(pairs) => Some(pairs),
        _ => None,
    }
}

pub fn get_str<'a>(obj: &'a Pairs, key: &str) -> Option<&'a str> {
    get(obj, key).and_then(Value::as_str)
}

/// An integer that is not a boolean. The distinction is structural here —
/// `Value::Bool` and `Value::Int` are different variants — where the reference
/// implementation had to exclude Python's `bool` subclass by hand.
pub fn strict_int(v: Option<&Value>) -> Option<i64> {
    match v {
        Some(Value::Int(n)) => Some(*n),
        _ => None,
    }
}

/// Python truthiness, which the contract's `any(edges.get(k))` relies on.
pub fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Int(n)) => *n != 0,
        Some(Value::Str(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn is_list_of_str(v: &Value) -> bool {
    matches!(v, Value::Array(items) if items.iter().all(|x| matches!(x, Value::Str(_))))
}

/// `^pc-[A-Za-z0-9][A-Za-z0-9.-]*$`
pub fn is_record_id(s: &str) -> bool {
    let Some(rest) = s.strip_prefix("pc-") else { return false };
    let mut chars = rest.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
}

/// `YYYY-MM-DD`, optionally `THH:MM[:SS[.f{1,9}]]` and then an optional
/// `Z`/`±HH:MM` — the offset only alongside a time. ASCII digits only: the
/// reference's `\d` matched every Unicode digit until the port read it
/// (pc-f8be771f39e2).
pub fn is_date(s: &str) -> bool {
    let b = s.as_bytes();
    let digits = |from: usize, n: usize| b.len() >= from + n && b[from..from + n].iter().all(u8::is_ascii_digit);
    if !(digits(0, 4) && b.get(4) == Some(&b'-') && digits(5, 2) && b.get(7) == Some(&b'-') && digits(8, 2)) {
        return false;
    }
    let mut i = 10;
    if i == b.len() {
        return true;
    }
    if b[i] != b'T' || !(digits(i + 1, 2) && b.get(i + 3) == Some(&b':') && digits(i + 4, 2)) {
        return false;
    }
    i += 6;
    if b.get(i) == Some(&b':') {
        if !digits(i + 1, 2) {
            return false;
        }
        i += 3;
        if b.get(i) == Some(&b'.') {
            let start = i + 1;
            let mut j = start;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            if !(1..=9).contains(&(j - start)) {
                return false;
            }
            i = j;
        }
    }
    match b.get(i) {
        None => true,
        Some(b'Z') => i + 1 == b.len(),
        Some(b'+' | b'-') => {
            digits(i + 1, 2) && b.get(i + 3) == Some(&b':') && digits(i + 4, 2) && i + 6 == b.len()
        }
        _ => false,
    }
}

/// `^([a-z][a-z0-9_-]*):([A-Za-z0-9._/#-]+)$` — a foreign reference, as
/// (scheme, target).
pub fn parse_foreign_ref(v: Option<&Value>) -> Option<(&str, &str)> {
    let s = v?.as_str()?;
    let (scheme, target) = s.split_once(':')?;
    let mut sc = scheme.chars();
    let scheme_ok = matches!(sc.next(), Some(c) if c.is_ascii_lowercase())
        && sc.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-');
    let target_ok = !target.is_empty()
        && target.chars().all(|c| c.is_ascii_alphanumeric() || "._/#-".contains(c));
    (scheme_ok && target_ok).then_some((scheme, target))
}

fn is_anchor(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Nesting depth of a value: containers count a level, scalars do not.
pub fn nesting_depth(v: &Value) -> usize {
    let mut max = 0;
    let mut stack = vec![(v, 1usize)];
    while let Some((v, d)) = stack.pop() {
        match v {
            Value::Object(pairs) => {
                max = max.max(d);
                stack.extend(pairs.iter().map(|(_, x)| (x, d + 1)));
            }
            Value::Array(items) => {
                max = max.max(d);
                stack.extend(items.iter().map(|x| (x, d + 1)));
            }
            _ => {}
        }
    }
    max
}

/// The shape the cross-record checks index on. A record failing this is
/// reported by E001 and skipped by the checks that would otherwise have to
/// guess at its fields.
pub fn record_is_sound(rec: &Value) -> bool {
    let Some(obj) = as_obj(rec) else { return false };
    if get_str(obj, "id").is_none() || strict_int(get(obj, "rev")).is_none() {
        return false;
    }
    if get_str(obj, "type").is_none() || get_str(obj, "status").is_none() || get_str(obj, "title").is_none() {
        return false;
    }
    let Some(edges) = get(obj, "edges").and_then(as_obj) else { return false };
    for key in LIST_EDGES {
        match get(edges, key) {
            None => {}
            Some(v) if is_list_of_str(v) => {}
            _ => return false,
        }
    }
    for key in SCALAR_EDGES {
        match get(edges, key) {
            None | Some(Value::Null) | Some(Value::Str(_)) => {}
            _ => return false,
        }
    }
    for field in ["disposition", "evidence"] {
        match get(obj, field) {
            None | Some(Value::Null) | Some(Value::Str(_)) => {}
            _ => return false,
        }
    }
    true
}

/// The full E001 field contract. One finding per defect, in the reference's
/// order, because a finding's position is something readers — and tests — use.
pub fn validate_record(rec: &Pairs, statuses: &[String], types: &[String], line: Option<usize>) -> Vec<Finding> {
    let mut out = Vec::new();
    let rid = get_str(rec, "id");
    let rev = get(rec, "rev");
    let rev_tag = safe_text(
        &match (strict_int(rev), rev, line) {
            (Some(n), _, _) => format!("[rev {n}]"),
            (None, Some(v), Some(l)) => format!("[rev {} — malformed, line {l}]", capped_value(v, MESSAGE_VALUE_CAP)),
            (None, Some(v), None) => format!("[rev {} — malformed]", capped_value(v, MESSAGE_VALUE_CAP)),
            (None, None, Some(l)) => format!("[rev absent — line {l}]"),
            (None, None, None) => String::new(),
        },
        0,
    );
    // Builds the finding rather than pushing it, so `out` stays free to return.
    let tag = |message: String| -> Finding {
        if rev_tag.is_empty() {
            return error(Code::E001, rid, &message);
        }
        let budget = MESSAGE_CAP.saturating_sub(rev_tag.chars().count() + 1);
        let body = safe_text(&message, budget);
        error(Code::E001, rid, &format!("{body} {rev_tag}"))
    };
    macro_rules! bad {
        ($m:expr) => {
            out.push(tag($m))
        };
    }

    let missing: Vec<&str> = REQUIRED_FIELDS.iter().copied().filter(|f| !has(rec, f)).collect();
    if !missing.is_empty() {
        bad!(format!("missing fields: {}", missing.join(", ")));
        return out;
    }
    // The record is an object: one level, over its deepest value.
    let depth = 1 + rec.iter().map(|(_, v)| nesting_depth(v)).max().unwrap_or(0);
    if depth > NESTING_BOUND - 1 {
        bad!(format!(
            "record nests {depth} levels deep — the declared bound is {NESTING_BOUND} for a stored line, so a record keeps to {} (v2.12, pc-2e2f)",
            NESTING_BOUND - 1
        ));
    }
    let mut dotted: Vec<String> = rec.iter().map(|(k, _)| k.clone()).filter(|k| k.contains('.')).collect();
    dotted.sort();
    if !dotted.is_empty() {
        bad!(format!(
            "top-level key(s) {} contain '.' — dotted names collide with the touched grammar's edges.* paths and the record contract names none (v2.7)",
            capped_seq(&dotted, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ")
        ));
    }
    if rec.iter().any(|(k, _)| k.is_empty()) {
        bad!("top-level key \"\" is empty — record.schema.json's propertyNames (^[^.]+$) requires every field name non-empty and dot-free, and a name the diff machinery cannot address is not a legal field name (v2.8)".into());
    }
    if !rid.is_some_and(is_record_id) {
        bad!("id must be a pc-prefixed token".into());
    }
    if !strict_int(rev).is_some_and(|n| n >= 1) {
        bad!("rev must be an integer >= 1 (booleans are not revisions)".into());
    }
    match get(rec, "type") {
        Some(Value::Str(t)) if !types.iter().any(|x| x == t) => {
            bad!(format!("unknown type: {}", capped_value(&Value::Str(t.clone()), MESSAGE_VALUE_CAP)))
        }
        Some(Value::Str(_)) => {}
        _ => bad!("type must be a string".into()),
    }
    if !get_str(rec, "title").is_some_and(|t| !py_strip(t).is_empty()) {
        bad!("title must be a non-empty string".into());
    }
    match get(rec, "status") {
        Some(Value::Str(s)) if s == "blocked" || s == "ready" => bad!("blocked/ready are computed, never stored".into()),
        Some(Value::Str(s)) if !statuses.iter().any(|x| x == s) => {
            bad!(format!("unknown status: {}", capped_value(&Value::Str(s.clone()), MESSAGE_VALUE_CAP)))
        }
        Some(Value::Str(_)) => {}
        _ => bad!("status must be a string".into()),
    }
    if !strict_int(get(rec, "priority")).is_some_and(|p| (0..=4).contains(&p)) {
        bad!("priority must be an integer 0-4 (booleans are not priorities)".into());
    }
    for field in ["created", "updated"] {
        if !get_str(rec, field).is_some_and(is_date) {
            bad!(format!("{field} must be a YYYY-MM-DD string"));
        }
    }
    if has(rec, "target") && !get_str(rec, "target").is_some_and(is_date) {
        bad!("target, when present, must be a YYYY-MM-DD string".into());
    }
    for (field, msg) in [("disposition", "disposition must be null or a string"), ("evidence", "evidence must be null or a string")] {
        if !matches!(get(rec, field), Some(Value::Null) | Some(Value::Str(_))) {
            bad!(msg.into());
        }
    }
    if !get_str(rec, "owner").is_some_and(|o| !py_strip(o).is_empty()) {
        bad!("owner must be a non-empty string".into());
    }
    if !get(rec, "labels").is_some_and(is_list_of_str) {
        bad!("labels must be a list of strings".into());
    }
    if get_str(rec, "body").is_none() {
        bad!("body must be a string".into());
    }
    if has(rec, "forced") && get(rec, "forced") != Some(&Value::Bool(true)) {
        bad!("forced, when present, must be true (the brand is presence, not a toggle)".into());
    }
    if has(rec, "anchor") && !get_str(rec, "anchor").is_some_and(is_anchor) {
        bad!("anchor, when present, must be a 40-hex commit id (stamped by the write path, never authored — v2.5)".into());
    }
    if has(rec, "anchor_dirty") {
        if get(rec, "anchor_dirty") != Some(&Value::Bool(true)) {
            bad!("anchor_dirty, when present, must be true (presence, not a toggle)".into());
        }
        if !has(rec, "anchor") {
            bad!("anchor_dirty without anchor — dirtiness qualifies an anchor".into());
        }
    }
    if has(rec, "ratified_by") != has(rec, "ratified") {
        bad!("ratified_by and ratified travel together (v2.5): a ratifying revision sets who and when in one act".into());
    }
    if has(rec, "ratified_by") {
        if !get_str(rec, "ratified_by").is_some_and(|r| !py_strip(r).is_empty()) {
            bad!("ratified_by must be a non-empty string".into());
        }
        if get_str(rec, "type") != Some("decision") {
            bad!("ratification is for decision records (v2.5) — other types have evidence, which is stronger".into());
        }
    }
    if has(rec, "ratified") && !get_str(rec, "ratified").is_some_and(is_date) {
        bad!("ratified must be a YYYY-MM-DD string".into());
    }
    if has(rec, "context") && parse_foreign_ref(get(rec, "context")).is_none() {
        bad!("context, when present, must be a <scheme>:<target> reference (v2.5, e.g. doc:docs/orientation.md#anchor)".into());
    }

    let Some(edges) = get(rec, "edges").and_then(as_obj) else {
        bad!("edges must be an object".into());
        return out;
    };
    for key in LIST_EDGES {
        match get(edges, key) {
            None => {}
            Some(v) if !is_list_of_str(v) => bad!(format!("edges.{key} must be a list of strings")),
            Some(Value::Array(items)) => {
                if items.iter().any(|x| x.as_str().is_some_and(|s| py_strip(s).is_empty())) {
                    bad!(format!("edges.{key} contains an empty target — every element must name a record (v2.7)"));
                }
            }
            Some(_) => {}
        }
    }
    for (key, _) in edges {
        if !EDGE_KEYS.contains(&key.as_str()) && key != "no_edges" {
            bad!(format!("unknown edge key: {}", safe_text(key, MESSAGE_ITEM_CAP)));
        }
    }
    if has(edges, "no_edges") && get(edges, "no_edges") != Some(&Value::Bool(true)) {
        bad!("edges.no_edges, when present, must be true (a declaration, not a toggle)".into());
    }
    if get(edges, "no_edges") == Some(&Value::Bool(true)) && EDGE_KEYS.iter().any(|k| truthy(get(edges, k))) {
        let mut held: Vec<&str> = EDGE_KEYS.iter().copied().filter(|k| truthy(get(edges, k))).collect();
        held.sort();
        bad!(format!(
            "edges.no_edges beside a real edge ({}) — no_edges declares the absence of the others (v2.6); drop the declaration or the edge",
            held.join(", ")
        ));
    }
    for key in SCALAR_EDGES {
        match get(edges, key) {
            None | Some(Value::Null) => {}
            Some(Value::Str(s)) if py_strip(s).is_empty() => bad!(format!(
                "edges.{key} is an empty string — a scalar edge is null or names a record; the empty string names nothing (v2.7)"
            )),
            Some(Value::Str(_)) => {}
            Some(_) => bad!(format!("edges.{key} must be null or an id string")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates_are_the_reference_grammar_in_ascii_digits() {
        for ok in ["2026-09-22", "2026-09-22T10:00", "2026-09-22T10:00:05", "2026-09-22T10:00:05.123456789Z", "2026-09-22T10:00+02:00"] {
            assert!(is_date(ok), "{ok}");
        }
        for bad in ["2026-9-22", "2026-09-22Z", "2026-09-22T10:00:05.", "2026-09-22T10:00:05.1234567890", "\u{662}\u{660}\u{662}\u{666}-09-22", "2026-09-22T10"] {
            assert!(!is_date(bad), "{bad}");
        }
    }

    #[test]
    fn ids_and_references() {
        assert!(is_record_id("pc-a3f8") && is_record_id("pc-cm-f.T1"));
        assert!(!is_record_id("pc-") && !is_record_id("pc-.x") && !is_record_id("pc-\u{e9}"));
        let r = Value::Str("claims:format-spec".into());
        assert_eq!(parse_foreign_ref(Some(&r)), Some(("claims", "format-spec")));
        assert!(parse_foreign_ref(Some(&Value::Str("Claims:x".into()))).is_none());
    }
}
