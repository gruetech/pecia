//! The checker: reading the stored formats, the cross-record invariants, and
//! the timeline invariants. Pure — every file arrives as bytes from a caller.
//!
//! Iteration order is part of the output contract: findings are emitted in the
//! reference implementation's order (sorted by id where it sorts, in first-seen
//! order where it does not), because a finding's position is something readers
//! and tests rely on.

use crate::canonical::canonical;
use crate::config::{py_strip, shlex_split, Config, TERMINAL_STATUSES};
use crate::entry_hash;
use crate::finding::{error, warning, Code, Finding, Severity};
use crate::parse::{parse, parse_noting_canonical};
use crate::record::{
    as_obj, get, get_str, has, parse_foreign_ref, record_is_sound, strict_int, validate_record,
    Pairs, LIST_EDGES, SCALAR_EDGES,
};
use crate::text::{capped_array, capped_seq, capped_value, safe_text, MESSAGE_ITEM_CAP, MESSAGE_VALUE_CAP};
use crate::value::Value;
use crate::write::evidence_command_head;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

pub const TOUCHED_FIELDS: &[&str] = &[
    "title", "status", "priority", "disposition", "evidence", "owner", "labels", "body", "target",
    "context", "ratified_by", "ratified",
];
pub const TOUCHED_EDGE_FIELDS: &[&str] = &[
    "edges.blocks", "edges.retires", "edges.parent", "edges.duplicate_of", "edges.discovered_from",
    "edges.caused_by", "edges.validates", "edges.supersedes", "edges.no_edges",
];
pub const PER_REVISION_FIELDS: &[&str] = &["id", "rev", "updated", "edges", "anchor", "anchor_dirty", "forced"];

/// LF-delimited physical lines (v2.12, pc-d96d); a trailing LF yields no
/// phantom empty line. A buffer that is valid UTF-8 as a whole — every
/// conforming log and projection — is split with `str`'s vectorized search;
/// only one that is not falls back to a byte at a time, 5x slower.
pub fn source_lines(bytes: &[u8]) -> Vec<&[u8]> {
    let mut lines: Vec<&[u8]> = match std::str::from_utf8(bytes) {
        Ok(text) => text.split('\n').map(str::as_bytes).collect(),
        Err(_) => bytes.split(|b| *b == b'\n').collect(),
    };
    if lines.last().is_some_and(|l| l.is_empty()) {
        lines.pop();
    }
    lines
}

/// Blank as the reference sees it: the line decodes, and strips to nothing.
fn is_blank(line: &[u8]) -> bool {
    std::str::from_utf8(line).is_ok_and(|s| py_strip(s).is_empty())
}

fn display_id(v: Option<&Value>) -> Option<String> {
    match v {
        None | Some(Value::Null) => None,
        Some(Value::Str(s)) => Some(s.clone()),
        Some(other) => Some(canonical(other)),
    }
}

fn render_opt(v: Option<&Value>) -> String {
    capped_value(v.unwrap_or(&Value::Null), MESSAGE_VALUE_CAP)
}

/// A timeline's entries, as read. Freeing a large one — a million trees of
/// small allocations — takes longer than reading it did, and nothing waits on
/// the result: past a threshold the entries are handed to a thread of their
/// own to free, which a process that exits next never waits for and a server
/// overlaps with its next request.
///
/// Entries read from bytes also carry where each line sits in them — the
/// byte offset and length the query index records (v3.2).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Entries {
    values: Vec<Value>,
    spans: Vec<(u64, u32)>,
}

/// Below this many entries freeing is cheaper than a thread.
const FREE_IN_BACKGROUND_FROM: usize = 4096;

impl Entries {
    pub fn into_vec(mut self) -> Vec<Value> {
        std::mem::take(&mut self.values)
    }

    /// Each entry's line as (byte offset, length without its LF), when every
    /// entry's place is known: read from bytes, or appended with its length.
    pub fn spans(&self) -> Option<&[(u64, u32)]> {
        (!self.values.is_empty() && self.spans.len() == self.values.len()).then_some(&self.spans[..])
    }

    /// The bytes the entries occupy as a log, every line LF-terminated.
    pub fn byte_len(&self) -> Option<u64> {
        match self.spans() {
            Some(spans) => spans.last().map(|&(o, l)| o + u64::from(l) + 1),
            None => self.values.is_empty().then_some(0),
        }
    }

    /// Append an entry whose canonical line is `len` bytes, keeping its place.
    pub fn push_line(&mut self, entry: Value, len: usize) {
        if let (Some(end), Ok(len)) = (self.byte_len(), u32::try_from(len)) {
            self.spans.push((end, len));
        }
        self.values.push(entry);
    }
}

impl From<Vec<Value>> for Entries {
    fn from(values: Vec<Value>) -> Self {
        Entries { values, spans: Vec::new() }
    }
}

impl std::ops::Deref for Entries {
    type Target = Vec<Value>;
    fn deref(&self) -> &Vec<Value> {
        &self.values
    }
}

impl std::ops::DerefMut for Entries {
    fn deref_mut(&mut self) -> &mut Vec<Value> {
        &mut self.values
    }
}

impl Drop for Entries {
    fn drop(&mut self) {
        if self.values.len() >= FREE_IN_BACKGROUND_FROM {
            let entries = std::mem::take(&mut self.values);
            // If no thread can be had, the entries are freed here, as before.
            let _ = std::thread::Builder::new().name("free".into()).spawn(move || drop(entries));
        }
    }
}

/// One log line read on its own: the entry and the hash of its canonical
/// form, or the finding that stops the read there.
enum LogLine {
    Entry(Value, String),
    Stop(Finding),
}

fn read_log_line(n: usize, raw: &[u8]) -> LogLine {
    if is_blank(raw) {
        return LogLine::Stop(error(Code::E001, None, &format!("log line {n} is blank — the log is one entry per line, with no blank lines")));
    }
    let Ok(text) = std::str::from_utf8(raw) else {
        return LogLine::Stop(error(Code::E001, None, &format!("log line {n} is not valid UTF-8 — the log is one UTF-8 JSON entry per line")));
    };
    let (entry, stored_canonical) = match parse_noting_canonical(text) {
        Ok(parsed) => parsed,
        Err(e) => return LogLine::Stop(error(Code::E001, None, &format!("log line {n} does not parse: {e}"))),
    };
    if as_obj(&entry).and_then(|o| get(o, "rec")).and_then(as_obj).is_none() {
        return LogLine::Stop(error(Code::E001, None, &format!("log line {n} is not an entry")));
    }
    // The link is the hash of the entry's canonical form; a line already in
    // that form is hashed as it is stored.
    let hash = if stored_canonical { crate::sha256_hex(raw) } else { entry_hash(&entry) };
    LogLine::Entry(entry, hash)
}

