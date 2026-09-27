//! The CLI's output for people (v3.5, pc-f590f6ef556a).
//!
//! The CLI is read by people and the MCP server by models. A command's reply
//! is the same either way; this module is only how the CLI prints it when
//! `--json` is not given. With `--json` every value is one JSON document per
//! line, exactly as before v3.5, and the MCP server never comes here.
//!
//! Each function mirrors the reference implementation's of the same name, so
//! the two print the same bytes. Every rendered string is bounded by
//! `safe_text` as its JSON value is, except `show`, which prints a record's
//! text as written with only its control characters removed.

use crate::args::Parsed;
use crate::output::dumps;
use pecia_core::text::{plain_line, safe_text, MESSAGE_CAP};
use pecia_core::Value;

/// Whether this invocation prints JSON: `--json`, or graph's `--format json`.
pub fn json_mode(parsed: &Parsed) -> bool {
    parsed.flag("json") || parsed.str("format") == Some("json")
}

/// A fatal on stderr, for a person.
pub fn fatal_line(v: &Value) -> String {
    let message = match field(v, "message") {
        Value::Str(s) => s.clone(),
        other => plain(other),
    };
    format!("pecia: {}", safe_text(&message, MESSAGE_CAP))
}

/// One emitted value, rendered for a person.
pub fn human(parsed: &Parsed, value: &Value) -> String {
    let command = parsed.command;
    if is_finding(value) {
        return human_finding(value);
    }
    if let Some(verb) = echo_verb(command) {
        if let Value::Array(items) = value {
            return items.iter().map(|v| human(parsed, v)).collect::<Vec<_>>().join("\n");
        }
        if has(value, "rev") && has(value, "title") {
            let verb = if matches!(field(value, "unchanged"), Value::Bool(true)) { "unchanged" } else { verb };
            return human_echo(verb, value);
        }
    }
    match (command, value) {
        ("ready" | "next", Value::Array(rows)) => return human_queue(rows),
        ("blocked", Value::Array(rows)) => return human_blocked(rows),
        ("show", Value::Array(entries)) => return human_history(entries),
        ("show", Value::Object(_)) => return human_record(value).join("\n"),
        ("graph", Value::Object(_)) if has(value, "nodes") => return human_graph(value),
        ("audit", Value::Object(_)) if has(value, "findings") => return human_audit(value),
        _ => {}
    }
    human_fields(value).join("\n")
}

fn echo_verb(command: &str) -> Option<&'static str> {
    match command {
        "add" => Some("added"),
        "edit" => Some("edited"),
        "close" => Some("closed"),
        _ => None,
    }
}

const NULL: Value = Value::Null;

fn field<'a>(v: &'a Value, key: &str) -> &'a Value {
    match v {
        Value::Object(pairs) => pairs.iter().find(|(k, _)| k == key).map_or(&NULL, |(_, x)| x),
        _ => &NULL,
    }
}

fn has(v: &Value, key: &str) -> bool {
    matches!(v, Value::Object(pairs) if pairs.iter().any(|(k, _)| k == key))
}

fn is_finding(v: &Value) -> bool {
    matches!(v, Value::Object(pairs) if pairs.len() == 4
        && ["code", "id", "message", "severity"].iter().all(|k| pairs.iter().any(|(x, _)| x == k)))
}

