//! The read-only queries: pure functions over the resolved head set.

use crate::record::{get, get_str, strict_int, Pairs, LIST_EDGES, SCALAR_EDGES};
use crate::text::{digest8, safe_id, safe_text};
use crate::write::is_terminal;
use crate::value::Value;
use std::collections::{BTreeSet, HashMap, HashSet};

pub type Heads<'a> = [(String, &'a Pairs)];

/// A sortable form of a date or timestamp: a time without seconds gains
/// `:00`, and seconds without a fraction gain `.000000000`, so a coarser
/// timestamp sorts BEFORE a finer one at the same instant (pc-749d) — as
/// plain strings they sorted backwards.
pub fn chronological_key(text: &str) -> String {
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len() + 13);
    let mut i = 0;
    while i < b.len() {
        let hhmm = b[i] == b'T'
            && b.len() >= i + 6
            && b[i + 1].is_ascii_digit() && b[i + 2].is_ascii_digit() && b[i + 3] == b':'
            && b[i + 4].is_ascii_digit() && b[i + 5].is_ascii_digit();
        if hhmm {
            out.push_str(&text[i..i + 6]);
            i += 6;
            let secs = b.len() >= i + 3 && b[i] == b':' && b[i + 1].is_ascii_digit() && b[i + 2].is_ascii_digit();
            if secs {
                out.push_str(&text[i..i + 3]);
                i += 3;
                if b.get(i) != Some(&b'.') {
                    out.push_str(".000000000");
                }
            } else if b.get(i) != Some(&b':') {
                out.push_str(":00.000000000");
            }
            continue;
        }
        let ch = text[i..].chars().next().expect("char");
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

/// id -> the non-terminal records holding it back: a `blocks` or `retires`
/// holder, or an open child blocking its parent's completion. Sorted, each
/// holder once. Only records something holds back have an entry — the
/// others are unblocked, and cost nothing.
pub fn compute_blockers(heads: &Heads) -> HashMap<String, Vec<String>> {
    let ids: HashSet<&str> = heads.iter().map(|(id, _)| id.as_str()).collect();
    let mut blocked: HashMap<&str, BTreeSet<&str>> = HashMap::new();
    for (rid, head) in heads {
        if is_terminal(get_str(head, "status")) {
            continue;
        }
        let edges = get(head, "edges").and_then(crate::record::as_obj).unwrap_or(&[]);
        let lists = LIST_EDGES.iter().filter_map(|k| match get(edges, k) {
            Some(Value::Array(items)) => Some(items.iter().filter_map(Value::as_str)),
            _ => None,
        });
        for t in lists.flatten().chain(get_str(edges, "parent")) {
            if let Some(&t) = ids.get(t) {
                blocked.entry(t).or_default().insert(rid.as_str());
            }
        }
    }
    blocked.into_iter().map(|(k, v)| (k.to_string(), v.into_iter().map(str::to_string).collect())).collect()
}

/// A milestone whose target day has passed while it is still open. Overdue is
/// computed, never a stored status (rule 2).
pub fn milestone_is_overdue(h: &Pairs, cutoff_today: &str) -> bool {
    get_str(h, "type") == Some("milestone")
        && !is_terminal(get_str(h, "status"))
        && get_str(h, "target").is_some_and(|t| !t.is_empty() && t < cutoff_today)
}

/// A head's priority as the total order ranks it: absent is 2, and a value
/// that is not an integer sorts after every one that is.
pub fn priority(h: &Pairs) -> i64 {
    match get(h, "priority") {
        None => 2,
        Some(v) => strict_int(Some(v)).unwrap_or(i64::MAX),
    }
}

/// Open, unblocked heads in the total order: priority, then creation time,
/// then id.
pub fn ready<'a>(heads: &'a Heads<'a>, max_priority: Option<i64>) -> Vec<&'a Pairs> {
    let blocked = compute_blockers(heads);
    let mut out: Vec<(&String, &Pairs)> = heads
        .iter()
        .filter(|(id, h)| get_str(h, "status") == Some("open") && blocked.get(id.as_str()).is_none_or(|b| b.is_empty()))
        .filter(|(_, h)| max_priority.is_none_or(|m| priority(h) <= m))
        .map(|(id, h)| (id, *h))
        .collect();
    // Each key once, not once per comparison: chronological_key allocates.
    out.sort_by_cached_key(|(id, h)| (priority(h), chronological_key(get_str(h, "created").unwrap_or("")), *id));
    out.into_iter().map(|(_, h)| h).collect()
}

/// Characters Mermaid reads as syntax inside a label.
fn mermaid_syntax(c: char) -> bool {
    "\":,;[]{}|<>%".contains(c)
}

pub fn mermaid_label(value: &str, cap: usize) -> String {
    let text: String = safe_text(value, cap).chars().map(|c| if mermaid_syntax(c) { ' ' } else { c }).collect();
    let joined = text.split(crate::text::is_space).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ");
    if joined.is_empty() { "-".into() } else { joined }
}

/// An id as a Mermaid IDENTIFIER: dashes to underscores, and anything else
/// outside [A-Za-z0-9_] replaced, with a digest so two ids never collapse.
pub fn mermaid_id(value: &str) -> String {
    let text = safe_id(value);
    let node = text.replace('-', "_");
    let node = if node.chars().any(|c| !(c.is_ascii_alphanumeric() || c == '_')) {
        let cleaned: String = node.chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' { c } else { '_' }).collect();
        format!("{cleaned}_{}", digest8(&text))
    } else {
        node
    };
    if node.is_empty() { "_".into() } else { node }
}

pub fn mermaid_safe_id_display(value: &str) -> String {
    safe_id(value).chars().map(|c| if mermaid_syntax(c) { ' ' } else { c }).collect()
}

pub fn edge_targets(head: &Pairs) -> Vec<(&'static str, String)> {
    let edges = get(head, "edges").and_then(crate::record::as_obj).unwrap_or(&[]);
    let mut out = Vec::new();
    for key in LIST_EDGES {
        if let Some(Value::Array(items)) = get(edges, key) {
            out.extend(items.iter().filter_map(Value::as_str).map(|t| (*key, safe_id(t))));
        }
    }
    for key in SCALAR_EDGES {
        if let Some(t) = get_str(edges, key).filter(|t| !t.is_empty()) {
            out.push((*key, safe_id(t)));
        }
    }
    out
}