/// The log: entries in order, stopping at the first line that is not a
/// well-formed link — a log is trusted only up to its first break.
///
/// Every line is parsed and hashed on its own, across the cores; what links
/// the lines — each seq its position, each prev the hash of the line before —
/// is then checked in order, and the first break ends the read exactly where
/// a line-at-a-time reader would have stopped.
pub fn read_log(bytes: &[u8]) -> (Entries, Vec<Finding>) {
    let lines = source_lines(bytes);
    let read = crate::par::map(lines.len(), |i| read_log_line(i + 1, lines[i]));
    let span = |l: &[u8]| ((l.as_ptr() as usize - bytes.as_ptr() as usize) as u64, l.len() as u32);
    let mut spans = Vec::with_capacity(read.len());
    let mut entries = Vec::with_capacity(read.len());
    let mut findings = Vec::new();
    let mut prev_hash: Option<String> = None;
    for (i, line) in read.into_iter().enumerate() {
        let n = i + 1;
        let (entry, hash) = match line {
            LogLine::Entry(entry, hash) => (entry, hash),
            LogLine::Stop(f) => {
                findings.push(f);
                break;
            }
        };
        let obj = as_obj(&entry).expect("an entry");
        // The id a break is reported under — computed only for a break.
        let rid = || display_id(get(get(obj, "rec").and_then(as_obj).expect("an entry"), "id"));
        if strict_int(get(obj, "seq")) != Some(n as i64) {
            findings.push(error(Code::E013, rid().as_deref(), &format!("log line {n} declares seq {}", render_opt(get(obj, "seq")))));
            break;
        }
        if !has(obj, "prev") {
            findings.push(error(Code::E013, rid().as_deref(), &format!(
                "log line {n} omits the mandatory prev field — every entry carries seq, prev, touched, and rec (format-v2.md 3.2)"
            )));
            break;
        }
        let linked = match (get(obj, "prev"), &prev_hash) {
            (Some(Value::Null), None) => true,
            (Some(Value::Str(p)), Some(h)) => p == h,
            _ => false,
        };
        if !linked {
            findings.push(error(Code::E013, rid().as_deref(), &format!("log line {n} breaks the chain (prev does not match line {})", n - 1)));
            break;
        }
        prev_hash = Some(hash);
        entries.push(entry);
        spans.push(span(lines[i]));
    }
    (Entries { values: entries, spans }, findings)
}

/// Record lines (`check --ledger`): every well-formed object, with the line it
/// came from; a malformed line is a finding and the read continues.
pub fn read_record_lines(bytes: &[u8]) -> (Vec<Value>, Vec<Finding>, Vec<usize>) {
    let (mut records, mut findings, mut lines) = (Vec::new(), Vec::new(), Vec::new());
    for (i, raw) in source_lines(bytes).into_iter().enumerate() {
        let n = i + 1;
        if is_blank(raw) {
            findings.push(error(Code::E001, None, &format!(
                "line {n} is blank — the record-line format is one JSON object per line, with no blank lines (format-v2.md, pc-5127)"
            )));
            continue;
        }
        let Ok(text) = std::str::from_utf8(raw) else {
            findings.push(error(Code::E001, None, &format!(
                "line {n} is not valid UTF-8 — the record-line format is one UTF-8 JSON object per line (pc-34b5)"
            )));
            continue;
        };
        match parse(text) {
            Err(e) => findings.push(error(Code::E001, None, &format!("line {n} does not parse: {e}"))),
            Ok(v @ Value::Object(_)) => {
                records.push(v);
                lines.push(n);
            }
            Ok(_) => findings.push(error(Code::E001, None, &format!("line {n} is not an object"))),
        }
    }
    (records, findings, lines)
}

/// A touched field's value, where ABSENT AND NULL ARE ONE STATE — as the
/// reference reads them. Without this, an importer that omits edge keys would
/// have every edge it never wrote counted as changed.
fn field_get<'a>(rec: &'a Pairs, path: &str) -> Option<&'a Value> {
    let v = match path.split_once('.') {
        None => get(rec, path),
        Some((outer, inner)) => get(rec, outer).and_then(as_obj).and_then(|o| get(o, inner)),
    };
    v.filter(|v| **v != Value::Null)
}

/// Two field values are the same value when their canonical forms are: by
/// type as well as value (`1` and `true` differ — distinct variants), and
/// whatever an object's key order, which the derived `Value` equality does
/// not ignore. Compared in place: serializing both sides to compare them cost
/// every entry a canonical pass over each field, bodies included.
fn same_value(a: Option<&Value>, b: Option<&Value>) -> bool {
    fn same(a: &Value, b: &Value) -> bool {
        match (a, b) {
            (Value::Object(x), Value::Object(y)) => {
                // Keys are unique (the parser refuses duplicates), so equal
                // lengths and every key of one matching in the other suffice.
                x.len() == y.len() && x.iter().all(|(k, v)| get(y, k).is_some_and(|w| same(v, w)))
            }
            (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(v, w)| same(v, w)),
            _ => a == b,
        }
    }
    match (a, b) {
        (Some(a), Some(b)) => same(a, b),
        (a, b) => a.is_none() && b.is_none(),
    }
}

/// The field-set one revision changed against the head it was written over —
/// `touched`, derived and never authored (format-v2.md 4.1).
pub fn diff_fields(before: Option<&Pairs>, after: &Pairs) -> Vec<String> {
    let Some(before) = before else { return Vec::new() };
    let fields: Vec<&str> = TOUCHED_FIELDS.iter().chain(TOUCHED_EDGE_FIELDS).copied().collect();
    let mut changed: BTreeSet<String> = fields
        .iter()
        .filter(|f| !same_value(field_get(before, f), field_get(after, f)))
        .map(|f| f.to_string())
        .collect();
    for (k, _) in before.iter().chain(after.iter()) {
        if PER_REVISION_FIELDS.contains(&k.as_str()) || fields.contains(&k.as_str()) {
            continue;
        }
        if !same_value(get(before, k), get(after, k)) {
            changed.insert(k.clone());
        }
    }
    changed.into_iter().collect()
}

fn is_terminal(status: Option<&str>) -> bool {
    status.is_some_and(|s| TERMINAL_STATUSES.contains(&s))
}

fn list_targets<'a>(edges: &'a Pairs, key: &str) -> Vec<&'a str> {
    match get(edges, key) {
        Some(Value::Array(items)) => items.iter().filter_map(Value::as_str).collect(),
        _ => Vec::new(),
    }
}

fn edges_of(rec: &Pairs) -> &Pairs {
    get(rec, "edges").and_then(as_obj).unwrap_or(&[])
}

