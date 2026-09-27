//! `audit`: the advisory anti-rot surface. Findings never block; they surface
//! frontier-vs-rot — an untriaged record is either the edge of the work or its
//! decay, and only a person can tell which.
//!
//! Pure, like the rest of this crate: what the audit needs from the world —
//! running a declared resolver, reading the test tree, looking a command up —
//! comes through `AuditEnv`, and "today" is an argument.

use crate::config::{Config, ConfigValue};
use crate::dates::format_day;
use crate::query::{compute_blockers, milestone_is_overdue, Heads};
use crate::record::{as_obj, get, get_str, parse_foreign_ref, strict_int, truthy, Pairs, EDGE_KEYS, LIST_EDGES, SCALAR_EDGES};
use crate::text::{casefold, is_word_char, render_value, safe_id, safe_text, word_count, MESSAGE_CAP};
use crate::config::py_strip;
use crate::value::Value;
use crate::write::{evidence_command_head, evidence_is_structural, evidence_kind, is_terminal};
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// Cap on one field value inside a finding.
pub const AUDIT_VALUE_CAP: usize = 2048;

/// The day the `retires` edge landed (v1.13). A record closed before it could
/// not have used the edge, so its prose-only linkage is counted, never listed
/// (D12).
pub const RETIRES_LANDED: &str = "2026-08-11";

/// Each finding kind: the fields it carries, and the note they fill. A field
/// not declared here cannot reach the output.
const KINDS: &[(&str, &[&str], &str)] = &[
    ("untriaged", &[], "no edges and no no_edges declaration — frontier or rot?"),
    ("prose-only-linkage", &["ids", "unresolved"], "disposition names {ids} with no edge to them — the linkage is prose and nothing computes it. Which edge is the record's own question (`retires` if this work closed them, else discovered_from / supersedes / blocks). Further pc--shaped tokens naming no record: {unresolved}"),
    ("stale-in-progress", &["since"], "in-progress but untouched since {since}"),
    ("aging-unknown-evidence", &[], "defect with unknown evidence beyond rot horizon"),
    ("priority-rot", &["priority", "created"], "p{priority} open since {created}"),
    ("closed-while-blocked", &["blocker"], "terminal but {blocker} (non-terminal) still blocks it"),
    ("inadequate-disposition", &["disposition_chars", "disposition_words", "min_chars", "disposition"], "disposition is {disposition_chars} chars / {disposition_words} words (min {min_chars}) — names an outcome, not what happened and why (spec: 'Fixed' alone fails audit)"),
    ("possible-duplicates", &["ids"], "same title on: {ids} (duplicate_of edge?)"),
    ("forced-write", &["rev", "owner", "updated"], "rev {rev} written with --force by {owner} on {updated}"),
    ("historical-custody-violation", &["rev", "violation"], "rev {rev}: {violation} when written"),
    ("unresolvable-reference", &["scheme", "target", "reason", "exit", "stderr"], "evidence reference {scheme}:{target} did not resolve — {reason}, exit {exit}"),
    ("reference-not-attempted", &["field", "scheme"], "{field} reference (scheme {scheme}) not attempted — board never executes resolvers; run `audit` to resolve it"),
    ("historical-prose-only-linkage", &["count", "cutoff"], "{count} record(s) closed before the retires edge existed ({cutoff}, v1.13) name records in their dispositions with no edge — counted, never listed (D12); `audit --historical` lists them"),
    ("unratified-decision", &["owner"], "a terminal decision with machine owner {owner} and no ratifying revision — until a human ratifies it (edit <id> --ratify), an agent-recorded decision reads like the human's own call (v2.5)"),
    ("unresolvable-context", &["scheme", "target", "reason", "exit", "stderr"], "context reference {scheme}:{target} did not resolve — {reason}, exit {exit}. The orientation document this record leans on is not where it says"),
    ("unresolvable-evidence", &["evidence_kind", "reason", "evidence"], "evidence command (kind {evidence_kind}) {reason}"),
    ("unverified-imperative", &["phrase", "reason"], "a terminal record's disposition says {phrase} about the implementation, and {reason}. Writing a fix down is not landing it — cite the test, or say why none"),
    ("truth-audit-sample", &["rev", "disposition_chars", "disposition_words", "evidence_kind", "disposition", "evidence"], "verify rev {rev}: disposition {disposition_chars} chars / {disposition_words} words, evidence {evidence_kind}"),
    ("overdue-milestone", &["target", "status"], "target {target} passed and status is still {status} (non-terminal) — overdue is computed, never a stored status (rule 2)"),
];

