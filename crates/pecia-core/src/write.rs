//! The pure half of the write path: building revisions and entries, the
//! per-id compare-and-swap, the write gate, and the projection a write prints.
//! The IO half — the lock, the append, the clock, git — lives in the CLI.

use crate::check::{diff_fields, log_head_hash, run_checks};
use crate::config::{py_strip, shlex_split, Config, TERMINAL_STATUSES};
use crate::finding::{error, Finding, Severity};
use crate::record::{as_obj, get, get_str, parse_foreign_ref, record_is_sound, strict_int, Pairs, LIST_EDGES, SCALAR_EDGES};
use crate::text::{safe_id, safe_text, MESSAGE_CAP};
use crate::value::Value;
use std::collections::{HashMap, HashSet};

pub const VOCAB_CAP: usize = 256;
pub const TITLE_CAP: usize = 4096;
pub const OWNER_CAP: usize = 1024;
pub const DATE_CAP: usize = 128;

pub fn set(obj: &mut Vec<(String, Value)>, key: &str, v: Value) {
    match obj.iter_mut().find(|(k, _)| k == key) {
        Some((_, slot)) => *slot = v,
        None => obj.push((key.to_string(), v)),
    }
}

pub fn remove(obj: &mut Vec<(String, Value)>, key: &str) {
    obj.retain(|(k, _)| k != key);
}

pub fn pairs_mut(v: &mut Value) -> &mut Vec<(String, Value)> {
    match v {
        Value::Object(p) => p,
        _ => panic!("not an object"),
    }
}

pub fn empty_edges() -> Value {
    let mut e: Vec<(String, Value)> = LIST_EDGES.iter().map(|k| (k.to_string(), Value::Array(Vec::new()))).collect();
    e.extend(SCALAR_EDGES.iter().map(|k| (k.to_string(), Value::Null)));
    Value::Object(e)
}

/// The record's edge object, created empty if absent.
pub fn edges_mut(rec: &mut Vec<(String, Value)>) -> &mut Vec<(String, Value)> {
    if !rec.iter().any(|(k, _)| k == "edges") {
        rec.push(("edges".into(), empty_edges()));
    }
    let slot = rec.iter_mut().find(|(k, _)| k == "edges").map(|(_, v)| v).expect("edges");
    if !matches!(slot, Value::Object(_)) {
        *slot = empty_edges();
    }
    pairs_mut(slot)
}

pub fn strs(items: &[String]) -> Value {
    Value::Array(items.iter().cloned().map(Value::Str).collect())
}

/// `retires` gains the targets, as a sorted set.
pub fn merge_retires(rec: &mut Vec<(String, Value)>, targets: &[String]) {
    if targets.is_empty() {
        return;
    }
    let edges = edges_mut(rec);
    let mut all: Vec<String> = match get(edges, "retires") {
        Some(Value::Array(items)) => items.iter().filter_map(Value::as_str).map(str::to_string).collect(),
        _ => Vec::new(),
    };
    all.extend(targets.iter().cloned());
    all.sort();
    all.dedup();
    set(edges, "retires", strs(&all));
}

fn rec_in(entry: &Value) -> Option<&Pairs> {
    entry.get("rec").and_then(as_obj)
}

/// The head of `id` in a timeline: its highest well-typed revision, the first
/// record carrying it. One scan, nothing allocated — the single-shot form of
/// what a `Chain` keeps current.
pub fn head_in<'a>(entries: &'a [Value], id: &str) -> Option<&'a Pairs> {
    let mut best: Option<(i64, &Pairs)> = None;
    for r in entries.iter().filter_map(rec_in).filter(|r| get_str(r, "id") == Some(id)) {
        let Some(rev) = strict_int(get(r, "rev")) else { continue };
        if best.is_none_or(|(b, _)| rev > b) {
            best = Some((rev, r));
        }
    }
    best.map(|(_, r)| r)
}

