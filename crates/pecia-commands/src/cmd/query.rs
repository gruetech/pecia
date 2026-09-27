//! `ready`, `blocked`, `next`, `graph`: read-only, JSON (graph can emit
//! Mermaid). Each refuses over a history it cannot schedule against.

use crate::args::Parsed;
use crate::cmd::store_cmds::load_entries_noting_identity;
use crate::ctx::{obj, s, Ctx};
use pecia_core::config::Config;
use pecia_core::query::{compute_blockers, edge_targets, mermaid_id, mermaid_label, mermaid_safe_id_display, ready};
use pecia_core::check::Entries;
use pecia_core::index;
use pecia_core::record::{as_obj, get_str, Pairs};
use pecia_core::text::safe_id;
use pecia_core::write::{is_terminal, project, require_heads};
use pecia_core::Value;
use pecia_store::read_optional;

pub fn config(ctx: &Ctx) -> Result<Config, String> {
    Ok(read_optional(&ctx.store.config_path())?.map(|b| Config::parse(&String::from_utf8_lossy(&b))).unwrap_or_default())
}

/// The heads a query's answer depends on — the only ones it reads. The
/// selection is applied the same way whether the heads come from the index or
/// from a full read, so the two paths hand a query the same heads.
#[derive(Clone, Copy)]
pub enum Select {
    All,
    /// Only a non-terminal record can block, or be ready.
    NonTerminal,
    Kind(&'static str),
}

impl Select {
    fn wants(self, status: Option<&str>, kind: Option<&str>) -> bool {
        match self {
            Select::All => true,
            Select::NonTerminal => !is_terminal(status),
            Select::Kind(k) => kind == Some(k),
        }
    }
}

/// The selected heads, in first-seen order, and how many heads the ledger
/// has; or the refusal to schedule against this history.
pub fn with_heads<T>(ctx: &Ctx, select: Select, f: impl FnOnce(&[(String, &Pairs)], usize, &Config) -> T) -> Result<Result<T, String>, String> {
    if let Some((records, total)) = indexed_heads(ctx, select) {
        let heads: Vec<(String, &Pairs)> = records.iter().filter_map(as_obj).map(|r| (get_str(r, "id").unwrap_or("").to_string(), r)).collect();
        return Ok(Ok(f(&heads, total, &config(ctx)?)));
    }
    let (entries, findings, identity) = load_entries_noting_identity(ctx)?;
    let records = pecia_core::check::records_of(&entries);
    let heads = match require_heads(&records, &findings) {
        Ok(h) => h,
        Err(m) => return Ok(Err(m)),
    };
    // A read that verified the whole chain, and would let any query run, is
    // what the index stands in for until the log changes.
    if let (true, Some(identity)) = (findings.is_empty(), identity) {
        write_index(ctx, &entries, &identity);
    }
    // The query views, exactly as the index would give them: the two paths
    // hand a query the same records, not merely records that agree.
    let total = heads.len();
    let views: Vec<Value> = heads
        .iter()
        .filter(|(_, h)| select.wants(get_str(h, "status"), get_str(h, "type")))
        .map(|(_, h)| Value::Object(index::query_view(h)))
        .collect();
    let chosen: Vec<(String, &Pairs)> = views.iter().filter_map(as_obj).map(|r| (get_str(r, "id").unwrap_or("").to_string(), r)).collect();
    Ok(Ok(f(&chosen, total, &config(ctx)?)))
}

/// The selected heads' query views read from the index, in first-seen order,
/// and how many heads the ledger has — or None whenever the index cannot
/// stand in for a full read: it is absent, damaged or stale, or a view does
/// not name the head it is filed under. A selection of live heads reads the
/// header and the live section and nothing else; the log is never read.
fn indexed_heads(ctx: &Ctx, select: Select) -> Option<(Entries, usize)> {
    use std::io::Read;
    let log = std::fs::File::open(ctx.store.log_path()?).ok()?;
    let identity = pecia_store::identity(&log).ok()?;
    let mut file = std::fs::File::open(ctx.store.index_path()?).ok()?;
    let mut head = [0u8; index::HEADER_LEN];
    file.read_exact(&mut head).ok()?;
    let h = index::header(&head)?;
    if h.stamp.log != identity {
        return None;
    }
    let mut read = |len: u64| -> Option<Vec<u8>> {
        let mut buf = vec![0u8; usize::try_from(len).ok()?];
        file.read_exact(&mut buf).ok().map(|_| buf)
    };
    let live_bytes = read(h.live.bytes)?;
    let mut filed = index::heads(&h.live, &live_bytes)?;
    let settled_bytes;
    if !matches!(select, Select::NonTerminal) {
        settled_bytes = read(h.settled.bytes)?;
        if !index::is_trailer(&read(8)?) {
            return None;
        }
        filed.extend(index::heads(&h.settled, &settled_bytes)?);
        filed.sort_unstable_by_key(|x| x.order);
    }
    let chosen: Vec<&index::Head> = filed.iter().filter(|x| select.wants(Some(x.status), Some(x.kind))).collect();
    let total = usize::try_from(h.live.count + h.settled.count).ok()?;
    Some((index::views(&chosen)?.into(), total))
}

/// Record the heads of `entries` against the log's `identity` — for a
/// caller that has just written the log itself, and so has not asked whether
/// a query could run over it; the index only ever stands in for a read that
/// lets one.
pub fn refresh_index(ctx: &Ctx, entries: &Entries, identity: &index::Identity) {
    if require_heads(&pecia_core::check::records_of(entries), &[]).is_ok() {
        write_index(ctx, entries, identity);
    }
}

/// Record the heads of `entries` against `identity`, the log they were read
/// from — for a caller that has verified the read would let a query run.
pub fn write_index(ctx: &Ctx, entries: &Entries, identity: &index::Identity) {
    let Some(last) = entries.last() else { return };
    let stamp = index::Stamp { log: *identity, entries: entries.len() as u64, head: pecia_core::entry_digest(last) };
    if let Some(bytes) = index::heads_of(entries).and_then(|heads| index::encode(&stamp, &heads)) {
        ctx.store.write_index(&bytes);
    }
}

fn run(ctx: &Ctx, select: Select, body: impl FnOnce(&[(String, &Pairs)], &Config) -> Value) -> Result<u8, String> {
    match with_heads(ctx, select, |heads, _, cfg| body(heads, cfg))? {
        Ok(v) => {
            ctx.emit(&v);
            Ok(0)
        }
        Err(m) => Ok(ctx.cannot_run(&m)),
    }
}

pub fn ready_cmd(ctx: &Ctx, _args: &Parsed) -> Result<u8, String> {
    run(ctx, Select::NonTerminal, |heads, cfg| Value::Array(ready(heads, None).into_iter().map(|h| project("ready", h, cfg)).collect()))
}

pub fn next_cmd(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let limit = args.int("limit").unwrap_or(10);
    run(ctx, Select::NonTerminal, |heads, cfg| {
        let all = ready(heads, Some(3));
        // A negative limit slices from the end, as the reference's list slice does.
        let n = all.len() as i64;
        let take = if limit >= 0 { limit.min(n) } else { (n + limit).max(0) };
        Value::Array(all.into_iter().take(take as usize).map(|h| project("next", h, cfg)).collect())
    })
}

pub fn blocked_cmd(ctx: &Ctx, _args: &Parsed) -> Result<u8, String> {
    run(ctx, Select::NonTerminal, |heads, cfg| {
        let blocked = compute_blockers(heads);
        let kind_of: std::collections::HashMap<&str, Option<&str>> = heads.iter().map(|(k, h)| (k.as_str(), get_str(h, "type"))).collect();
        let mut sorted: Vec<&(String, &Pairs)> = heads.iter().filter(|(_, h)| get_str(h, "status") == Some("open")).collect();
        sorted.sort_by(|a, b| a.0.cmp(&b.0));
        let mut out = Vec::new();
        for (id, h) in sorted {
            let blockers = blocked.get(id).cloned().unwrap_or_default();
            if blockers.is_empty() {
                continue;
            }
            let is_question = |b: &String| kind_of.get(b.as_str()) == Some(&Some("question"));
            let mut view = match project("blocked", h, cfg) {
                Value::Object(p) => p,
                _ => unreachable!(),
            };
            view.push(("blockers".into(), Value::Array(blockers.iter().filter(|b| !is_question(b)).map(|b| s(safe_id(b))).collect())));
            view.push(("awaiting_answers".into(), Value::Array(blockers.iter().filter(|b| is_question(b)).map(|b| s(safe_id(b))).collect())));
            out.push(Value::Object(view));
        }
        Value::Array(out)
    })
}

pub fn graph_cmd(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let mermaid = args.str("format") == Some("mermaid");
    let got = with_heads(ctx, Select::All, |heads, _, cfg| {
        let mut sorted: Vec<&(String, &Pairs)> = heads.iter().collect();
        sorted.sort_by(|a, b| a.0.cmp(&b.0));
        let nodes: Vec<Value> = sorted.iter().map(|(_, h)| project("graph", h, cfg)).collect();
        let mut edges: Vec<(String, String, &'static str)> = Vec::new();
        for (rid, h) in &sorted {
            for (kind, to) in edge_targets(h) {
                edges.push((safe_id(rid), to, kind));
            }
        }
        (nodes, edges)
    })?;
    let (nodes, edges) = match got {
        Ok(v) => v,
        Err(m) => return Ok(ctx.cannot_run(&m)),
    };
    if mermaid {
        let mut lines = vec!["graph TD".to_string()];
        for n in &nodes {
            let id = n.get("id").and_then(Value::as_str).unwrap_or("");
            let title = n.get("title").and_then(Value::as_str).unwrap_or("");
            lines.push(format!("  {}[\"{}: {}\"]", mermaid_id(id), mermaid_safe_id_display(id), mermaid_label(title, 40)));
        }
        for (from, to, kind) in &edges {
            lines.push(format!("  {} -->|{kind}| {}", mermaid_id(from), mermaid_id(to)));
        }
        ctx.text(lines.join("\n"));
    } else {
        let edge_values: Vec<Value> = edges
            .into_iter()
            .map(|(from, to, kind)| obj(vec![("from", s(from)), ("to", s(to)), ("kind", s(kind))]))
            .collect();
        ctx.emit(&obj(vec![("nodes", Value::Array(nodes)), ("edges", Value::Array(edge_values))]));
    }
    Ok(0)
}

/// A record as stored — every field, verbatim (v3.3, pc-ded91385e31a) — or,
/// with `--history`, every entry that revised it, in log order. Refuses
/// where the queries refuse. The current record comes from its own line in
/// the log when the index stands for the log; a history reads it whole.
pub fn show_cmd(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let id = args.str("id").unwrap_or("");
    let history = args.flag("history");
    if !history {
        match indexed_record(ctx, id) {
            Some(Some(rec)) => {
                ctx.emit(&rec);
                return Ok(0);
            }
            Some(None) => return Ok(ctx.cannot_run(&format!("record {id} not found"))),
            None => {}
        }
    }
    let (entries, findings, _) = load_entries_noting_identity(ctx)?;
    let records = pecia_core::check::records_of(&entries);
    let heads = match require_heads(&records, &findings) {
        Ok(h) => h,
        Err(m) => return Ok(ctx.cannot_run(&m)),
    };
    let Some(&(_, head)) = heads.iter().find(|(k, _)| k == id) else {
        return Ok(ctx.cannot_run(&format!("record {id} not found")));
    };
    if history {
        let revisions = entries.iter().filter(|e| e.get("rec").and_then(as_obj).is_some_and(|r| get_str(r, "id") == Some(id))).cloned().collect();
        ctx.emit(&Value::Array(revisions));
    } else {
        ctx.emit(&Value::Object(head.to_vec()));
    }
    Ok(0)
}

/// The record `id` as the index finds it: Some(Some(rec)) read from its own
/// line and matching the view filed for it, Some(None) when the index —
/// standing for the log as it is — holds no such head, or None when the
/// index cannot stand in.
fn indexed_record(ctx: &Ctx, id: &str) -> Option<Option<Value>> {
    let log = std::fs::File::open(ctx.store.log_path()?).ok()?;
    let identity = pecia_store::identity(&log).ok()?;
    let bytes = std::fs::read(ctx.store.index_path()?).ok()?;
    if index::header(&bytes)?.stamp.log != identity {
        return None;
    }
    let (_, filed) = index::decode(&bytes)?;
    match filed.iter().find(|h| h.id == id) {
        None => Some(None),
        Some(h) => head_line(&log, h).map(Some),
    }
}

/// The record on the log line an index files `h` at — only if that line
/// holds exactly the view it is filed under.
pub fn head_line(log: &std::fs::File, h: &index::Head<'_>) -> Option<Value> {
    use std::os::unix::fs::FileExt;
    let mut line = vec![0u8; h.len as usize];
    log.read_exact_at(&mut line, h.offset).ok()?;
    let Value::Object(entry) = pecia_core::parse(std::str::from_utf8(&line).ok()?).ok()? else { return None };
    let rec = entry.into_iter().find(|(k, _)| k == "rec")?.1;
    (pecia_core::canonical::canonical_object(&index::query_view(as_obj(&rec)?)) == h.view).then_some(rec)
}