/// What the audit asks of the world.
pub trait AuditEnv {
    /// Run a declared resolver on a target: (resolved, reason, exit code, its
    /// first non-empty stderr line).
    fn resolve(&self, command: &str, target: &str) -> (bool, &'static str, Option<i64>, String);
    /// Whether any test file names this record id.
    fn cited_in_tests(&self, rid: &str) -> bool;
    /// Whether an evidence command resolves here: on PATH, or a path in the tree.
    fn command_resolves(&self, token: &str) -> bool;
}

pub struct AuditOptions {
    /// Today, as days since 1970-01-01.
    pub today: i64,
    /// How many closed records to sample for a human truth audit; negative
    /// drops that many from the end, as the reference's slice does.
    pub sample: i64,
    /// Run declared resolvers (`audit`), or report them unattempted (`board`).
    pub resolve: bool,
    /// List prose-only linkage on records closed before `retires` existed.
    pub historical: bool,
}

/// One finding: its declared fields bounded, and the note they fill.
fn finding(kind: &str, rid: Option<&str>, fields: Vec<(&str, Value)>) -> Value {
    let (_, declared, template) = KINDS.iter().find(|k| k.0 == kind).expect("declared kind");
    let names: Vec<&str> = fields.iter().map(|f| f.0).collect();
    assert_eq!(names, *declared, "audit finding {kind} carries undeclared fields");
    let mut out: Vec<(String, Value)> = vec![
        ("kind".into(), Value::Str(kind.into())),
        ("id".into(), rid.map_or(Value::Null, |r| Value::Str(safe_id(r)))),
    ];
    for (key, value) in fields {
        let value = match value {
            v @ (Value::Int(_) | Value::Bool(_)) => v,
            Value::Str(s) => Value::Str(safe_text(&s, AUDIT_VALUE_CAP)),
            other => Value::Str(safe_text(&render_value(&other), AUDIT_VALUE_CAP)),
        };
        out.push((key.into(), value));
    }
    let note = fill(template, &out);
    out.push(("note".into(), Value::Str(safe_text(&note, MESSAGE_CAP))));
    Value::Object(out)
}

/// `{name}` placeholders filled in ONE pass, so a value that itself reads
/// `{since}` is text, not a second placeholder.
fn fill(template: &str, values: &[(String, Value)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let close = rest[open..].find('}').expect("closed placeholder") + open;
        let name = &rest[open + 1..close];
        match values.iter().find(|(k, _)| k == name).map(|(_, v)| v).expect("declared placeholder") {
            Value::Int(n) => out.push_str(&n.to_string()),
            Value::Bool(b) => out.push_str(if *b { "True" } else { "False" }),
            Value::Str(s) => out.push_str(s),
            other => out.push_str(&render_value(other)),
        }
        rest = &rest[close + 1..];
    }
    out.push_str(rest);
    out
}

fn raw(h: &Pairs, key: &str) -> Value {
    get(h, key).cloned().unwrap_or(Value::Null)
}

/// A timestamp field for a cutoff comparison. A missing or non-text value
/// compares as the empty string: earlier than every cutoff, so it is reported
/// rather than silently passed.
fn when<'a>(h: &'a Pairs, key: &str) -> &'a str {
    get_str(h, key).unwrap_or("")
}

/// An integer config key. A value that is not one is a refusal, not a silent
/// default: a mistyped threshold would otherwise switch a finding off.
pub fn cfg_int(cfg: &Config, key: &str, default: i64) -> Result<i64, String> {
    match cfg.get(key) {
        None => Ok(default),
        Some(ConfigValue::Int(n)) => Ok(*n),
        Some(ConfigValue::Str(s)) => py_strip(s).parse().map_err(|_| format!("config key {key} is not an integer: {}", safe_text(s, 128))),
        Some(ConfigValue::List(_)) => Err(format!("config key {key} is a list, not an integer")),
    }
}