/// The per-id half of the compare-and-swap, against the head `rec` would be
/// written over: rev N+1 only if the head is N. A boolean rev is not a head
/// (pc-d89f) — structurally so here.
pub fn cas(head: Option<&Pairs>, rec: &Pairs) -> Result<(), String> {
    let rev = get(rec, "rev");
    let shown = |v: Option<&Value>| match v {
        Some(Value::Int(n)) => n.to_string(),
        Some(other) => crate::canonical::canonical(other),
        None => "null".into(),
    };
    match head {
        None if strict_int(rev) != Some(1) => Err(format!("rev {} for a record with no head (expected 1)", shown(rev))),
        None => Ok(()),
        Some(head) => {
            let head_rev = strict_int(get(head, "rev")).unwrap_or(0);
            if strict_int(rev) != Some(head_rev + 1) {
                Err(format!("rev {} does not follow head rev {head_rev} — re-read and retry (this is the CAS, not a merge)", shown(rev)))
            } else {
                Ok(())
            }
        }
    }
}

pub fn cas_admissible(entries: &[Value], rec: &Pairs) -> Result<(), String> {
    cas(get_str(rec, "id").and_then(|id| head_in(entries, id)), rec)
}

/// The entry at position `seq` linking to `prev` that appends `rec`, with the
/// field-set it changed against `head` — derived, never authored.
pub fn entry(seq: usize, prev: Option<String>, head: Option<&Pairs>, rec: Value) -> Value {
    let touched = diff_fields(head, as_obj(&rec).expect("record"));
    Value::Object(vec![
        ("seq".into(), Value::Int(seq as i64)),
        ("prev".into(), prev.map_or(Value::Null, Value::Str)),
        ("touched".into(), strs(&touched)),
        ("rec".into(), rec),
    ])
}

pub fn make_entry(entries: &[Value], rec: &Value) -> Value {
    let head = as_obj(rec).and_then(|r| get_str(r, "id")).and_then(|id| head_in(entries, id));
    entry(entries.len() + 1, log_head_hash(entries), head, rec.clone())
}

/// A timeline as a writer extends it. The head of every id and the hash of
/// the last entry are kept current as it grows, so admitting a record and
/// appending it cost nothing in the timeline's length — where re-deriving
/// both from the entries on every append made a rebuild quadratic.
pub struct Chain {
    entries: crate::check::Entries,
    /// id -> (head rev, index of the head's entry).
    heads: HashMap<String, (i64, usize)>,
    last: Option<String>,
}

impl Chain {
    pub fn new(entries: impl Into<crate::check::Entries>) -> Chain {
        let entries = entries.into();
        let mut heads = HashMap::new();
        for (i, e) in entries.iter().enumerate() {
            Chain::index(&mut heads, e, i);
        }
        Chain { last: log_head_hash(&entries), heads, entries }
    }

    fn index(heads: &mut HashMap<String, (i64, usize)>, e: &Value, i: usize) {
        let Some(r) = rec_in(e) else { return };
        let (Some(id), Some(rev)) = (get_str(r, "id"), strict_int(get(r, "rev"))) else { return };
        match heads.get_mut(id) {
            None => {
                heads.insert(id.to_string(), (rev, i));
            }
            Some(h) if rev > h.0 => *h = (rev, i),
            _ => {}
        }
    }

    pub fn entries(&self) -> &[Value] {
        &self.entries
    }

    /// The entries with their places in the log.
    pub fn log(&self) -> &crate::check::Entries {
        &self.entries
    }

    pub fn into_entries(self) -> crate::check::Entries {
        self.entries
    }

    /// The hash of the last entry, or None for an empty timeline.
    pub fn head_hash(&self) -> Option<&str> {
        self.last.as_deref()
    }

    pub fn head(&self, id: &str) -> Option<&Pairs> {
        self.heads.get(id).and_then(|&(_, i)| rec_in(&self.entries[i]))
    }

    /// The compare-and-swap for `rec` against this timeline.
    pub fn admit(&self, rec: &Pairs) -> Result<(), String> {
        cas(get_str(rec, "id").and_then(|id| self.head(id)), rec)
    }