/// Heads in first-seen order: for each id, the first record carrying its
/// highest revision. The ORDER is load-bearing for E009's lineage grouping.
fn resolve_heads<'a>(sound: &[&'a Pairs]) -> Vec<(String, &'a Pairs)> {
    let mut order: Vec<String> = Vec::new();
    let mut best: HashMap<String, (i64, &'a Pairs)> = HashMap::new();
    for rec in sound {
        let (Some(id), Some(rev)) = (get_str(rec, "id"), strict_int(get(rec, "rev"))) else { continue };
        match best.get(id) {
            None => {
                order.push(id.to_string());
                best.insert(id.to_string(), (rev, rec));
            }
            Some((r, _)) if rev > *r => {
                best.insert(id.to_string(), (rev, rec));
            }
            _ => {}
        }
    }
    order.into_iter().map(|id| { let rec = best[&id].1; (id, rec) }).collect()
}

fn local_shaped(token: &str) -> bool {
    token.starts_with('/') || token.starts_with('~') || token == ".." || token.starts_with("../")
        || token.contains("/../") || token.ends_with("/..")
}

/// Cycles over the blocks/parent/retires graph, found by an iterative DFS that
/// resumes each node's neighbour list where it left off, from sorted roots.
/// The heads as the graph checks (E004, E009, E012, E016) read them: each
/// at its position in first-seen order, with its edges to other heads as
/// positions. Built once and shared by the four — none of them re-keys the
/// heads by id, or copies an id to walk the graph.
pub(crate) struct Graph<'a> {
    heads: &'a [(&'a str, &'a Pairs)],
    status: Vec<Option<&'a str>>,
    decisions: Vec<u32>,
    /// Each id's position — shared with the graph this one was derived
    /// from, plus the one head it appended, if any.
    at: std::borrow::Cow<'a, HashMap<&'a str, u32>>,
    appended: Option<(&'a str, u32)>,
    /// Each head's targets that are heads, as E004 walks them: `blocks`,
    /// then `retires`, then `parent`. Head i's run starts at `marks[i][0]`,
    /// its `retires` at `marks[i][1]` and its `parent` at `marks[i][2]`; it
    /// ends where head i+1's begins.
    walk: Vec<u32>,
    marks: Vec<[u32; 3]>,
}

impl<'a> Graph<'a> {
    pub(crate) fn new(heads: &'a [(&'a str, &'a Pairs)]) -> Graph<'a> {
        let at: HashMap<&'a str, u32> = heads.iter().enumerate().map(|(i, (k, _))| (*k, position(i))).collect();
        Graph::over(heads, std::borrow::Cow::Owned(at), None)
    }

    /// The graph of `heads`: these heads with one replaced where it stands,
    /// or one appended. Every other head keeps its position, so the id
    /// table is this one's.
    pub(crate) fn next<'b>(&'b self, heads: &'b [(&'b str, &'b Pairs)]) -> Graph<'b> {
        debug_assert!(heads.len() == self.heads.len() || heads.len() == self.heads.len() + 1);
        let appended = heads.get(self.heads.len()).map(|(k, _)| (*k, position(self.heads.len())));
        Graph::over(heads, std::borrow::Cow::Borrowed(&*self.at), appended)
    }

    /// Where the head `id` stands, if it is one.
    pub(crate) fn find(&self, id: &str) -> Option<u32> {
        self.at.get(id).copied().or_else(|| self.appended.filter(|(k, _)| *k == id).map(|(_, i)| i))
    }

    fn over(heads: &'a [(&'a str, &'a Pairs)], at: std::borrow::Cow<'a, HashMap<&'a str, u32>>, appended: Option<(&'a str, u32)>) -> Graph<'a> {
        let status = heads.iter().map(|(_, h)| get_str(h, "status")).collect();
        let decisions = (0..position(heads.len())).filter(|&i| get_str(heads[i as usize].1, "type") == Some("decision")).collect();
        let (mut walk, mut marks) = (Vec::new(), Vec::with_capacity(heads.len() + 1));
        let mut g = Graph { heads, status, decisions, at, appended, walk: Vec::new(), marks: Vec::new() };
        let resolve = |t: &str| g.find(t);
        for (_, h) in heads {
            let edges = edges_of(h);
            let b = position(walk.len());
            walk.extend(list_iter(edges, "blocks").filter_map(resolve));
            let r = position(walk.len());
            walk.extend(list_iter(edges, "retires").filter_map(resolve));
            let p = position(walk.len());
            walk.extend(get_str(edges, "parent").and_then(resolve));
            marks.push([b, r, p]);
        }
        marks.push([position(walk.len()); 3]);
        (g.walk, g.marks) = (walk, marks);
        g
    }

    fn id(&self, i: u32) -> &'a str {
        self.heads[i as usize].0
    }

    fn terminal(&self, i: u32) -> bool {
        is_terminal(self.status[i as usize])
    }

    fn run(&self, i: u32, from: usize, to: usize) -> &[u32] {
        let (m, next) = (self.marks[i as usize], self.marks[i as usize + 1]);
        let end = if to == 3 { next[0] } else { m[to] };
        &self.walk[m[from] as usize..end as usize]
    }

    fn blocks(&self, i: u32) -> &[u32] {
        self.run(i, 0, 1)
    }

    fn retires(&self, i: u32) -> &[u32] {
        self.run(i, 1, 2)
    }

    /// Every edge E004 follows out of head i, in the order it follows them.
    fn out(&self, i: u32) -> &[u32] {
        self.run(i, 0, 3)
    }

    fn len(&self) -> u32 {
        position(self.heads.len())
    }

    /// Pairs sorted by their ids, first then second, each pair once.
    fn by_id(&self, mut pairs: Vec<(u32, u32)>) -> Vec<(u32, u32)> {
        pairs.sort_unstable_by(|a, b| self.id(a.0).cmp(self.id(b.0)).then_with(|| self.id(a.1).cmp(self.id(b.1))));
        pairs.dedup();
        pairs
    }
}

/// A head's position. A timeline has fewer than 2^32 heads.
fn position(i: usize) -> u32 {
    u32::try_from(i).expect("fewer than 2^32 heads")
}

fn list_iter<'a>(edges: &'a Pairs, key: &str) -> impl Iterator<Item = &'a str> {
    match get(edges, key) {
        Some(Value::Array(items)) => items.as_slice(),
        _ => &[],
    }
    .iter()
    .filter_map(Value::as_str)
}

/// Each head on a cycle, mapped to its whole cyclic component (pc-c69d): the
/// strongly connected components of size two or more, and any head with an
/// edge to itself. One DFS reports one cycle per back edge it meets, so a
/// second cycle through the same component can go unreported; the component
/// names every head on any cycle without enumerating cycles, whose number
/// can be exponential. Tarjan's algorithm, iterative, as the cycle walk is.
fn cyclic_components(g: &Graph) -> Vec<Option<Vec<u32>>> {
    const NONE: u32 = u32::MAX;
    let n = g.len() as usize;
    let (mut index, mut low) = (vec![NONE; n], vec![0u32; n]);
    let mut on_stack = vec![false; n];
    let (mut stack, mut work): (Vec<u32>, Vec<(u32, usize)>) = (Vec::new(), Vec::new());
    let mut next = 0u32;
    let mut components: Vec<Option<Vec<u32>>> = vec![None; n];
    let mut roots: Vec<u32> = (0..g.len()).collect();
    roots.sort_unstable_by_key(|&i| g.id(i));
    for root in roots {
        if index[root as usize] != NONE {
            continue;
        }
        index[root as usize] = next;
        low[root as usize] = next;
        next += 1;
        stack.push(root);
        on_stack[root as usize] = true;
        work.push((root, 0));
        while let Some(&(node, from)) = work.last() {
            let out = g.out(node);
            let mut i = from;
            let mut descended = false;
            while i < out.len() {
                let nb = out[i];
                i += 1;
                if index[nb as usize] == NONE {
                    index[nb as usize] = next;
                    low[nb as usize] = next;
                    next += 1;
                    stack.push(nb);
                    on_stack[nb as usize] = true;
                    work.last_mut().expect("frame").1 = i;
                    work.push((nb, 0));
                    descended = true;
                    break;
                }
                if on_stack[nb as usize] {
                    low[node as usize] = low[node as usize].min(index[nb as usize]);
                }
            }
            if descended {
                continue;
            }
            work.pop();
            if let Some(&(parent, _)) = work.last() {
                low[parent as usize] = low[parent as usize].min(low[node as usize]);
            }
            if low[node as usize] == index[node as usize] {
                let mut members = Vec::new();
                loop {
                    let w = stack.pop().expect("tarjan stack");
                    on_stack[w as usize] = false;
                    members.push(w);
                    if w == node {
                        break;
                    }
                }
                if members.len() > 1 || out.contains(&node) {
                    for &w in &members {
                        components[w as usize] = Some(members.clone());
                    }
                }
            }
        }
    }
    components
}