fn cutoff(today: i64, days: i64, key: &str) -> Result<String, String> {
    today.checked_sub(days).and_then(format_day).ok_or_else(|| format!("config key {key} = {days} puts the audit cutoff outside years 1-9999"))
}

/// `\bpc-[A-Za-z0-9][A-Za-z0-9.-]*`, trailing `.` and `-` stripped: the ids a
/// disposition's prose names.
pub fn prose_ids(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut prev: Option<char> = None;
    let mut i = 0;
    while i < text.len() {
        let rest = &text[i..];
        if rest.starts_with("pc-") && prev.is_none_or(|p| !is_word_char(p)) && rest[3..].starts_with(|c: char| c.is_ascii_alphanumeric()) {
            let len = rest[3..].bytes().take_while(|b| b.is_ascii_alphanumeric() || *b == b'.' || *b == b'-').count();
            let end = i + 3 + len;
            out.push(text[i..end].trim_end_matches(['.', '-']));
            prev = text[..end].chars().next_back();
            i = end;
            continue;
        }
        let c = rest.chars().next().expect("char");
        prev = Some(c);
        i += c.len_utf8();
    }
    out
}

/// (known ids named, the distinct pc-shaped tokens naming no record), each sorted.
pub fn ids_named_in(text: Option<&Value>, known: &BTreeSet<&str>) -> (Vec<String>, Vec<String>) {
    let mut named = BTreeSet::new();
    let mut unknown = BTreeSet::new();
    for token in prose_ids(text.and_then(Value::as_str).unwrap_or("")) {
        if known.contains(token) { named.insert(token.to_string()); } else { unknown.insert(token); }
    }
    (named.into_iter().collect(), unknown.into_iter().map(str::to_string).collect())
}

const IMPERATIVES: &[&str] = &[
    "must route", "must call", "must check", "must re-run", "must rerun", "must verify", "must enforce",
    "must pass", "must use", "must go through", "must be",
    "has to route", "has to call", "has to check", "has to re-run", "has to rerun", "has to verify", "has to enforce",
    "the caller must", "callers must", "implementations must", "implementation must",
];

/// One text character against one pattern character (ASCII lowercase),
/// case-insensitively as the reference's Unicode regex matches. Exactly four
/// non-ASCII characters fold onto a pattern letter — `İ` and `ı` onto `i`,
/// `ſ` onto `s`, the Kelvin sign onto `k` — measured over every code point
/// against the reference's `re.IGNORECASE`, so everything else is ASCII
/// folding, without a Unicode case table.
fn ci_eq(c: char, p: char) -> bool {
    if c.is_ascii() {
        return c.to_ascii_lowercase() == p;
    }
    matches!((p, c), ('i', '\u{130}' | '\u{131}') | ('s', '\u{17f}') | ('k', '\u{212a}'))
}

/// The first implementation imperative a disposition states — "must call",
/// "callers must" — as a whole-word, case-insensitive match, lowercased.
pub fn imperative(text: &str) -> Option<String> {
    let mut prev: Option<char> = None;
    for (i, c) in text.char_indices() {
        // Only where a phrase can begin: a word start, on one of the letters
        // the phrases begin with.
        if prev.is_none_or(|p| !is_word_char(p)) && ['m', 'h', 't', 'c', 'i'].into_iter().any(|p| ci_eq(c, p)) {
            for phrase in IMPERATIVES {
                let mut chars = text[i..].char_indices();
                let matched = phrase.chars().all(|p| chars.next().is_some_and(|(_, c)| ci_eq(c, p)));
                if !matched {
                    continue;
                }
                let end = chars.next().map_or(text.len(), |(j, next)| if is_word_char(next) { usize::MAX } else { i + j });
                if end != usize::MAX {
                    return Some(text[i..end].to_lowercase());
                }
            }
        }
        prev = Some(c);
    }
    None
}