    /// The entry that would append `rec` — built, not yet appended, so a
    /// writer can refuse it first.
    pub fn next_entry(&self, rec: Value) -> Value {
        let head = as_obj(&rec).and_then(|r| get_str(r, "id")).and_then(|id| self.head(id));
        entry(self.entries.len() + 1, self.last.clone(), head, rec)
    }

    pub fn push(&mut self, entry: Value) {
        let line = crate::canonical::canonical(&entry);
        self.last = Some(crate::sha256_hex(line.as_bytes()));
        Chain::index(&mut self.heads, &entry, self.entries.len());
        self.entries.push_line(entry, line.len());
    }

    pub fn append(&mut self, rec: Value) {
        let e = self.next_entry(rec);
        self.push(e);
    }
}

/// The write gate's wrapper around a checker finding. The inner half is
/// budgeted so the wrapper's own words — including the escape hatch — survive.
pub fn refusal_message(message: &str) -> String {
    let (head, tail) = ("write refused: ", " (--force to override)");
    let inner = safe_text(message, MESSAGE_CAP.saturating_sub(head.chars().count() + tail.chars().count()));
    format!("{head}{inner}{tail}")
}

fn narrows_a_prior_finding(mine: &Finding, prior: &[Finding]) -> bool {
    let Some(my_group) = &mine.group else { return false };
    // For E004 the SAME component is not new either (pc-c69d): a cycle has
    // no subject, its group is its cyclic component, and removing an edge
    // keeps a component or shrinks it while the reported witness can move.
    let same_allowed = mine.code == crate::finding::Code::E004;
    prior.iter().any(|g| {
        g.code == mine.code
            && g.subject == mine.subject
            && g.group.as_ref().is_some_and(|theirs| {
                (my_group.len() < theirs.len() || (same_allowed && my_group.len() == theirs.len()))
                    && my_group.iter().all(|m| theirs.contains(m))
            })
    })
}

/// A write is refused iff it introduces an error that was not there before.
/// Pre-existing damage never blocks an unrelated write, and a repair that only
/// NARROWS a grouped finding is not a new error (pc-54c7).
pub fn write_gate(records: &[&Value], candidate: &Value, cfg: &Config) -> Vec<Finding> {
    let lines: Vec<usize> = (1..=records.len()).collect();
    let prior: Vec<Finding> = run_checks(records, Vec::new(), cfg, Some(&lines))
        .into_iter()
        .filter(|f| f.severity == Severity::Error)
        .collect();
    let before: HashSet<_> = prior.iter().map(Finding::key).collect();
    let mut all = records.to_vec();
    all.push(candidate);
    let mut after_lines = lines.clone();
    after_lines.push(records.len() + 1);
    run_checks(&all, Vec::new(), cfg, Some(&after_lines))
        .into_iter()
        .filter(|f| f.severity == Severity::Error)
        .filter(|f| !before.contains(&f.key()) && !narrows_a_prior_finding(f, &prior))
        .map(|f| error(f.code, f.id.as_deref(), &refusal_message(&f.message)))
        .collect()
}