/// The reference's truthiness, for "print this line only if there is one".
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Int(n) => *n != 0,
        Value::Str(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn empty(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Str(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
        _ => false,
    }
}

/// A value on one line of a rendered view: bounded like any JSON string.
fn plain(v: &Value) -> String {
    match v {
        Value::Null => "none".to_string(),
        Value::Bool(b) => (if *b { "yes" } else { "no" }).to_string(),
        Value::Array(items) => items.iter().map(plain).collect::<Vec<_>>().join(", "),
        Value::Object(_) => safe_text(&dumps(v), 0),
        Value::Int(n) => safe_text(&n.to_string(), 0),
        Value::Str(s) => safe_text(s, 0),
    }
}

/// A record's text as its lines, for `show`.
fn record_lines(text: &str) -> Vec<String> {
    text.split('\n').map(plain_line).collect()
}

fn label(key: &str) -> String {
    key.replace(['_', '.'], " ")
}

/// `label: value` lines with the values in one column; a value of several
/// lines continues indented beneath its label.
fn aligned(pairs: &[(String, Vec<String>)]) -> Vec<String> {
    let width = pairs.iter().map(|(k, _)| k.chars().count()).max().unwrap_or(0) + 1;
    let mut out = Vec::new();
    for (key, lines) in pairs {
        if lines.len() == 1 {
            out.push(format!("{:<width$} {}", format!("{key}:"), lines[0]));
        } else {
            out.push(format!("{key}:"));
            out.extend(lines.iter().map(|l| format!("  {l}")));
        }
    }
    out
}

fn sorted_pairs(v: &Value) -> Vec<&(String, Value)> {
    let mut pairs: Vec<&(String, Value)> = match v {
        Value::Object(p) => p.iter().collect(),
        _ => Vec::new(),
    };
    pairs.sort_by(|a, b| a.0.cmp(&b.0));
    pairs
}

fn flatten<'a>(v: &'a Value, prefix: &str, out: &mut Vec<(String, &'a Value)>) {
    for (key, x) in sorted_pairs(v) {
        if matches!(x, Value::Object(o) if !o.is_empty()) {
            flatten(x, &format!("{prefix}{key}."), out);
        } else {
            out.push((format!("{prefix}{key}"), x));
        }
    }
}

/// Any other value: an object as aligned fields, a list one per line.
fn human_fields(v: &Value) -> Vec<String> {
    match v {
        Value::Object(_) => {
            let mut flat = Vec::new();
            flatten(v, "", &mut flat);
            aligned(&flat.into_iter().map(|(k, x)| (label(&k), vec![plain(x)])).collect::<Vec<_>>())
        }
        Value::Array(items) => {
            let lines: Vec<String> = items.iter().flat_map(human_fields).collect();
            if lines.is_empty() { vec!["none".to_string()] } else { lines }
        }
        other => vec![plain(other)],
    }
}

fn human_finding(f: &Value) -> String {
    let id = field(f, "id");
    let place = if matches!(id, Value::Null) { String::new() } else { format!(" {}", plain(id)) };
    format!("{} {}{place}: {}", plain(field(f, "severity")), plain(field(f, "code")), plain(field(f, "message")))
}

fn human_echo(verb: &str, r: &Value) -> String {
    format!(
        "{verb} {} rev {} · {} · p{} · {} · {}",
        plain(field(r, "id")),
        plain(field(r, "rev")),
        plain(field(r, "type")),
        plain(field(r, "priority")),
        plain(field(r, "status")),
        plain(field(r, "title"))
    )
}

fn human_queue(rows: &[Value]) -> String {
    if rows.is_empty() {
        return "nothing ready".to_string();
    }
    let ids: Vec<String> = rows.iter().map(|r| plain(field(r, "id"))).collect();
    let types: Vec<String> = rows.iter().map(|r| plain(field(r, "type"))).collect();
    let iw = ids.iter().map(|i| i.chars().count()).max().unwrap_or(0);
    let tw = types.iter().map(|t| t.chars().count()).max().unwrap_or(0);
    rows.iter()
        .zip(ids.iter().zip(&types))
        .map(|(r, (i, t))| format!("{i:<iw$}  p{}  {t:<tw$}  {}", plain(field(r, "priority")), plain(field(r, "title"))))
        .collect::<Vec<_>>()
        .join("\n")
}

fn human_blocked(rows: &[Value]) -> String {
    if rows.is_empty() {
        return "nothing blocked".to_string();
    }
    let mut out = Vec::new();
    for r in rows {
        out.push(format!("{}  {}", plain(field(r, "id")), plain(field(r, "title"))));
        if truthy(field(r, "blockers")) {
            out.push(format!("  blocked by {}", plain(field(r, "blockers"))));
        }
        if truthy(field(r, "awaiting_answers")) {
            out.push(format!("  awaiting answers from {}", plain(field(r, "awaiting_answers"))));
        }
    }
    out.join("\n")
}

/// A stored field's value for `show`, as lines.
fn shown(v: &Value) -> Vec<String> {
    match v {
        Value::Str(s) => record_lines(s),
        Value::Array(items) if items.iter().all(|x| matches!(x, Value::Str(_))) => {
            vec![items
                .iter()
                .map(|x| match x {
                    Value::Str(s) => record_lines(s).join(" "),
                    _ => String::new(),
                })
                .collect::<Vec<_>>()
                .join(", ")]
        }
        Value::Array(_) | Value::Object(_) => record_lines(&dumps(v)),
        other => vec![plain(other)],
    }
}

const HEADED: [&str; 7] = ["id", "type", "priority", "status", "rev", "title", "body"];

/// A record as a person reads it: who and what it is, its title, its other
/// fields aligned (empty ones left out), then its body as written.
fn human_record(r: &Value) -> Vec<String> {
    let one = |k: &str| shown(field(r, k)).join(" ");
    let mut head = Vec::new();
    if has(r, "id") {
        head.push(one("id"));
    }
    if has(r, "type") {
        head.push(one("type"));
    }
    if has(r, "priority") {
        head.push(format!("p{}", one("priority")));
    }
    if has(r, "status") {
        head.push(one("status"));
    }
    if has(r, "rev") {
        head.push(format!("rev {}", one("rev")));
    }
    let mut out = vec![head.join(" · "), if has(r, "title") { one("title") } else { String::new() }];
    let mut pairs: Vec<(String, Vec<String>)> = Vec::new();
    for (key, value) in sorted_pairs(r) {
        if HEADED.contains(&key.as_str()) {
            continue;
        }
        if key == "edges" && matches!(value, Value::Object(_)) {
            for (kind, targets) in sorted_pairs(value) {
                if !empty(targets) {
                    pairs.push((label(kind), shown(targets)));
                }
            }
        } else if !empty(value) {
            pairs.push((label(key), shown(value)));
        }
    }
    if !pairs.is_empty() {
        out.push(String::new());
        out.extend(aligned(&pairs));
    }
    if !empty(field(r, "body")) {
        out.push(String::new());
        out.extend(shown(field(r, "body")));
    }
    out
}

fn at_path<'a>(rec: &'a Value, path: &str) -> &'a Value {
    let mut v = rec;
    for part in path.split('.') {
        v = field(v, part);
    }
    v
}