/// Every audit finding over the heads and the full revision history, in the
/// order the reference emits them.
pub fn audit_findings(heads: &Heads, records: &[&Value], cfg: &Config, opts: &AuditOptions, env: &dyn AuditEnv) -> Result<Vec<Value>, String> {
    let stale_days = cfg_int(cfg, "stale_days", 7)?;
    let rot_days = cfg_int(cfg, "rot_days", 14)?;
    let min_chars = cfg_int(cfg, "min_disposition_chars", 20)?;
    let cutoff_stale = cutoff(opts.today, stale_days, "stale_days")?;
    let cutoff_rot = cutoff(opts.today, rot_days, "rot_days")?;
    let cutoff_today = cutoff(opts.today, 0, "today")?;
    let mut out = Vec::new();
    let blocked_by = compute_blockers(heads);
    let known: BTreeSet<&str> = heads.iter().map(|(id, _)| id.as_str()).collect();
    let mut linkage: HashMap<String, BTreeSet<String>> = heads.iter().map(|(id, _)| (id.clone(), BTreeSet::new())).collect();
    for (rid, h) in heads {
        let edges = get(h, "edges").and_then(as_obj).unwrap_or(&[]);
        let mut targets: BTreeSet<&str> = BTreeSet::new();
        for k in LIST_EDGES {
            if let Some(Value::Array(items)) = get(edges, k) {
                targets.extend(items.iter().filter_map(Value::as_str));
            }
        }
        targets.extend(SCALAR_EDGES.iter().filter_map(|k| get_str(edges, k)));
        for t in targets {
            linkage.entry(rid.clone()).or_default().insert(t.to_string());
            linkage.entry(t.to_string()).or_default().insert(rid.clone());
        }
    }
    let mut first_terminal: HashMap<&str, &str> = HashMap::new();
    for rec in records.iter().filter_map(|r| as_obj(r)) {
        let (Some(id), Some(when)) = (get_str(rec, "id"), get_str(rec, "updated")) else { continue };
        if is_terminal(get_str(rec, "status")) && !when.is_empty() {
            let slot = first_terminal.entry(id).or_insert(when);
            if when < *slot {
                *slot = when;
            }
        }
    }
    let mut sorted: Vec<&(String, &Pairs)> = heads.iter().collect();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    let mut grandfathered = 0i64;
    let mut titles: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (rid, h) in &sorted {
        let (rid, h) = (rid.as_str(), *h);
        let edges = get(h, "edges").and_then(as_obj).unwrap_or(&[]);
        let (kind, status) = (get_str(h, "type"), get_str(h, "status"));
        let terminal = is_terminal(status);
        let has_edge = EDGE_KEYS.iter().any(|k| truthy(get(edges, k)));
        if matches!(kind, Some("task" | "defect")) && !terminal && !has_edge && !truthy(get(edges, "no_edges")) {
            out.push(finding("untriaged", Some(rid), vec![]));
        }
        if status == Some("in-progress") && when(h, "updated") < cutoff_stale.as_str() {
            out.push(finding("stale-in-progress", Some(rid), vec![("since", raw(h, "updated"))]));
        }
        if kind == Some("defect") && !terminal && get_str(h, "evidence") == Some("unknown") && when(h, "created") < cutoff_rot.as_str() {
            out.push(finding("aging-unknown-evidence", Some(rid), vec![]));
        }
        let priority = match get(h, "priority") {
            None => Some(2),
            Some(Value::Bool(b)) => Some(i64::from(*b)),
            v => strict_int(v),
        };
        if status == Some("open") && priority.is_some_and(|p| p <= 1) && when(h, "created") < cutoff_rot.as_str() {
            out.push(finding("priority-rot", Some(rid), vec![("priority", raw(h, "priority")), ("created", raw(h, "created"))]));
        }
        if milestone_is_overdue(h, &cutoff_today) {
            out.push(finding("overdue-milestone", Some(rid), vec![("target", raw(h, "target")), ("status", raw(h, "status"))]));
        }
        if kind == Some("decision") && terminal && get_str(h, "owner").is_some_and(|o| o.contains(':')) && !truthy(get(h, "ratified_by")) {
            out.push(finding("unratified-decision", Some(rid), vec![("owner", raw(h, "owner"))]));
        }
        if terminal {
            for blocker in blocked_by.get(rid).into_iter().flatten() {
                out.push(finding("closed-while-blocked", Some(rid), vec![("blocker", Value::Str(blocker.clone()))]));
            }
            if let Some(disp) = get_str(h, "disposition") {
                let stripped = py_strip(disp);
                let (chars, words) = (stripped.chars().count() as i64, word_count(stripped) as i64);
                if chars < min_chars || words < 3 {
                    out.push(finding("inadequate-disposition", Some(rid), vec![
                        ("disposition_chars", Value::Int(chars)),
                        ("disposition_words", Value::Int(words)),
                        ("min_chars", Value::Int(min_chars)),
                        ("disposition", Value::Str(disp.to_string())),
                    ]));
                }
            }
        }
        let (named, unresolved) = ids_named_in(get(h, "disposition"), &known);
        let linked = linkage.get(rid);
        let unlinked: Vec<String> = named.into_iter().filter(|i| i != rid && !linked.is_some_and(|l| l.contains(i))).collect();
        if !unlinked.is_empty() {
            let closed_pre_retires = terminal && first_terminal.get(rid).is_some_and(|w| !w.is_empty() && *w < RETIRES_LANDED);
            if closed_pre_retires && !opts.historical {
                grandfathered += 1;
            } else {
                out.push(finding("prose-only-linkage", Some(rid), vec![
                    ("ids", Value::Str(unlinked.join(", "))),
                    ("unresolved", Value::Str(if unresolved.is_empty() { "none".into() } else { unresolved.join(", ") })),
                ]));
            }
        }
        titles.entry(py_strip(&casefold(get_str(h, "title").unwrap_or(""))).to_string()).or_default().push(rid.to_string());
    }
    for (title, ids) in &titles {
        if !title.is_empty() && ids.len() > 1 {
            let mut all = ids.clone();
            all.sort();
            out.push(finding("possible-duplicates", Some(&ids[0]), vec![("ids", Value::Str(all.join(", ")))]));
        }
    }
    if grandfathered > 0 {
        out.push(finding("historical-prose-only-linkage", None, vec![
            ("count", Value::Int(grandfathered)),
            ("cutoff", Value::Str(RETIRES_LANDED.into())),
        ]));
    }
    let recs: Vec<&Pairs> = records.iter().filter_map(|r| as_obj(r)).collect();
    for rec in &recs {
        if get(rec, "forced") == Some(&Value::Bool(true)) {
            out.push(finding("forced-write", get_str(rec, "id"), vec![
                ("rev", raw(rec, "rev")), ("owner", raw(rec, "owner")), ("updated", raw(rec, "updated")),
            ]));
        }
    }
    let head_of: HashMap<&str, &Pairs> = heads.iter().map(|(id, h)| (id.as_str(), *h)).collect();
    for rec in &recs {
        let rid = get_str(rec, "id");
        if rid.and_then(|r| head_of.get(r)).is_some_and(|head| get(head, "rev") == get(rec, "rev")) {
            continue;
        }
        let status = get_str(rec, "status");
        if is_terminal(status) && py_strip(get_str(rec, "disposition").unwrap_or("")).is_empty() {
            out.push(finding("historical-custody-violation", rid, vec![
                ("rev", raw(rec, "rev")), ("violation", Value::Str("terminal without disposition".into())),
            ]));
        }
        if get_str(rec, "type") == Some("defect") && status == Some("done") && !evidence_is_structural(get(rec, "evidence"), cfg) {
            out.push(finding("historical-custody-violation", rid, vec![
                ("rev", raw(rec, "rev")), ("violation", Value::Str("done defect without structural evidence".into())),
            ]));
        }
    }
    let declared = cfg.resolvers();
    for (field, kind) in [("evidence", "unresolvable-reference"), ("context", "unresolvable-context")] {
        for (rid, h) in &sorted {
            // An undeclared scheme is E011's to report, not a second report here.
            let Some((scheme, target)) = parse_foreign_ref(get(h, field)) else { continue };
            let Some(command) = declared.get(scheme) else { continue };
            if !opts.resolve {
                out.push(finding("reference-not-attempted", Some(rid), vec![
                    ("field", Value::Str(field.into())), ("scheme", Value::Str(scheme.into())),
                ]));
                continue;
            }
            let (ok, reason, exit, stderr) = env.resolve(command, target);
            if !ok {
                out.push(finding(kind, Some(rid), vec![
                    ("scheme", Value::Str(scheme.into())),
                    ("target", Value::Str(target.into())),
                    ("reason", Value::Str(reason.into())),
                    ("exit", exit.map_or(Value::Str("n/a".into()), Value::Int)),
                    ("stderr", Value::Str(stderr)),
                ]));
            }
        }
    }
    for (rid, h) in &sorted {
        if !is_terminal(get_str(h, "status")) {
            continue;
        }
        let Some(phrase) = get_str(h, "disposition").and_then(imperative) else { continue };
        if !env.cited_in_tests(rid) {
            out.push(finding("unverified-imperative", Some(rid), vec![
                ("phrase", Value::Str(phrase)),
                ("reason", Value::Str("no test file names this record".into())),
            ]));
        }
    }
    for (rid, h) in &sorted {
        let Some(token) = evidence_command_head(get(h, "evidence"), cfg) else { continue };
        let resolvable = token.trim_end_matches(';').split("::").next().unwrap_or("");
        if !env.command_resolves(resolvable) {
            out.push(finding("unresolvable-evidence", Some(rid), vec![
                ("evidence_kind", Value::Str(evidence_kind(h, cfg))),
                ("reason", Value::Str("does not resolve: neither on PATH nor a path in this tree — the evidence cannot be run here".into())),
                ("evidence", raw(h, "evidence")),
            ]));
        }
    }
    if opts.sample != 0 {
        let mut closed: Vec<(String, &str, &Pairs)> = heads
            .iter()
            .filter(|(_, h)| is_terminal(get_str(h, "status")))
            .map(|(id, h)| (crate::sha256_hex(id.as_bytes()), id.as_str(), *h))
            .collect();
        closed.sort_by(|a, b| a.0.cmp(&b.0));
        let n = closed.len() as i64;
        let take = if opts.sample > 0 { opts.sample.min(n) } else { (n + opts.sample).max(0) };
        for (_, rid, h) in closed.into_iter().take(take as usize) {
            let disp = get_str(h, "disposition");
            out.push(finding("truth-audit-sample", Some(rid), vec![
                ("rev", raw(h, "rev")),
                ("disposition_chars", Value::Int(disp.map_or(0, |d| d.chars().count()) as i64)),
                ("disposition_words", Value::Int(disp.map_or(0, word_count) as i64)),
                ("evidence_kind", Value::Str(evidence_kind(h, cfg))),
                ("disposition", raw(h, "disposition")),
                ("evidence", raw(h, "evidence")),
            ]));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prose_ids_follow_the_word_boundary_and_strip_trailing_punctuation() {
        assert_eq!(prose_ids("see pc-ab12. and xpc-cd34, (pc-ef56-) pc-1.pc-2"), ["pc-ab12", "pc-ef56", "pc-1.pc-2"]);
        assert_eq!(prose_ids("_pc-a1 épc-b2 pc- pc-.x"), Vec::<&str>::new());
    }

    #[test]
    fn imperatives_match_whole_words_case_insensitively() {
        assert_eq!(imperative("Callers MUST route through it").as_deref(), Some("callers must"));
        assert_eq!(imperative("it must better be").as_deref(), None);
        assert_eq!(imperative("x mustn't; y must Be").as_deref(), Some("must be"));
        assert_eq!(imperative("the caller muſt").as_deref(), Some("the caller muſt"));
        assert_eq!(imperative("must re-run").as_deref(), Some("must re-run"));
        assert_eq!(imperative("implementations must").as_deref(), Some("implementations must"));
    }

    #[test]
    fn a_placeholder_is_filled_once() {
        let v = finding("stale-in-progress", Some("pc-1"), vec![("since", Value::Str("{since}".into()))]);
        assert_eq!(v.get("note").and_then(Value::as_str), Some("in-progress but untouched since {since}"));
    }
}