/// The write gate for ONE appended revision, computed over only what that
/// append can change. It equals `write_gate` over the whole timeline when
/// every record in the timeline is sound with no revision duplicated — what
/// the query index certifies — and `candidate` is its head's next revision,
/// or rev 1 of an id with no head. `heads` are the timeline's heads (their
/// query views suffice) in first-seen order; `head` is the candidate's own
/// head, whole; `records` is how many records the timeline holds.
///
/// Why only this, finding by finding. The gate reports an error the full
/// checker finds after the append and did not find before. Appending one
/// revision of record X changes:
/// - the record-level findings (E001 and kin) by the candidate's own, each
///   tagged with its rev or line, which no earlier record carries;
/// - the per-id history by one new pair, X's head to the candidate (E005); no
///   duplicate revision (E002) and no gap (E008) can appear, as its rev is
///   one past the highest;
/// - the per-head findings (E003, E017, E006, E007, E011) of X alone — every
///   other head is the same record before and after, and targets that X's
///   arrival resolves only remove findings;
/// - the graph findings (E004, E012, E016, E009), which are recomputed over
///   all heads before and after: they are few and cheap, and which cycle a
///   search reports first depends on the whole graph.
///
/// Everything else is identical before and after, so it can never be new.
/// `write_gate_local_equals_write_gate` holds the two to the same answers,
/// in the same order, over the corpus bent every way a write can bend it.
pub fn write_gate_local(heads: &[(&str, &Pairs)], head: Option<&Pairs>, candidate: &Value, records: usize, cfg: &Config) -> Vec<Finding> {
    use crate::check as c;
    let Some(cand) = as_obj(candidate) else { return Vec::new() };
    let (statuses, types) = (cfg.allowed_statuses(), cfg.allowed_types());
    let declared = cfg.resolvers();
    let planned = cfg.planned_ids();
    let is_planned = |t: &str| planned.iter().any(|p| p == t);

    // In the order the full checker emits them.
    let mut after: Vec<Finding> = crate::record::validate_record(cand, statuses, types, Some(records + 1));
    let mut before: Vec<Finding> = Vec::new();
    let x = get_str(cand, "id").unwrap_or("");
    if record_is_sound(candidate) {
        let mut next: Vec<(&str, &Pairs)> = heads.to_vec();
        match next.iter_mut().find(|(k, _)| *k == x) {
            Some(slot) => slot.1 = cand,
            None => next.push((x, cand)),
        }
        let was = c::Graph::new(heads);
        let now = was.next(&next);
        after.extend(head.and_then(|h| c::e005(x, h, cand, statuses)));
        after.extend(c::e003(x, cand, |t| now.find(t).is_some() || is_planned(t)));
        after.extend(c::e004(&now));
        after.extend(c::e017(x, cand));
        after.extend(c::e006(x, cand));
        after.extend(c::e007(x, cand, cfg));
        after.extend(c::e011(x, cand, declared));
        after.extend(c::e012(&now));
        after.extend(c::e016(&now));
        after.extend(c::e009(&now));
        if let Some(h) = head {
            before.extend(c::e003(x, h, |t| was.find(t).is_some() || is_planned(t)));
            before.extend(c::e017(x, h));
            before.extend(c::e006(x, h));
            before.extend(c::e007(x, h, cfg));
            before.extend(c::e011(x, h, declared));
        }
        before.extend(c::e004(&was));
        before.extend(c::e012(&was));
        before.extend(c::e016(&was));
        before.extend(c::e009(&was));
    }
    let prior: Vec<Finding> = before.into_iter().filter(|f| f.severity == Severity::Error).collect();
    let seen: HashSet<_> = prior.iter().map(Finding::key).collect();
    after
        .into_iter()
        .filter(|f| f.severity == Severity::Error)
        .filter(|f| !seen.contains(&f.key()) && !narrows_a_prior_finding(f, &prior))
        .map(|f| error(f.code, f.id.as_deref(), &refusal_message(&f.message)))
        .collect()
}