fn human_history(entries: &[Value]) -> String {
    let empty_rec = Value::Object(Vec::new());
    let mut out: Vec<String> = Vec::new();
    for (n, e) in entries.iter().enumerate() {
        let rec = if truthy(field(e, "rec")) { field(e, "rec") } else { &empty_rec };
        let touched: Vec<String> = match field(e, "touched") {
            Value::Array(t) => t.iter().map(plain).collect(),
            _ => Vec::new(),
        };
        let what = match (touched.is_empty(), n) {
            (false, _) => format!("changed {}", touched.join(", ")),
            (true, 0) => "created".to_string(),
            (true, _) => "changed nothing".to_string(),
        };
        if n > 0 {
            out.push(String::new());
        }
        out.push(format!("seq {} · rev {} · {what}", plain(field(e, "seq")), plain(field(rec, "rev"))));
        if n == 0 {
            out.extend(human_record(rec).into_iter().map(|l| if l.is_empty() { l } else { format!("  {l}") }));
        } else {
            let pairs: Vec<(String, Vec<String>)> = touched
                .iter()
                .map(|t| {
                    let v = at_path(rec, t);
                    (label(t), if empty(v) { vec!["none".to_string()] } else { shown(v) })
                })
                .collect();
            out.extend(aligned(&pairs).into_iter().map(|l| format!("  {l}")));
        }
    }
    out.join("\n")
}

/// A key for grouping by a value, as the reference's dict keys them.
fn key_of(v: &Value) -> String {
    match v {
        Value::Str(s) => s.clone(),
        other => dumps(other),
    }
}

fn human_graph(g: &Value) -> String {
    let nodes: &[Value] = match field(g, "nodes") {
        Value::Array(n) => n,
        _ => &[],
    };
    let edges: &[Value] = match field(g, "edges") {
        Value::Array(e) => e,
        _ => &[],
    };
    let mut titles: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for n in nodes {
        titles.insert(key_of(field(n, "id")), plain(field(n, "title")));
    }
    let title = |v: &Value| titles.get(&key_of(v)).cloned().unwrap_or_default();
    let mut out = vec![format!("{} records, {} edges", nodes.len(), edges.len())];
    let mut groups: Vec<(String, &Value, Vec<&Value>)> = Vec::new();
    for e in edges {
        let from = field(e, "from");
        let k = key_of(from);
        match groups.iter_mut().find(|(g, _, _)| *g == k) {
            Some((_, _, items)) => items.push(e),
            None => groups.push((k, from, vec![e])),
        }
    }
    for (_, source, outgoing) in groups {
        out.push(String::new());
        out.push(format!("{}  {}", plain(source), title(source)).trim_end().to_string());
        for e in outgoing {
            let to = field(e, "to");
            out.push(format!("  {} → {}  {}", plain(field(e, "kind")), plain(to), title(to)).trim_end().to_string());
        }
    }
    out.join("\n")
}

fn human_audit(a: &Value) -> String {
    let findings: &[Value] = match field(a, "findings") {
        Value::Array(f) => f,
        _ => &[],
    };
    let mut groups: Vec<(String, &Value, Vec<&Value>)> = Vec::new();
    for f in findings {
        let kind = field(f, "kind");
        let k = key_of(kind);
        match groups.iter_mut().find(|(g, _, _)| *g == k) {
            Some((_, _, items)) => items.push(f),
            None => groups.push((k, kind, vec![f])),
        }
    }
    let mut out: Vec<String> = Vec::new();
    for (_, kind, items) in &groups {
        out.push(format!("{} ({})", plain(kind), items.len()));
        for f in items {
            out.push(format!("  {}: {}", plain(field(f, "id")), plain(field(f, "note"))));
            let rest: Vec<(String, Vec<String>)> = sorted_pairs(f)
                .into_iter()
                .filter(|(k, _)| !matches!(k.as_str(), "id" | "kind" | "note"))
                .map(|(k, v)| (label(k), vec![plain(v)]))
                .collect();
            out.extend(aligned(&rest).into_iter().map(|l| format!("      {l}")));
        }
        out.push(String::new());
    }
    if groups.is_empty() {
        out.push("no audit findings".to_string());
        out.push(String::new());
    }
    let rest = match a {
        Value::Object(p) => Value::Object(p.iter().filter(|(k, _)| k != "findings").cloned().collect()),
        other => other.clone(),
    };
    out.extend(human_fields(&rest));
    out.join("\n")
}