/// The cycles a depth-first search finds, from roots in id order, each set
/// of nodes once — as the reference's search finds them, in that order.
/// A root with no edge out finishes at once and changes nothing, so only
/// the heads with an edge out are roots.
fn cycles(g: &Graph) -> Vec<Vec<u32>> {
    const OFF: u32 = u32::MAX;
    let n = g.len() as usize;
    let mut roots: Vec<u32> = (0..g.len()).filter(|&i| !g.out(i).is_empty()).collect();
    roots.sort_unstable_by_key(|&i| g.id(i));
    let (mut state, mut on_path) = (vec![0u8; n], vec![OFF; n]);
    let (mut path, mut stack): (Vec<u32>, Vec<(u32, usize)>) = (Vec::new(), Vec::new());
    let mut seen: HashSet<Vec<u32>> = HashSet::new();
    let mut found: Vec<Vec<u32>> = Vec::new();
    for root in roots {
        if state[root as usize] != 0 {
            continue;
        }
        state[root as usize] = 1;
        on_path[root as usize] = 0;
        path.push(root);
        stack.push((root, 0));
        while let Some(&(node, from)) = stack.last() {
            let out = g.out(node);
            let mut i = from;
            let mut descended = false;
            while i < out.len() {
                let nb = out[i];
                i += 1;
                match state[nb as usize] {
                    1 => {
                        let mut cycle: Vec<u32> = path[on_path[nb as usize] as usize..].to_vec();
                        cycle.push(nb);
                        let mut key = cycle.clone();
                        key.sort_unstable();
                        key.dedup();
                        if seen.insert(key) {
                            found.push(cycle);
                        }
                    }
                    0 => {
                        state[nb as usize] = 1;
                        on_path[nb as usize] = position(path.len());
                        path.push(nb);
                        stack.last_mut().expect("frame").1 = i;
                        stack.push((nb, 0));
                        descended = true;
                        break;
                    }
                    _ => {}
                }
            }
            if !descended {
                state[node as usize] = 2;
                on_path[node as usize] = OFF;
                stack.pop();
                path.pop();
            }
        }
    }
    found
}

/// Each entry's record, borrowed: the view every checker and query reads.
/// Records are never copied out of the timeline to be read.
pub fn records_of(entries: &[Value]) -> Vec<&Value> {
    entries.iter().filter_map(|e| e.get("rec")).collect()
}

/// Every record-level and cross-record invariant (E001–E012, E016, E017).
pub fn run_checks(records: &[&Value], parse_findings: Vec<Finding>, cfg: &Config, record_lines: Option<&[usize]>) -> Vec<Finding> {
    let mut findings = parse_findings;
    let statuses = cfg.allowed_statuses();
    let types = cfg.allowed_types();
    for (i, rec) in records.iter().enumerate() {
        if let Some(obj) = as_obj(rec) {
            let line = record_lines.and_then(|l| l.get(i).copied());
            findings.extend(validate_record(obj, &statuses, &types, line));
        }
    }
    let sound: Vec<&Pairs> = records.iter().filter(|r| record_is_sound(r)).filter_map(|r| as_obj(r)).collect();

    // E002 / E008 index every record with a string id and an integer rev,
    // sound or not: a duplicate is corruption whatever else is wrong.
    let mut dup: BTreeMap<String, BTreeMap<i64, Vec<&Pairs>>> = BTreeMap::new();
    for rec in records.iter().filter_map(|r| as_obj(r)) {
        if let (Some(id), Some(rev)) = (get_str(rec, "id"), strict_int(get(rec, "rev"))) {
            dup.entry(id.to_string()).or_default().entry(rev).or_default().push(rec);
        }
    }
    for (rid, revs) in &dup {
        for (rev, recs) in revs {
            if recs.len() > 1 {
                let distinct: HashSet<String> = recs.iter().map(|r| crate::canonical::canonical_object(r)).collect();
                findings.push(error(Code::E002, Some(rid), &format!(
                    "rev {rev} appears {}x in the timeline ({} content) — a log the CAS admitted cannot contain this, so the log is corrupt; see E013",
                    recs.len(), if distinct.len() == 1 { "identical" } else { "divergent" }
                )));
            }
        }
    }
    for (rid, revs) in &dup {
        let present: Vec<i64> = revs.keys().copied().collect();
        let (lo, hi) = (present[0], present[present.len() - 1]);
        let gaps: Vec<Value> = (lo..=hi).filter(|r| !revs.contains_key(r)).map(Value::Int).collect();
        if !gaps.is_empty() {
            findings.push(error(Code::E008, Some(rid), &format!("revision gap(s): {}", capped_array(&Value::Array(gaps), MESSAGE_VALUE_CAP))));
        }
    }

    // E005 over each record's sound revisions, in rev order.
    let mut groups: BTreeMap<String, BTreeMap<i64, &Pairs>> = BTreeMap::new();
    for rec in &sound {
        let id = get_str(rec, "id").expect("sound");
        let rev = strict_int(get(rec, "rev")).expect("sound");
        groups.entry(id.to_string()).or_default().entry(rev).or_insert(rec);
    }
    for (rid, revs) in &groups {
        let ordered: Vec<&Pairs> = revs.values().copied().collect();
        for pair in ordered.windows(2) {
            findings.extend(e005(rid, pair[0], pair[1], statuses));
        }
    }

    let heads_ordered = resolve_heads(&sound);
    let ordered: Vec<(&str, &Pairs)> = heads_ordered.iter().map(|(k, v)| (k.as_str(), *v)).collect();
    let heads: BTreeMap<&str, &Pairs> = ordered.iter().copied().collect();
    let mut known: HashSet<&str> = groups.keys().map(String::as_str).collect();
    let planned = cfg.planned_ids();
    known.extend(planned.iter().map(String::as_str));
    let declared = cfg.resolvers();

    // Each code in turn, over the heads in id order — the order a reader of
    // `check` meets them in.
    for (rid, head) in &heads {
        findings.extend(e003(rid, head, |t| known.contains(t)));
    }
    let graph = Graph::new(&ordered);
    findings.extend(e004(&graph));
    for (rid, head) in &heads {
        findings.extend(e017(rid, head));
    }
    for (rid, head) in &heads {
        findings.extend(e006(rid, head));
    }
    for (rid, head) in &heads {
        findings.extend(e007(rid, head, cfg));
    }
    for (rid, head) in &heads {
        findings.extend(e007_local(rid, head, cfg));
    }
    for (rid, head) in &heads {
        findings.extend(e011(rid, head, declared));
    }
    findings.extend(e012(&graph));
    findings.extend(e016(&graph));
    findings.extend(e009(&graph));
    findings
}