/// Heads a command may schedule against, or the refusal that says why not.
/// A write or a query over a corrupt or malformed history refuses outright.
pub fn require_heads<'a>(records: &[&'a Value], parse_findings: &[Finding]) -> Result<Vec<(String, &'a Pairs)>, String> {
    use crate::finding::Code;
    for f in parse_findings {
        if f.code == Code::E000 {
            return Err(f.message.clone());
        }
        if f.code == Code::E013 {
            return Err(format!("timeline chain is broken — {} (run `pecia check`)", f.message));
        }
    }
    if parse_findings.iter().any(|f| f.code == Code::E001) {
        return Err("ledger has unparseable lines — run `pecia check`".into());
    }
    if records.iter().any(|r| !record_is_sound(r)) {
        return Err("ledger has malformed records (E001) — run `pecia check`".into());
    }
    // One pass: each id's head (the first record carrying its highest rev),
    // in first-seen order, and a count per (id, rev). Records are sound, so
    // every id is text and every rev an integer.
    let mut order: Vec<&'a str> = Vec::new();
    let mut head: HashMap<&'a str, (i64, &'a Pairs)> = HashMap::new();
    let mut seen: HashMap<(&'a str, i64), usize> = HashMap::new();
    for r in records.iter().copied().filter_map(as_obj) {
        let (id, rev) = (get_str(r, "id").expect("sound"), strict_int(get(r, "rev")).expect("sound"));
        *seen.entry((id, rev)).or_default() += 1;
        match head.get(id) {
            None => {
                order.push(id);
                head.insert(id, (rev, r));
            }
            Some(&(best, _)) if rev > best => {
                head.insert(id, (rev, r));
            }
            _ => {}
        }
    }
    if seen.values().any(|n| *n > 1) {
        // Named as the reference names it: the first id seen, its lowest
        // duplicated rev.
        for id in &order {
            let dup = seen.iter().filter(|((i, _), n)| i == id && **n > 1).map(|((_, rev), n)| (*rev, *n)).min();
            if let Some((rev, n)) = dup {
                return Err(format!(
                    "timeline is corrupt — {id} rev {rev} appears {n}x, which the compare-and-swap cannot admit (E002). Run `pecia check`; do not schedule against it"
                ));
            }
        }
    }
    Ok(order.into_iter().map(|id| (id.to_string(), head[id].1)).collect())
}

fn py_split_count(s: &str) -> usize {
    crate::text::word_count(s)
}

/// The command evidence would run — its first shell token, when that is a
/// path or a declared evidence command — or None for prose, a reference,
/// `unknown`, or text that does not tokenise.
pub fn evidence_command_head(evidence: Option<&Value>, cfg: &Config) -> Option<String> {
    let s = evidence?.as_str()?;
    if py_strip(s).is_empty() || s == "unknown" || parse_foreign_ref(evidence).is_some() {
        return None;
    }
    let head = shlex_split(s).ok()?.into_iter().next()?;
    (head.contains('/') || cfg.evidence_commands().contains(&head)).then_some(head)
}

/// Evidence something can check: a foreign reference or a runnable command.
pub fn evidence_is_structural(evidence: Option<&Value>, cfg: &Config) -> bool {
    parse_foreign_ref(evidence).is_some() || evidence_command_head(evidence, cfg).is_some()
}

/// Evidence's KIND, emitted in place of its text: its semantic type is a
/// command to run, and quoting an attacker-authored command into an agent's
/// context, next to a word like "verify", makes running it the natural next act.
pub fn evidence_kind(rec: &Pairs, cfg: &Config) -> String {
    match get(rec, "evidence") {
        None | Some(Value::Null) => "absent".into(),
        Some(Value::Str(s)) if py_strip(s).is_empty() => "absent".into(),
        Some(Value::Str(s)) if s == "unknown" => "unknown".into(),
        Some(v @ Value::Str(_)) => {
            if let Some((scheme, _)) = parse_foreign_ref(Some(v)) {
                format!("reference:{}", safe_text(scheme, VOCAB_CAP))
            } else if evidence_command_head(Some(v), cfg).is_some() {
                "command".into()
            } else {
                "prose".into()
            }
        }
        Some(_) => "malformed".into(),
    }
}

/// A field rendered as the projection shows it: bounded text for a string,
/// and the value's own spelling otherwise.
fn text_field(rec: &Pairs, key: &str, cap: usize) -> Value {
    match get(rec, key) {
        Some(Value::Str(s)) => Value::Str(safe_text(s, cap)),
        Some(Value::Null) | None => Value::Str(safe_text("None", cap)),
        Some(other) => Value::Str(safe_text(&crate::canonical::canonical(other), cap)),
    }
}

