//! `migrate`: reconstruct the single timeline from every committed snapshot
//! blob plus the working tree (pc-5c7c) — a BOOTSTRAP, never a resync.
//!
//! The expected diff is enumerated before the comparison (GP36): the unit is
//! record REVISIONS, not ids, and the frame is every reachable blob of
//! `.pecia/work.jsonl` plus the working-tree snapshot. What the frame cannot
//! contain is a revision never committed on any ref and absent from this
//! worktree — which is why the existing log is a SOURCE here, not an output.

use crate::args::Parsed;
use crate::cmd::query::config;
use crate::cmd::remote::{published_blob_lines, ref_head, PECIA_REF};
use crate::cmd::store_cmds::write_log_validated;
use crate::ctx::{finding_value, obj, s, Ctx, RULE1};
use pecia_core::canonical::canonical;
use pecia_core::check::{read_log, snapshot_chain, source_lines, timeline_errors};
use pecia_core::config::py_strip;
use pecia_core::parse::parse;
use pecia_core::record::{as_obj, get, get_str, strict_int};
use pecia_core::text::{render_value, safe_text, MESSAGE_CAP};
use pecia_core::write::{set, Chain};
use pecia_core::{sha256_hex, Value};
use pecia_store::{git, read_optional};
use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap, HashMap, HashSet};

type Key = (String, i64);

#[derive(Default)]
struct Collected {
    /// Every distinct content seen per (id, rev), first-seen first, with its
    /// canonical text.
    seen: HashMap<Key, Vec<(String, Value)>>,
    /// Keys in first-seen order, so iteration never depends on hashing.
    keys: Vec<Key>,
    /// Each source's observed order of revisions.
    orders: Vec<Vec<Key>>,
    /// A source line already read, and what it read as. Successive blobs
    /// repeat almost every line, so each distinct line is parsed once.
    memo: HashMap<Vec<u8>, Option<Key>>,
    blobs: i64,
    lines: i64,
    unreadable: i64,
}

impl Collected {
    /// One source's lines: (lines read, lines unreadable).
    fn absorb(&mut self, text: &[u8]) -> (i64, i64) {
        let (mut read, mut unreadable) = (0, 0);
        let mut sequence: Vec<Key> = Vec::new();
        let mut witnessed: HashSet<Key> = HashSet::new();
        for raw in source_lines(text) {
            if std::str::from_utf8(raw).is_ok_and(|t| py_strip(t).is_empty()) {
                continue;
            }
            let key = match self.memo.get(raw) {
                Some(Some(key)) => key.clone(),
                Some(None) => {
                    unreadable += 1;
                    continue;
                }
                None => {
                    let rec = std::str::from_utf8(raw).ok().and_then(|t| parse(t).ok()).filter(|v| as_obj(v).is_some());
                    let key = rec.as_ref().and_then(as_obj).and_then(|r| Some((get_str(r, "id")?.to_string(), strict_int(get(r, "rev"))?)));
                    let (Some(rec), Some(key)) = (rec, key) else {
                        self.memo.insert(raw.to_vec(), None);
                        unreadable += 1;
                        continue;
                    };
                    let text = canonical(&rec);
                    self.memo.insert(raw.to_vec(), Some(key.clone()));
                    let bucket = self.seen.entry(key.clone()).or_insert_with(|| {
                        self.keys.push(key.clone());
                        Vec::new()
                    });
                    if !bucket.iter().any(|(t, _)| *t == text) {
                        bucket.push((text, rec));
                    }
                    key
                }
            };
            read += 1;
            if witnessed.insert(key.clone()) {
                sequence.push(key);
            }
        }
        if sequence.len() > 1 {
            self.orders.push(sequence);
        }
        self.lines += read;
        self.unreadable += unreadable;
        (read, unreadable)
    }