/// E005 for one pair of consecutive revisions: terminal is final.
pub(crate) fn e005(rid: &str, prev: &Pairs, next: &Pairs, statuses: &[String]) -> Option<Finding> {
    let (ps, ns) = (get_str(prev, "status")?, get_str(next, "status")?);
    (statuses.iter().any(|s| s == ps) && statuses.iter().any(|s| s == ns) && TERMINAL_STATUSES.contains(&ps) && ns != ps).then(|| {
        error(Code::E005, Some(rid), &format!(
            "illegal transition {} -> {} at rev {} (terminal is final; reopen = new record with discovered_from)",
            safe_text(ps, MESSAGE_ITEM_CAP), safe_text(ns, MESSAGE_ITEM_CAP),
            strict_int(get(next, "rev")).unwrap_or(0)
        ))
    })
}

pub(crate) fn e003(rid: &str, head: &Pairs, known: impl Fn(&str) -> bool) -> Vec<Finding> {
    let edges = edges_of(head);
    let mut targets: Vec<&str> = LIST_EDGES.iter().flat_map(|k| list_targets(edges, k)).collect();
    targets.extend(SCALAR_EDGES.iter().filter_map(|k| get_str(edges, k)));
    targets
        .into_iter()
        .filter(|t| !known(t))
        .map(|t| error(Code::E003, Some(rid), &format!(
            "edge target {} does not exist and is not declared planned",
            capped_value(&Value::Str(t.to_string()), MESSAGE_VALUE_CAP)
        )))
        .collect()
}

pub(crate) fn e004(g: &Graph) -> Vec<Finding> {
    let components = cyclic_components(g);
    cycles(g)
        .into_iter()
        .map(|cycle| {
            let names: Vec<String> = cycle.iter().map(|&i| g.id(i).to_string()).collect();
            // The finding's group is the cycle's whole cyclic component
            // (pc-c69d): the message names any member the witness misses,
            // and the write gate reads the component.
            let mut component: Vec<String> = match &components[cycle[0] as usize] {
                Some(members) => members.iter().map(|&i| g.id(i).to_string()).collect(),
                None => names.clone(),
            };
            component.sort();
            component.dedup();
            let mut extra: Vec<String> = component.iter().filter(|m| !names.contains(m)).cloned().collect();
            extra.sort();
            let also = if extra.is_empty() {
                String::new()
            } else {
                format!("; the same cyclic component also holds {}", capped_seq(&extra, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", "))
            };
            error(Code::E004, Some(&names[0]), &format!(
                "cycle in the blocks/parent/retires graph: {}{also}",
                capped_seq(&names, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, " -> ")
            ))
            .grouped(&component, None)
        })
        .collect()
}

pub(crate) fn e017(rid: &str, head: &Pairs) -> Vec<Finding> {
    let edges = edges_of(head);
    let mut selfish: Vec<&str> = LIST_EDGES.iter().copied().filter(|k| list_targets(edges, k).contains(&rid)).collect();
    selfish.sort();
    selfish.extend(SCALAR_EDGES.iter().copied().filter(|k| get_str(edges, k) == Some(rid)));
    selfish
        .into_iter()
        .map(|key| error(Code::E017, Some(rid), &format!(
            "self-edge: edges.{key} names the record itself — no edge relation is reflexive (v1.3's ban, restored at v2.5)"
        )))
        .collect()
}

pub(crate) fn e006(rid: &str, head: &Pairs) -> Option<Finding> {
    (is_terminal(get_str(head, "status")) && !get_str(head, "disposition").is_some_and(|d| !py_strip(d).is_empty()))
        .then(|| error(Code::E006, Some(rid), "terminal status without disposition"))
}

pub(crate) fn e007(rid: &str, head: &Pairs, cfg: &Config) -> Option<Finding> {
    if get_str(head, "type") != Some("defect") || get_str(head, "status") != Some("done") {
        return None;
    }
    let ev = get(head, "evidence");
    (parse_foreign_ref(ev).is_none() && evidence_command_head(ev, cfg).is_none()).then(|| error(Code::E007, Some(rid),
        "done defect requires executable-shaped evidence or a <scheme>:<id> reference (prose is not evidence). A path-shaped command head passes on shape; a bare one must be declared in .pecia/config.yaml as `extra_evidence_commands: [<name>]`"))
}

pub(crate) fn e007_local(rid: &str, head: &Pairs, cfg: &Config) -> Option<Finding> {
    let ev = get(head, "evidence");
    evidence_command_head(ev, cfg)?;
    let tokens = shlex_split(ev.and_then(Value::as_str).unwrap_or("")).unwrap_or_default();
    // Each argument beside its position (v3.3, reversing pc-2706's
    // withholding): the argument is what the reader changes.
    let local: Vec<String> = tokens
        .iter()
        .enumerate()
        .skip(1)
        .filter(|(_, a)| local_shaped(a) || a.split_once('=').is_some_and(|(_, v)| local_shaped(v)))
        .map(|(i, a)| format!("{i}: {a}"))
        .collect();
    (!local.is_empty()).then(|| warning(Code::E007, Some(rid), &format!(
        "machine-local evidence argument(s) {} — an absolute or parent-escaping path resolves only on the host that wrote it, so the command cannot run anywhere else (v2.5, pc-0a66)",
        capped_seq(&local, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ")
    )))
}

pub(crate) fn e011(rid: &str, head: &Pairs, declared: &HashMap<String, String>) -> Vec<Finding> {
    ["evidence", "context"]
        .into_iter()
        .filter_map(|field| {
            let (scheme, _) = parse_foreign_ref(get(head, field))?;
            (!declared.contains_key(scheme)).then(|| error(Code::E011, Some(rid), &format!(
                "{field} references undeclared scheme {} — declare it in .pecia/config.yaml as `resolvers: [{}=<verifier command>]`",
                capped_value(&Value::Str(scheme.to_string()), MESSAGE_VALUE_CAP),
                safe_text(scheme, MESSAGE_ITEM_CAP)
            )))
        })
        .collect()
}

/// Runs of pairs sharing their first member, in order.
fn grouped(pairs: &[(u32, u32)]) -> impl Iterator<Item = (u32, Vec<u32>)> + '_ {
    pairs.chunk_by(|a, b| a.0 == b.0).map(|run| (run[0].0, run.iter().map(|p| p.1).collect()))
}