pub fn projected_edges(rec: &Pairs) -> Value {
    let Some(edges) = get(rec, "edges").and_then(as_obj) else { return Value::Object(Vec::new()) };
    let mut out: Vec<(String, Value)> = LIST_EDGES
        .iter()
        .map(|k| {
            let items = match get(edges, k) {
                Some(Value::Array(a)) => a.iter().filter_map(Value::as_str).map(|t| Value::Str(safe_id(t))).collect(),
                _ => Vec::new(),
            };
            (k.to_string(), Value::Array(items))
        })
        .collect();
    for k in SCALAR_EDGES {
        out.push((k.to_string(), get_str(edges, k).map_or(Value::Null, |t| Value::Str(safe_id(t)))));
    }
    if get(edges, "no_edges") == Some(&Value::Bool(true)) {
        out.push(("no_edges".into(), Value::Bool(true)));
    }
    Value::Object(out)
}

/// One field of a command's view of a record, or None where the field is
/// absent by presence semantics (`forced`).
pub fn project_field(key: &str, rec: &Pairs, cfg: &Config) -> Option<Value> {
    let int_or_null = |k: &str| strict_int(get(rec, k)).map_or(Value::Null, Value::Int);
    Some(match key {
        "id" => Value::Str(safe_id(&match get(rec, "id") {
            Some(Value::Str(s)) => s.clone(),
            Some(Value::Null) | None => "None".into(),
            Some(o) => crate::canonical::canonical(o),
        })),
        "rev" => int_or_null("rev"),
        "priority" => int_or_null("priority"),
        "type" | "status" => text_field(rec, key, VOCAB_CAP),
        "title" => text_field(rec, key, TITLE_CAP),
        "owner" => text_field(rec, key, OWNER_CAP),
        "created" | "updated" => text_field(rec, key, DATE_CAP),
        "target" => match get(rec, "target") {
            None | Some(Value::Null) => Value::Null,
            _ => text_field(rec, "target", DATE_CAP),
        },
        "forced" => {
            if get(rec, "forced") == Some(&Value::Bool(true)) { Value::Bool(true) } else { return None }
        }
        "edges" => projected_edges(rec),
        "body_chars" => Value::Int(get_str(rec, "body").map_or(0, |s| s.chars().count()) as i64),
        "disposition_chars" => Value::Int(get_str(rec, "disposition").map_or(0, |s| s.chars().count()) as i64),
        "disposition_words" => Value::Int(get_str(rec, "disposition").map_or(0, py_split_count) as i64),
        "evidence_kind" => Value::Str(evidence_kind(rec, cfg)),
        "labels_count" => Value::Int(match get(rec, "labels") { Some(Value::Array(a)) => a.len() as i64, _ => 0 }),
        other => panic!("unregistered projection field {other}"),
    })
}

/// A command's allow-listed view of a record. Subtractive by construction:
/// nothing starts from the record and deletes keys, so nothing is disclosed by
/// forgetting to exclude it.
pub fn project(command: &str, rec: &Pairs, cfg: &Config) -> Value {
    let fields: &[&str] = match command {
        "next" | "ready" => &["id", "type", "title", "priority"],
        "blocked" => &["id", "title"],
        // Graph is the one view over every head, closed ones included, so
        // it carries a record's age and rank as well.
        "graph" => &["id", "type", "status", "title", "priority", "created"],
        "gantt" => &["id", "title", "status", "created", "target"],
        "board" => &["id", "type", "status", "title", "priority"],
        "written" => &["id", "rev", "type", "title", "status", "priority", "created", "updated", "owner", "edges", "forced", "body_chars", "disposition_chars", "disposition_words", "evidence_kind", "labels_count"],
        other => panic!("unregistered projection {other}"),
    };
    Value::Object(fields.iter().filter_map(|k| project_field(k, rec, cfg).map(|v| (k.to_string(), v))).collect())
}

pub fn is_terminal(status: Option<&str>) -> bool {
    status.is_some_and(|s| TERMINAL_STATUSES.contains(&s))
}
