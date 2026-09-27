//! The pure half of `sync`: re-chaining a local-only suffix onto a published
//! timeline. Two appends revising DISJOINT fields of one record commute and
//! both survive; two revising the SAME field do not, and the loser is
//! surfaced — never auto-rebased, because replaying an intent onto changed
//! state is a lost update (format-v2.md 4.1).

use crate::check::diff_fields;
use crate::record::{as_obj, get, get_str, strict_int, Pairs};
use crate::value::Value;
use crate::write::{remove, set};
use std::collections::{BTreeMap, BTreeSet};

pub const PRESENCE_FIELDS: &[&str] = &["target", "context", "ratified_by", "ratified"];

/// The fields two touched sets conflict on. `edges.no_edges` declares the
/// ABSENCE of every other edge, so it conflicts with any other `edges.*` on
/// the other side, not only with itself (pc-7f4e).
pub fn touched_conflicts(a: &BTreeSet<String>, b: &BTreeSet<String>) -> BTreeSet<String> {
    let mut overlap: BTreeSet<String> = a.intersection(b).cloned().collect();
    let other = |s: &BTreeSet<String>| -> BTreeSet<String> {
        s.iter().filter(|f| f.starts_with("edges.") && *f != "edges.no_edges").cloned().collect()
    };
    let (oa, ob) = (other(a), other(b));
    if a.contains("edges.no_edges") && !ob.is_empty() {
        overlap.insert("edges.no_edges".into());
        overlap.extend(ob.iter().cloned());
    }
    if b.contains("edges.no_edges") && !oa.is_empty() {
        overlap.insert("edges.no_edges".into());
        overlap.extend(oa.iter().cloned());
    }
    overlap
}

/// A field as the touched grammar addresses it, absent and null collapsed.
fn field_get<'a>(rec: &'a Pairs, path: &str) -> Option<&'a Value> {
    let v = match path.split_once('.') {
        None => get(rec, path),
        Some((outer, inner)) => get(rec, outer).and_then(as_obj).and_then(|o| get(o, inner)),
    };
    v.filter(|v| **v != Value::Null)
}

/// Write one addressed field. A presence field's cleared state is ABSENCE, not
/// null (pc-76b5), and so is `no_edges`'.
fn field_set(rec: &mut Vec<(String, Value)>, path: &str, value: Option<&Value>) {
    match path.split_once('.') {
        None => match value {
            None if PRESENCE_FIELDS.contains(&path) => remove(rec, path),
            None => set(rec, path, Value::Null),
            Some(v) => set(rec, path, v.clone()),
        },
        Some((outer, inner)) => {
            if !matches!(get(rec, outer), Some(Value::Object(_))) {
                set(rec, outer, Value::Object(Vec::new()));
            }
            let slot = rec.iter_mut().find(|(k, _)| k == outer).map(|(_, v)| v).expect("outer");
            let Value::Object(sub) = slot else { unreachable!() };
            match value {
                None if inner == "no_edges" => remove(sub, inner),
                None => set(sub, inner, Value::Null),
                Some(v) => set(sub, inner, v.clone()),
            }
        }
    }
}

/// Carry one touched field from `src` into `dst`.
pub fn transfer_field(dst: &mut Vec<(String, Value)>, src: &Pairs, path: &str) {
    if path.contains('.') {
        field_set(dst, path, field_get(src, path));
    } else if let Some(v) = get(src, path) {
        set(dst, path, v.clone());
    } else {
        remove(dst, path);
    }
}

fn rec_of(e: &Value) -> &Pairs {
    e.get("rec").and_then(as_obj).unwrap_or(&[])
}

/// The touched set entry `upto` of `entries` would carry, derived against the
/// highest revision of its id before it.
pub fn derived_touched(entries: &[Value], upto: usize) -> BTreeSet<String> {
    let rec = rec_of(&entries[upto]);
    let rid = get(rec, "id");
    let mut before: Option<&Pairs> = None;
    for prior in &entries[..upto] {
        let r = rec_of(prior);
        if get(r, "id") == rid {
            let rev = |x: &Pairs| strict_int(get(x, "rev")).unwrap_or(0);
            if before.is_none_or(|b| rev(r) > rev(b)) {
                before = Some(r);
            }
        }
    }
    diff_fields(before, rec).into_iter().collect()
}

/// (id, rev) -> canonical content, for every well-typed revision.
pub fn published_revisions(entries: &[Value]) -> BTreeMap<(String, i64), String> {
    let mut out = BTreeMap::new();
    for e in entries {
        let r = rec_of(e);
        if let (Some(id), Some(rev)) = (get_str(r, "id"), strict_int(get(r, "rev"))) {
            out.insert((id.to_string(), rev), crate::canonical::canonical_object(r));
        }
    }
    out
}

pub fn published_heads(entries: &[Value]) -> BTreeMap<String, i64> {
    let mut out: BTreeMap<String, i64> = BTreeMap::new();
    for e in entries {
        let r = rec_of(e);
        if let (Some(id), Some(rev)) = (get_str(r, "id"), strict_int(get(r, "rev"))) {
            let slot = out.entry(id.to_string()).or_insert(0);
            if rev > *slot {
                *slot = rev;
            }
        }
    }
    out
}

/// The first revision the register publishes that the timeline about to be
/// written would drop — neither arriving with the same content nor carried
/// locally — as (id, rev, how it is lost). Moving the register there would
/// UNPUBLISH a landed revision (pc-1bb6).
pub fn unpublished_by(held: &[Value], arriving: &[Value], local: &[Value]) -> Option<(String, i64, String)> {
    let held = published_revisions(held);
    let arr = published_revisions(arriving);
    let loc = published_revisions(local);
    let heads = published_heads(arriving);
    for ((rid, rev), content) in &held {
        let key = (rid.clone(), *rev);
        if arr.get(&key) == Some(content) || loc.get(&key) == Some(content) {
            continue;
        }
        let lost = match heads.get(rid) {
            None => format!("carries no {rid}"),
            Some(have) if !arr.contains_key(&key) => format!("stops at {rid} rev {have}"),
            Some(_) => format!("carries a DIFFERENT {rid} rev {rev} — same number, other content, and this store has never held the published one"),
        };
        return Some((rid.clone(), *rev, lost));
    }
    None
}