    /// Topological merge of every source's observed sequence over `keys`,
    /// (rev, id) as the tie-break; None when the witnesses disagree (a
    /// cycle). Each sequence is read with the other keys left out.
    fn witnessed_order(&self, keys: &[Key]) -> Option<Vec<Key>> {
        let wanted: HashSet<&Key> = keys.iter().collect();
        let mut succ: HashMap<&Key, BTreeSet<&Key>> = keys.iter().map(|k| (k, BTreeSet::new())).collect();
        let mut indegree: HashMap<&Key, usize> = keys.iter().map(|k| (k, 0)).collect();
        for full in &self.orders {
            let sequence: Vec<&Key> = full.iter().filter(|k| wanted.contains(k)).collect();
            for pair in sequence.windows(2) {
                let (a, b) = (pair[0], pair[1]);
                if succ.get_mut(a).expect("key").insert(b) {
                    *indegree.get_mut(b).expect("key") += 1;
                }
            }
        }
        let mut heap: BinaryHeap<Reverse<(i64, &str)>> =
            indegree.iter().filter(|(_, d)| **d == 0).map(|(k, _)| Reverse((k.1, k.0.as_str()))).collect();
        let mut out = Vec::new();
        while let Some(Reverse((rev, rid))) = heap.pop() {
            let key = (rid.to_string(), rev);
            for next in &succ[&key] {
                let d = indegree.get_mut(*next).expect("key");
                *d -= 1;
                if *d == 0 {
                    heap.push(Reverse((next.1, next.0.as_str())));
                }
            }
            out.push(key);
        }
        (out.len() == keys.len()).then_some(out)
    }
}

/// Every committed `.pecia/work.jsonl` blob, one per commit that has one, in
/// `rev-list --all` order — read through ONE `cat-file --batch`, not a `git
/// show` per commit. A blob several commits share is absorbed once and
/// counted for each.
fn collect(ctx: &Ctx) -> Result<(Vec<Value>, Vec<(&'static str, Value)>), String> {
    let root = &ctx.store.root;
    let mut c = Collected::default();
    let revs = git::out(root, &["rev-list", "--all"]).unwrap_or_default();
    let shas: Vec<&str> = revs.split_whitespace().collect();
    if !shas.is_empty() {
        let request: String = shas.iter().map(|sha| format!("{sha}:.pecia/work.jsonl\n")).collect();
        let (code, out, err) = git::run_raw(root, &["cat-file", "--batch"], Some(request.as_bytes()));
        if code != 0 {
            return Err(format!("git cat-file --batch failed: {}", String::from_utf8_lossy(&err).trim()));
        }
        let mut memo: HashMap<Vec<u8>, (i64, i64)> = HashMap::new();
        let mut at = 0;
        for _ in &shas {
            let nl = out[at..].iter().position(|b| *b == b'\n').ok_or("git cat-file --batch: truncated output")? + at;
            let header = String::from_utf8_lossy(&out[at..nl]).to_string();
            at = nl + 1;
            let parts: Vec<&str> = header.split(' ').collect();
            if parts.len() != 3 {
                continue; // `<object> missing`: this commit has no snapshot
            }
            let size: usize = parts[2].parse().map_err(|_| format!("git cat-file --batch: bad header {header:?}"))?;
            let body = out.get(at..at + size).ok_or("git cat-file --batch: truncated object")?;
            at += size + 1;
            if parts[1] != "blob" || body.is_empty() {
                continue;
            }
            c.blobs += 1;
            let oid = parts[0].as_bytes().to_vec();
            if let Some((read, unreadable)) = memo.get(&oid) {
                c.lines += read;
                c.unreadable += unreadable;
            } else {
                let counts = c.absorb(body);
                memo.insert(oid, counts);
            }
        }
    }
    if let Some(bytes) = read_optional(&ctx.store.snapshot_path())? {
        c.absorb(&bytes);
    }
    let divergent = c.seen.values().filter(|v| v.len() > 1).count() as i64;
    let distinct_ids = c.keys.iter().map(|k| k.0.as_str()).collect::<HashSet<_>>().len() as i64;
    let by_rev_id = |mut o: Vec<Key>| {
        o.sort_by(|a, b| (a.1, &a.0).cmp(&(b.1, &b.0)));
        o
    };
    // A verified snapshot is the order (v3.7, pc-9846a784839f): the working
    // tree's snapshot, when its records chain to its recorded head, is the
    // timeline through that head (v3.6), so it is the rebuild's order and its
    // first entries, record for record. History adds only the revisions it
    // lacks, after it, merged as before.
    let (proj, head, _, _) = crate::cmd::store_cmds::snapshot_files(ctx)?;
    let copy = snapshot_chain(head.as_deref(), proj.as_deref());
    let first: Option<Vec<Key>> = copy.as_ref().and_then(|c| {
        c.iter()
            .map(|e| {
                let r = e.get("rec").and_then(as_obj)?;
                Some((get_str(r, "id")?.to_string(), strict_int(get(r, "rev"))?))
            })
            .collect()
    });
    let usable = first.filter(|f| f.iter().collect::<HashSet<_>>().len() == f.len() && f.iter().all(|k| c.seen.contains_key(k)));
    let (records, how): (Vec<Value>, &str) = match (copy, usable) {
        (Some(copy), Some(first)) => {
            let taken: HashSet<&Key> = first.iter().collect();
            let rest: Vec<Key> = c.keys.iter().filter(|k| !taken.contains(k)).cloned().collect();
            let (tail, how) = match (rest.is_empty(), c.witnessed_order(&rest)) {
                (true, _) => (Vec::new(), "snapshot"),
                (false, Some(o)) => (o, "snapshot, then witnessed"),
                (false, None) => (by_rev_id(rest.clone()), "snapshot, then rev-id (witness orders conflict)"),
            };
            let mut records: Vec<Value> = copy.iter().filter_map(|e| e.get("rec").cloned()).collect();
            records.extend(tail.iter().map(|k| c.seen[k][0].1.clone()));
            (records, how)
        }
        _ => {
            let (order, how) = match c.witnessed_order(&c.keys) {
                Some(o) => (o, "witnessed"),
                None => (by_rev_id(c.keys.clone()), "rev-id (witness orders conflict)"),
            };
            (order.iter().map(|k| c.seen[k][0].1.clone()).collect(), how)
        }
    };
    let stats = vec![
        ("blobs", Value::Int(c.blobs)),
        ("lines", Value::Int(c.lines)),
        ("unreadable_lines", Value::Int(c.unreadable)),
        ("divergent", Value::Int(divergent)),
        ("distinct_revisions", Value::Int(c.keys.len() as i64)),
        ("distinct_ids", Value::Int(distinct_ids)),
        ("order", s(how)),
    ];
    Ok((records, stats))
}

fn stat(stats: &[(&str, Value)], key: &str) -> i64 {
    stats.iter().find(|(k, _)| *k == key).and_then(|(_, v)| v.as_i64()).unwrap_or(0)
}

/// A revision's identity, absent and null alike.
fn id_rev(r: &Value) -> String {
    let part = |k: &str| r.get(k).map_or_else(|| "null".to_string(), canonical);
    format!("{} {}", part("id"), part("rev"))
}

/// A value as a message names it: text as itself, anything else as JSON.
fn shown(v: Option<&Value>) -> String {
    match v {
        Some(Value::Str(s)) => s.clone(),
        Some(other) => render_value(other),
        None => "null".into(),
    }
}

fn emit_with<'a>(ctx: &Ctx, mut head: Vec<(&'a str, Value)>, stats: &[(&'a str, Value)], tail: Vec<(&'a str, Value)>) {
    head.extend(stats.iter().cloned());
    head.extend(tail);
    ctx.emit(&obj(head));
}