/// E012: a record every retirer of which is terminal, while it is not.
pub(crate) fn e012(g: &Graph) -> Vec<Finding> {
    let claims: Vec<(u32, u32)> = (0..g.len()).flat_map(|c| g.retires(c).iter().map(move |&t| (t, c))).collect();
    let mut out = Vec::new();
    for (target, claimants) in grouped(&g.by_id(claims)) {
        if g.terminal(target) || claimants.iter().any(|&c| !g.terminal(c)) {
            continue;
        }
        let names: Vec<String> = claimants.iter().map(|&c| g.id(c).to_string()).collect();
        out.push(error(Code::E012, Some(g.id(target)), &format!(
            "non-terminal, but every record claiming to retire it is terminal ({}) — close it, or drop the retires claim that no longer holds",
            capped_seq(&names, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ")
        )).grouped(&names, Some(g.id(target))));
    }
    out
}

/// E016: terminal while a non-terminal record still blocks it. Only a live
/// record holds anything, so the holders come from the live heads alone.
pub(crate) fn e016(g: &Graph) -> Vec<Finding> {
    let held: Vec<(u32, u32)> = (0..g.len())
        .filter(|&o| !g.terminal(o))
        .flat_map(|o| g.blocks(o).iter().filter(|&&t| g.terminal(t)).map(move |&t| (t, o)))
        .collect();
    grouped(&g.by_id(held))
        .map(|(rid, holders)| {
            let holders: Vec<String> = holders.iter().map(|&o| g.id(o).to_string()).collect();
            error(Code::E016, Some(g.id(rid)), &format!(
                "terminal, but {} still block(s) it — drop the `blocks` claim that no longer holds, or close the blocker(s); this record cannot be reopened (E005: terminal is final)",
                capped_seq(&holders, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ")
            )).grouped(&holders, Some(g.id(rid)))
        })
        .collect()
}

/// E009: a decision lineage (joined by `supersedes`) with more than one
/// active head. Lineages are visited in first-seen order of their members.
pub(crate) fn e009(g: &Graph) -> Vec<Finding> {
    const NONE: u32 = u32::MAX;
    let decisions = &g.decisions;
    // Each decision's place among the decisions, and a union-find over them:
    // which member a lineage is rooted at never shows, only who is in it.
    let mut place = vec![NONE; g.len() as usize];
    for (k, &d) in decisions.iter().enumerate() {
        place[d as usize] = position(k);
    }
    let mut parent: Vec<u32> = (0..position(decisions.len())).collect();
    fn find(p: &mut [u32], mut x: u32) -> u32 {
        while p[x as usize] != x {
            p[x as usize] = p[p[x as usize] as usize];
            x = p[x as usize];
        }
        x
    }
    for (k, &d) in decisions.iter().enumerate() {
        let target = get_str(edges_of(g.heads[d as usize].1), "supersedes").and_then(|t| g.find(t)).map(|t| place[t as usize]);
        if let Some(t) = target.filter(|&t| t != NONE) {
            let (a, b) = (find(&mut parent, position(k)), find(&mut parent, t));
            parent[a as usize] = b;
        }
    }
    let mut lineages: Vec<Vec<u32>> = Vec::new();
    let mut lineage_of = vec![NONE; decisions.len()];
    for (k, &d) in decisions.iter().enumerate() {
        let root = find(&mut parent, position(k)) as usize;
        if lineage_of[root] == NONE {
            lineage_of[root] = position(lineages.len());
            lineages.push(Vec::new());
        }
        lineages[lineage_of[root] as usize].push(d);
    }
    let mut findings = Vec::new();
    for members in &lineages {
        let mut active: Vec<String> = members
            .iter()
            .filter(|&&m| !matches!(g.status[m as usize], Some("superseded") | Some("dropped")))
            .map(|&m| g.id(m).to_string())
            .collect();
        active.sort();
        if active.len() > 1 {
            findings.push(error(Code::E009, Some(&active[0]), &format!(
                "decision lineage has {} active heads: {}",
                active.len(),
                capped_seq(&active, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ")
            )).grouped(&active, None));
        }
    }
    findings
}

/// The projection and its witness, as the caller found them on disk.
pub struct SnapshotFiles<'a> {
    pub projection: Option<&'a [u8]>,
    pub head: Option<&'a [u8]>,
    pub projection_display: &'a str,
    pub head_display: &'a str,
    /// The log's high-water mark (v3.3), as its file holds it.
    pub mark: Option<&'a [u8]>,
    pub mark_display: &'a str,
}

/// What to do about an E019, said the same way by every command that meets one.
pub const MARK_REMEDY: &str = "Recover the log first: `pecia sync` restores entries that were published. Entries never published are not recoverable from here, and removing the mark is the deliberate act of accepting that";

/// The log's high-water mark (v3.3, pc-25cca4980c47): one line, the newest
/// entry's seq and hash, or None for an empty timeline, which marks nothing.
pub fn mark_text(entries: &[Value]) -> Option<String> {
    Some(format!("{} {}\n", entries.len(), entry_hash(entries.last()?)))
}

/// A mark as its file records it: (seq, hash), or None when it does not
/// parse. A seq too large to hold is too large for any log.
fn parse_mark(bytes: &[u8]) -> Option<(u128, &str)> {
    let text = std::str::from_utf8(bytes).ok()?;
    let (seq, hash) = py_strip(text).split_once(' ')?;
    let digits = !seq.is_empty() && !seq.starts_with('0') && seq.bytes().all(|b| b.is_ascii_digit());
    let hex = hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    (digits && hex).then(|| (seq.parse().unwrap_or(u128::MAX), hash))
}

/// E019: why `entries` end before the high-water mark, or None — also None
/// when there is no mark, which makes no claim. Every write moves the mark
/// to the entry it appended, so a log that no longer holds the marked entry
/// lost or replaced what this store wrote.
pub fn mark_violation(entries: &[Value], mark: Option<&[u8]>, shown: &str) -> Option<String> {
    let mark = mark?;
    let Some((seq, hash)) = parse_mark(mark) else {
        return Some(format!("the log's high-water mark {shown} does not parse — it records the newest entry's seq and hash, and a mark that says neither vouches for nothing"));
    };
    let len = entries.len() as u128;
    if seq > len {
        let missing = seq - len;
        let (entry, verb) = if missing == 1 { ("entry", "is") } else { ("entries", "are") };
        return Some(format!(
            "the log ends at seq {len}, but its high-water mark {shown} records seq {seq}: {missing} {entry} this store wrote {verb} missing from the log's end — the log looks suffix-truncated"
        ));
    }
    (entry_hash(&entries[seq as usize - 1]) != hash)
        .then(|| format!("the log's entry {seq} is not the one its high-water mark {shown} records — the log was rewritten at or below the mark"))
}

/// Whether `entries` hold the entry the mark records, at its seq: the
/// timeline a recovery restores.
pub fn holds_marked_entry(entries: &[Value], mark: Option<&[u8]>) -> bool {
    mark.and_then(parse_mark).is_some_and(|(seq, hash)| {
        seq <= entries.len() as u128 && entry_hash(&entries[seq as usize - 1]) == hash
    })
}

fn bytes_blank(b: &[u8]) -> bool {
    b.iter().all(|c| matches!(c, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c))
}

/// The timeline invariants: the per-id CAS and the derived `touched` (E014),
/// and the projection bound to the log by its witness (E015).
/// `snap` of None skips the projection comparison (the reference's
/// `check_snapshot=False`), leaving the per-entry invariants.
pub fn timeline_checks(entries: &[Value], snap: Option<&SnapshotFiles>, log_complete: bool) -> Vec<Finding> {
    let mut findings = Vec::new();
    let mut seen: HashMap<String, &Pairs> = HashMap::new();
    for e in entries {
        let Some(obj) = as_obj(e) else { continue };
        let Some(rec) = get(obj, "rec").and_then(as_obj) else { continue };
        let rid = get_str(rec, "id");
        let seq = strict_int(get(obj, "seq")).unwrap_or(0);
        let before = rid.and_then(|r| seen.get(r).copied());
        let prev_rev = before.and_then(|b| get(b, "rev"));
        let expected: Option<i64> = match (before, strict_int(prev_rev)) {
            (None, _) => Some(1),
            (Some(_), Some(p)) => Some(p + 1),
            (Some(_), None) => {
                findings.push(error(Code::E014, rid, &format!(
                    "seq {seq}: rev {} follows a revision whose own rev {} is not an integer (its E001 is reported separately) — contiguity cannot be verified past malformed history",
                    render_opt(get(rec, "rev")), render_opt(prev_rev)
                )));
                None
            }
        };
        if let Some(exp) = expected {
            if get(rec, "rev") != Some(&Value::Int(exp)) {
                let follows = match prev_rev {
                    None if before.is_none() => "no head".to_string(),
                    other => render_opt(other),
                };
                findings.push(error(Code::E014, rid, &format!(
                    "seq {seq}: rev {} does not follow {follows} (expected {exp}) — the CAS could not have admitted this",
                    render_opt(get(rec, "rev"))
                )));
            }
        }
        let expected_touched = diff_fields(before, rec);
        let declared = get(obj, "touched");
        let declared_list: Option<Vec<&str>> = match declared {
            Some(Value::Array(items)) => items.iter().map(Value::as_str).collect(),
            _ => None,
        };
        match declared_list {
            None => findings.push(error(Code::E014, rid, &format!(
                "seq {seq}: touched {} is not a list of field-name strings — the derived field-set is a JSON array of field names (format-v2.md 4.1)",
                capped_array(declared.unwrap_or(&Value::Null), MESSAGE_VALUE_CAP)
            ))),
            Some(list) if list != expected_touched => {
                let shown = capped_array(declared.expect("list"), MESSAGE_VALUE_CAP);
                let want = capped_array(&Value::Array(expected_touched.iter().cloned().map(Value::Str).collect()), MESSAGE_VALUE_CAP);
                let mut sorted = list.clone();
                sorted.sort();
                if sorted == expected_touched {
                    findings.push(error(Code::E014, rid, &format!(
                        "seq {seq}: touched {shown} carries the correct field-set in non-canonical order — the derived array is serialized sorted (format-v2.md 4.1, v2.9); reorder to {want}"
                    )));
                } else {
                    findings.push(error(Code::E014, rid, &format!(
                        "seq {seq}: touched {shown} does not match the recomputed diff {want} — `touched` is derived by the tool and never accepted from a writer (format-v2.md 4.1)"
                    )));
                }
            }
            Some(_) => {}
        }
        if let Some(r) = rid {
            seen.insert(r.to_string(), rec);
        }
    }

    let Some(snap) = snap else { return findings };
    let (snap_path, head_path) = (snap.projection_display, snap.head_display);
    if !log_complete {
        if snap.head.is_some() || snap.projection.is_some_and(|b| !bytes_blank(b)) {
            findings.push(warning(Code::E015, None, &format!(
                "{snap_path} was NOT compared with the timeline: the log stops at an error before its end (that finding names the line), so the entries read are a trusted prefix rather than the chain, and a witness naming a later head cannot be told from a forked one. The projection and its witness are untouched and unjudged — repair the log and check again. `pecia snapshot` is NOT the remedy here: it reads the same log and refuses on the same error"
            )));
        }
        return findings;
    }
    // E019 (v3.3): the log's own end, against the mark every write moves —
    // over a log that read whole, which a partial read has returned above.
    if let Some(gone) = mark_violation(entries, snap.mark, snap.mark_display) {
        findings.push(error(Code::E019, None, &format!("{gone}. {MARK_REMEDY}")));
    }
    let mut snap_text: Option<&str> = None;
    if let Some(bytes) = snap.projection {
        match std::str::from_utf8(bytes) {
            Ok(t) => snap_text = Some(t),
            Err(e) => findings.push(error(Code::E015, None, &format!(
                "{snap_path} is not valid UTF-8 ({e}) — the snapshot was corrupted or replaced with non-text bytes and cannot match the timeline at any head. Regenerate it with `pecia snapshot`; never edit it"
            ))),
        }
    }
    if let Some(head_bytes) = snap.head {
        let recorded = match std::str::from_utf8(head_bytes) {
            Ok(t) => py_strip(t),
            Err(e) => {
                findings.push(error(Code::E015, None, &format!(
                    "{head_path} is not valid UTF-8 ({e}) — the witness was corrupted or replaced with non-text bytes, so neither staleness nor a fork can be told apart. Regenerate both with `pecia snapshot`; never edit them"
                )));
                return findings;
            }
        };
        let upto: Option<usize> = if recorded.is_empty() {
            Some(0)
        } else {
            // From the END: the recorded head is almost always the last
            // entry, and hashes are unique (every entry carries its seq), so
            // the first match from either end is the same entry.
            match entries.iter().rposition(|e| entry_hash(e) == recorded) {
                Some(i) => Some(i + 1),
                None => {
                    findings.push(error(Code::E015, None, &format!(
                        "{head_path} names a head that is not in this timeline — the snapshot is FORKED, not merely stale (a stale snapshot's head is an ancestor and is clean). Regenerate it with `pecia snapshot`"
                    )));
                    None
                }
            }
        };
        if let (Some(upto), Some(text)) = (upto, snap_text) {
            if !projection_matches(text, &entries[..upto]) {
                findings.push(error(Code::E015, None, &format!(
                    "{snap_path}'s content does not match the timeline at the head it records ({upto} entries) — it was edited or replaced. Regenerate it with `pecia snapshot`; never edit it"
                )));
            }
        }
    } else if snap.projection.is_some_and(|b| !bytes_blank(b)) {
        findings.push(error(Code::E015, None, &format!(
            "{snap_path} carries content but {head_path} is missing — the witness binding the projection to the timeline is gone, so neither staleness nor a fork (nor a truncated log) can be told apart. Regenerate both with `pecia snapshot`"
        )));
    }
    findings
}

/// Whether `text` is exactly the projection of `entries` — each record's
/// canonical line, LF-terminated — compared a record at a time rather than
/// by building the whole expected body.
fn projection_matches(text: &str, entries: &[Value]) -> bool {
    let mut rest = text;
    let mut line = String::new();
    for rec in entries.iter().filter_map(|e| as_obj(e).and_then(|o| get(o, "rec"))) {
        line.clear();
        crate::canonical::write_to(rec, &mut line);
        line.push('\n');
        match rest.strip_prefix(line.as_str()) {
            Some(r) => rest = r,
            None => return false,
        }
    }
    rest.is_empty()
}

/// The chain's head: the hash of the last entry, or None for an empty log.
pub fn log_head_hash(entries: &[Value]) -> Option<String> {
    entries.last().map(entry_hash)
}

/// The projection: each entry's record, canonical, one per line.
pub fn projection_body(entries: &Entries) -> String {
    projection_parts(entries).concat()
}

/// The projection in parts which, joined, are `projection_body` — each part
/// a run of records canonicalized on its own core, so a million records
/// are not one core's work. A record read from the log is part of its line,
/// so where the lines are known each part is allocated once, at the size of
/// its lines: grown instead, parts this large are copied by the kernel,
/// every core contending for it.
pub fn projection_parts(entries: &Entries) -> Vec<String> {
    const PART: usize = 4096;
    let spans = entries.spans();
    crate::par::map(entries.len().div_ceil(PART), |i| {
        let run = i * PART..entries.len().min((i + 1) * PART);
        let room = spans.map_or(0, |s| s[run.clone()].iter().map(|&(_, len)| len as usize + 1).sum());
        let mut out = String::with_capacity(room);
        for r in entries[run].iter().filter_map(|e| e.get("rec")) {
            crate::canonical::write_to(r, &mut out);
            out.push('\n');
        }
        out
    })
}

/// Whether the witness `head` rules the truncation case out by itself: it
/// is absent or empty, or names an entry of the chain. Then the projection
/// need not be read to know `snapshot_truncation_witness` finds nothing.
fn witness_names_chain(entries: &[Value], head: Option<&[u8]>) -> bool {
    let Some(head) = head else { return true };
    let recorded = String::from_utf8_lossy(head);
    let recorded = py_strip(&recorded);
    recorded.is_empty() || entries.iter().rev().any(|e| entry_hash(e) == recorded)
}

/// Why regenerating the projection would destroy the only witness of lost
/// entries, or None. The case: the recorded head is not in the chain, and the
/// projection holds MORE records than the log, agreeing with it over the
/// log's whole length — the log looks suffix-truncated (E015, pc-0ff7).
/// `result` is a timeline about to be written, whose head may legitimately be
/// the recorded one. `marked` says the log's high-water mark file exists: a
/// store with no entries and no mark cannot have lost any.
pub fn snapshot_truncation_witness(entries: &[Value], head: Option<&[u8]>, projection: Option<&[u8]>, marked: bool, result: Option<&[Value]>) -> Option<String> {
    let (head, projection) = (head?, projection?);
    // From the end: the recorded head is almost always the last entry.
    if witness_names_chain(entries, Some(head)) {
        return None;
    }
    let recorded = String::from_utf8_lossy(head);
    let recorded = py_strip(&recorded);
    let mut snap: Vec<Value> = Vec::new();
    for line in source_lines(projection) {
        let Ok(text) = std::str::from_utf8(line) else { return None };
        if py_strip(text).is_empty() {
            continue;
        }
        match parse(text) {
            Ok(v) => snap.push(v),
            // Garbage content is the forged case, and regenerating is its remedy.
            Err(_) => return None,
        }
    }
    let log: Vec<&Value> = entries.iter().filter_map(|e| e.get("rec")).collect();
    if !(snap.len() > log.len() && log.iter().zip(&snap).all(|(a, b)| canonical(a) == canonical(b))) {
        return None;
    }
    if result.is_some_and(|r| r.iter().rev().any(|e| entry_hash(e) == recorded)) {
        return None;
    }
    // Say which loss this can be, and name an exit that works
    // (pc-7e110b7cf434). A clone with no entries and no mark has lost
    // nothing: the snapshot committed here carries entries nobody published.
    // With a log, the same shape is a loss from its end or another clone's
    // unpublished work, and nothing local tells them apart.
    if log.is_empty() && !marked {
        let holder = match result {
            Some(r) => format!("the published timeline it would adopt ({} entries)", r.len()),
            None => "nothing here".to_string(),
        };
        return Some(format!(
            "this clone has no timeline yet, and the snapshot committed here ({} entries) names a head that {holder} does not hold — the commit carries entries that were never published, and the snapshot is their only copy here (E015, pc-0ff7). Have whoever committed them run `pecia publish`, then run `pecia sync`; `pecia migrate` rebuilds from history instead, but only if that rebuild extends the published timeline",
            snap.len()
        ));
    }
    let holders = match result {
        Some(r) => format!("neither the log nor the timeline it would write ({} entries) holds", r.len()),
        None => "the log does not hold".to_string(),
    };
    Some(format!(
        "the snapshot ({} entries) agrees with this log's {} and extends it, and names a head that {holders} — either the log lost entries from its end, or the snapshot was committed from a clone that has not published its later entries; the snapshot is their only copy here (E015, pc-0ff7). If another clone wrote them, have it run `pecia publish`, then run `pecia sync`; if this log lost them, recover it first (`pecia migrate --force` rebuilds it from history and the snapshot)",
        snap.len(), log.len()
    ))
}

/// The snapshot's records chained as their writers chained them, if that
/// chain ends at the head the snapshot records (v3.6, pc-4f84768dbed1).
///
/// A committed snapshot is canonical(rec) of every entry in log order (E015
/// holds it to that), and every other field of an entry is determined: `seq`
/// by position, `prev` by the entry before, `touched` by the diff from the
/// id's head, which no writer may author (§4.1, E014). So chaining the
/// records with the writer's own `Chain` rebuilds the entries byte for byte,
/// and the recorded head is a SHA-256 commitment to the result. A snapshot
/// that was edited, replaced or truncated does not rebuild to its head, and is
/// never trusted.
pub fn snapshot_chain(head: Option<&[u8]>, projection: Option<&[u8]>) -> Option<Entries> {
    let recorded = String::from_utf8_lossy(head?);
    let recorded = py_strip(&recorded);
    if recorded.is_empty() {
        return None;
    }
    let mut chain = crate::write::Chain::new(Vec::new());
    for line in source_lines(projection?) {
        let text = std::str::from_utf8(line).ok()?;
        if py_strip(text).is_empty() {
            continue;
        }
        let rec = parse(text).ok()?;
        if !matches!(rec, Value::Object(_)) {
            return None;
        }
        chain.append(rec);
    }
    (!chain.entries().is_empty() && chain.head_hash() == Some(recorded)).then(|| chain.into_entries())
}

/// Whether `short` is `long`'s first entries, entry for entry.
pub fn chain_prefix(short: &[Value], long: &[Value]) -> bool {
    short.len() <= long.len() && short.iter().zip(long).all(|(a, b)| canonical(a) == canonical(b))
}

/// Why a snapshot whose chain verifies was still not adopted, for a refusal to
/// say: the published timeline has moved on from it.
pub fn copy_parts_note(copy: Option<&[Value]>, local: &[Value], theirs: &[Value]) -> String {
    let Some(copy) = copy else { return String::new() };
    if !chain_prefix(local, copy) || chain_prefix(theirs, copy) || chain_prefix(copy, theirs) {
        return String::new();
    }
    let k = (0..theirs.len()).find(|&i| i >= copy.len() || canonical(&theirs[i]) != canonical(&copy[i])).unwrap_or(theirs.len());
    format!(
        ". The snapshot's own chain does rebuild to its recorded head, but the published timeline parts from it at entry {}: its later entries must be re-chained before they can be published, so this commit's snapshot can never be adopted. Its writer has to sync, publish and commit the snapshot that produces; check out that commit and sync",
        k + 1
    )
}

/// Every error the full checker finds in a timeline — the gate a publish or a
/// re-chain must pass before anything is written.
pub fn timeline_errors(entries: &[Value], cfg: &Config, snap: Option<&SnapshotFiles>) -> Vec<Finding> {
    let records = records_of(entries);
    let lines: Vec<usize> = (1..=records.len()).collect();
    let mut f = run_checks(&records, Vec::new(), cfg, Some(&lines));
    f.extend(timeline_checks(entries, snap, true));
    f.retain(|x| x.severity == Severity::Error);
    f
}

pub fn has_error(findings: &[Finding]) -> bool {
    findings.iter().any(|f| f.severity == Severity::Error)
}