pub fn migrate(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let Some(target) = ctx.store.log_path() else {
        return Ok(ctx.cannot_run("not inside a git repository — the timeline lives under --git-common-dir"));
    };
    // The log is rewritten here, so the same lock every writer takes.
    let _lock = ctx.store.lock()?;
    let force_drop = args.flag("force_drop");
    let (records, mut stats) = collect(ctx)?;
    // A SOURCE LINE THE REBUILD CANNOT READ IS A REFUSAL, NOT A DROP (pc-d502).
    let n = stat(&stats, "unreadable_lines");
    if n > 0 && !force_drop {
        let (s1, s2, it) = if n == 1 { ("", "", "it") } else { ("s", "s", "them") };
        return Ok(ctx.cannot_run(&format!(
            "{n} source line{s1} could not be read as record{s2} (strict JSON carrying id and rev) — a rebuild would DROP {it} silently. Fix the source (`pecia check` names the damage), or pass --force-drop to accept the loss deliberately"
        )));
    }
    // THE EXISTING LOG IS A SOURCE, NOT AN OUTPUT (pc-824a), and a damaged
    // one does not disable the orphan guard (pc-e520).
    let existing_bytes = read_optional(&target)?;
    let (existing, log_findings) = existing_bytes.as_deref().map(read_log).unwrap_or_default();
    if existing_bytes.is_some() && !log_findings.is_empty() && !force_drop {
        return Ok(ctx.cannot_run(&format!(
            "the existing log cannot be read cleanly ({}) — entries past the damage cannot be verified as preserved, so a rebuild could ERASE them invisibly. Fix the log (`pecia check` names the damage), or pass --force-drop to accept the loss deliberately",
            log_findings[0].message
        )));
    }
    fn rec_of(e: &Value) -> &Value {
        e.get("rec").unwrap_or(&Value::Null)
    }
    if !existing.is_empty() && log_findings.is_empty() {
        let known: HashSet<String> = records.iter().map(id_rev).collect();
        let orphans: Vec<&Value> = existing.iter().map(rec_of).filter(|r| !known.contains(&id_rev(r))).collect();
        if !orphans.is_empty() && !force_drop {
            let what = if orphans.len() == 1 { "entry exists" } else { "entries exist" };
            return Ok(ctx.cannot_run(&format!(
                "{} {what} only in the log and in no committed blob or snapshot (first: {} rev {}). Rebuilding would ERASE them. Commit the snapshot first, or pass --force-drop to accept the loss deliberately",
                orphans.len(), shown(orphans[0].get("id")), shown(orphans[0].get("rev"))
            )));
        }
        stats.push(("log_only_orphans", Value::Int(orphans.len() as i64)));
    }
    let divergent = stat(&stats, "divergent");
    if divergent > 0 {
        return Ok(ctx.cannot_run(&format!(
            "{divergent} (id,rev) pairs carry divergent content — disposition them manually before migrating; this is the last merge"
        )));
    }
    let replacing = existing_bytes.is_some();
    let canonical_lines: HashSet<String> = existing.iter().map(|e| canonical(rec_of(e))).collect();
    let mut promoted = 0;
    let mut chain = Chain::new(Vec::new());
    for rec in records {
        let mut rec = rec;
        if replacing && !canonical_lines.contains(&canonical(&rec)) {
            if let Value::Object(pairs) = &mut rec {
                set(pairs, "forced", Value::Bool(true));
            }
            promoted += 1;
        }
        if let Err(why) = chain.admit(as_obj(&rec).expect("record")) {
            return Ok(ctx.cannot_run(&format!("migration input violates the CAS at {}: {why}", shown(rec.get("id")))));
        }
        chain.append(rec);
    }
    let entries = chain.into_entries();
    stats.push(("promoted_branded", Value::Int(promoted)));
    // A LOG THAT ENDS BEFORE ITS MARK (v3.3, E019) lost entries this store
    // wrote. A rebuild that holds the marked entry restores them and is the
    // recovery; one that does not would bury the loss, and is refused unless
    // the loss is accepted, as for log-only entries.
    let (mark, md) = crate::cmd::store_cmds::mark_file(ctx)?;
    if let Some(gone) = pecia_core::check::mark_violation(&existing, mark.as_deref(), &md) {
        if !pecia_core::check::holds_marked_entry(&entries, mark.as_deref()) && !force_drop {
            return Ok(ctx.cannot_run(&format!("{gone} (E019), and the rebuild from this repo's history does not hold the marked entry either, so it would bury the loss. Recover with `pecia sync`, or pass --force-drop to accept the loss deliberately")));
        }
    }
    let bad = timeline_errors(&entries, &config(ctx)?, None);
    if !bad.is_empty() {
        for f in &bad {
            ctx.emit(&finding_value(f));
        }
        ctx.emit(&obj(vec![
            ("migrated", Value::Bool(false)),
            ("entries", Value::Int(entries.len() as i64)),
            ("note", s("refusing to write a timeline the checker rejects — fix the findings above (or disposition the source records) and re-run")),
            ("rule", s(RULE1)),
        ]));
        return Ok(1);
    }
    if replacing && !args.flag("force") {
        return Ok(ctx.cannot_run(&format!("{} already exists — refusing to rewrite a timeline (use --force)", target.display())));
    }
    let (mut published, register_err) = ref_head(ctx);
    if let Some(err) = register_err.filter(|_| !force_drop) {
        return Ok(ctx.cannot_run(&format!(
            "the local publication register {PECIA_REF} exists but cannot be read: {err} — a failed read is an unknown register, not an absent one, so the divergence guard cannot run. Repair the read (or accept the rewrite with --force-drop)"
        )));
    }
    let root = &ctx.store.root;
    let mut remote_check: Vec<(&str, Value)> = vec![("attempted", Value::Bool(false))];
    if published.is_none() {
        let (code, remotes, remote_err) = git::run(root, &["remote"], None);
        if code != 0 {
            let why = if remote_err.is_empty() { "git remote failed".to_string() } else { remote_err };
            remote_check.push(("enumeration_failed", s(safe_text(&why, MESSAGE_CAP))));
        }
        if !remotes.is_empty() {
            let remote = args.str("remote").filter(|r| !r.is_empty()).map(str::to_string).unwrap_or_else(|| remotes.split('\n').next().unwrap_or("").to_string());
            remote_check[0].1 = Value::Bool(true);
            remote_check.push(("remote", s(remote.clone())));
            let (code, _, err) = git::run(root, &["fetch", &remote, &format!("+{PECIA_REF}:refs/pecia/remote")], None);
            if code == 0 {
                published = git::out(root, &["rev-parse", "--verify", "--quiet", "refs/pecia/remote"]).filter(|p| !p.is_empty());
                remote_check.push(("reached", Value::Bool(published.is_some())));
            } else {
                remote_check.push(("reached", Value::Bool(false)));
                remote_check.push(("error", s(safe_text(&err, MESSAGE_CAP))));
            }
        }
    }
    if let Some(published) = &published {
        let spec = format!("{published}:log.jsonl");
        let (code, prior, prior_err) = git::run_raw(root, &["show", &spec], None);
        if code != 0 && !force_drop {
            let why = String::from_utf8_lossy(&prior_err).trim().to_string();
            return Ok(ctx.cannot_run(&format!(
                "could not read the published prefix ({spec}): {} — a failed read is an unknown register, not an empty one, so the divergence guard cannot run. Repair the read (or accept the rewrite with --force-drop)",
                if why.is_empty() { "git show failed" } else { &why }
            )));
        }
        if code == 0 {
            match published_blob_lines(&prior) {
                Err(why) if !force_drop => {
                    return Ok(ctx.cannot_run(&format!(
                        "{why} — the published blob must be repaired (or the rewrite accepted with --force-drop), never silently normalized in comparison"
                    )));
                }
                Err(_) => {}
                Ok(prior_lines) => {
                    let rebuilt: Vec<String> = entries.iter().map(canonical).collect();
                    let extends = prior_lines.len() <= rebuilt.len()
                        && prior_lines.iter().zip(&rebuilt).all(|(p, r)| *p == r.as_bytes());
                    if !extends && !force_drop {
                        // Sync is the remedy only if sync can run
                        // (pc-7e110b7cf434): when the published timeline does
                        // not hold the head this commit's snapshot names, sync
                        // refuses too (E015), and each command named the other.
                        let (_, head, _, _) = crate::cmd::store_cmds::snapshot_files(ctx)?;
                        let recorded = head.as_deref().map(String::from_utf8_lossy).unwrap_or_default();
                        let recorded = py_strip(&recorded);
                        if !recorded.is_empty() && !prior_lines.iter().any(|l| sha256_hex(l) == recorded) {
                            let lost = if target.exists() { ", or that this log lost from its end" } else { "" };
                            return Ok(ctx.cannot_run(&format!(
                                "the rebuild would not extend the published timeline: {} entries are on {PECIA_REF} and the reconstruction diverges from them. migrate is a BOOTSTRAP command, not a resync — and `pecia sync` cannot adopt that timeline either, since it does not hold the head this commit's snapshot names: the snapshot carries entries that were never published{lost}. If another clone wrote them, have it run `pecia publish`, then run `pecia sync`; --force-drop accepts the rewrite, but the result is a fork `pecia publish` cannot push",
                                prior_lines.len()
                            )));
                        }
                        return Ok(ctx.cannot_run(&format!(
                            "the rebuild would not extend the published timeline: {} entries are on {PECIA_REF} and the reconstruction diverges from them. migrate is a BOOTSTRAP command, not a resync — use `pecia sync` to reconcile, or --force-drop to accept the rewrite",
                            prior_lines.len()
                        )));
                    }
                }
            }
        }
    }
    let shown_target = s(target.display().to_string());
    if args.flag("dry_run") {
        emit_with(ctx, vec![("would_write", shown_target), ("entries", Value::Int(entries.len() as i64))], &stats,
                  vec![("remote_check", obj(remote_check)), ("rule", s(RULE1))]);
        return Ok(0);
    }
    let expected = entries.len();
    let also = move |parsed: &[Value]| (parsed.len() != expected).then(|| "written log does not read back cleanly — chain integrity failed".to_string());
    if let Err(refusal) = write_log_validated(ctx, &entries, Some(&also), None, true)? {
        return Ok(ctx.cannot_run(&format!("the reconstruction does not read back cleanly: {refusal} — nothing was written")));
    }
    emit_with(ctx, vec![("migrated", shown_target), ("entries", Value::Int(entries.len() as i64))], &stats,
              vec![("head", crate::cmd::remote::head_of(&entries)), ("remote_check", obj(remote_check)), ("rule", s(RULE1))]);
    Ok(0)
}
