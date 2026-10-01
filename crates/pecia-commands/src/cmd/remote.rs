//! `publish` (and later `sync`): the distributed compare-and-swap.
//!
//! `refs/pecia/log` is a COMMIT chain, so fast-forward-ness is commit
//! ancestry and a remote that rejects non-fast-forward pushes is a CAS
//! register: the push wins or is refused, and there is no third outcome and no
//! merge. Delivery is confirmed by READING THE REF BACK — local register and
//! remote alike — never by a writer's exit code (VP20).

use crate::args::Parsed;
use crate::cmd::query::config;
use crate::cmd::store_cmds::{load_entries, snapshot_files};
use crate::ctx::{obj, s, Ctx, RULE1};
use pecia_core::check::{read_log, source_lines, timeline_errors, SnapshotFiles};
use pecia_core::config::py_strip;
use pecia_core::parse::parse;
use pecia_core::record::{as_obj, get};
use pecia_core::text::{render_str, safe_id, safe_text, MESSAGE_CAP};
use pecia_core::Value;
use pecia_store::git;

pub const PECIA_REF: &str = "refs/pecia/log";

fn git_run(ctx: &Ctx, args: &[&str], stdin: Option<&[u8]>) -> (i32, String, String) {
    git::run(&ctx.store.root, args, stdin)
}

/// The local publication register: (head, None), (None, None) when unborn,
/// or (None, reason) when it exists and cannot be read — which is UNKNOWN, not
/// empty, and must never be treated as empty.
pub fn ref_head(ctx: &Ctx) -> (Option<String>, Option<String>) {
    let (code, out, _) = git_run(ctx, &["rev-parse", "--verify", "--quiet", PECIA_REF], None);
    if code == 0 && !out.is_empty() {
        return (Some(out), None);
    }
    let (code, listed, err) = git_run(ctx, &["for-each-ref", PECIA_REF], None);
    if code != 0 {
        return (None, Some(if err.is_empty() { "git for-each-ref failed".into() } else { err }));
    }
    if !err.trim().is_empty() {
        return (None, Some(err.trim().to_string()));
    }
    if !listed.trim().is_empty() {
        return (None, Some(format!("{PECIA_REF} is listed but does not resolve to a commit")));
    }
    (None, None)
}

/// None when the register reads back as `expected`; otherwise what it reads.
pub fn local_ref_readback(ctx: &Ctx, expected: &str) -> Option<String> {
    match ref_head(ctx) {
        (_, Some(err)) => Some(format!("<unreadable: {err}>")),
        (Some(got), None) if got == expected => None,
        (got, None) => Some(got.unwrap_or_else(|| "<unborn>".into())),
    }
}

/// A published blob's lines, refused whole if any line is not a well-formed
/// entry — an invalid blob is refused, never normalized in comparison.
pub fn published_blob_lines(blob: &[u8]) -> Result<Vec<&[u8]>, String> {
    let lines = source_lines(blob);
    for (i, ln) in lines.iter().enumerate() {
        let n = i + 1;
        let Ok(text) = std::str::from_utf8(ln) else {
            if ln.iter().all(u8::is_ascii_whitespace) {
                return Err(format!("published log line {n} is blank — the published blob is E001-invalid (one entry per line, no blank lines; format-v2.md, pc-e7f0)"));
            }
            return Err(format!("published log line {n} is not valid UTF-8 — the published blob is E001-invalid at the line level (one UTF-8 JSON entry per line; format-v2.md, pc-0a2f)"));
        };
        if py_strip(text).is_empty() {
            return Err(format!("published log line {n} is blank — the published blob is E001-invalid (one entry per line, no blank lines; format-v2.md, pc-e7f0)"));
        }
        match parse(text) {
            Err(e) => return Err(format!("published log line {n} does not parse ({e}) — the published blob is E001-invalid at the line level (one JSON entry per line; format-v2.md, pc-1362)")),
            Ok(v) if as_obj(&v).and_then(|o| get(o, "rec")).and_then(as_obj).is_none() => {
                return Err(format!("published log line {n} is not an entry object carrying a rec — the published blob is E001-invalid at the line level (format-v2.md, pc-1362)"));
            }
            Ok(_) => {}
        }
    }
    Ok(lines)
}

pub fn remote_selection_refusal(requested: Option<&str>, remotes: &str) -> Option<String> {
    let requested = requested.filter(|r| !r.is_empty())?;
    let configured: Vec<&str> = remotes.split('\n').filter(|r| !r.trim().is_empty()).collect();
    if configured.contains(&requested) {
        return None;
    }
    if configured.is_empty() {
        return Some(format!("--remote {requested} was requested and this repository has NO remotes configured — the local publication register is the whole timeline here (format-v2.md 3.1), so the name cannot be honoured. Nothing about it was assumed"));
    }
    Some(format!("--remote {requested} names no configured remote (configured: {}) — the name is refused, never silently replaced by another remote", configured.join(", ")))
}

pub fn publish(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    publish_with_after_validation(ctx, args, || {})
}

fn publish_with_after_validation(ctx: &Ctx, args: &Parsed, after_validation: impl FnOnce()) -> Result<u8, String> {
    let Some(target) = ctx.store.log_path() else {
        return Ok(ctx.cannot_run("not inside a git repository"));
    };
    // Parse, validate, and publish one immutable generation. A second read
    // after validation could include an append the checker never saw.
    let log_bytes = match pecia_store::read_optional(&target)? {
        Some(bytes) => bytes,
        None => {
            let (_, findings) = load_entries(ctx)?;
            return Ok(ctx.cannot_run(&findings.first().map_or_else(
                || "timeline disappeared before publication — retry".to_string(),
                |f| f.message.clone(),
            )));
        }
    };
    let (entries, findings) = read_log(&log_bytes);
    if let Some(f) = findings.first() {
        return Ok(ctx.cannot_run(&f.message));
    }
    if entries.is_empty() {
        return Ok(ctx.cannot_run("timeline is empty — nothing to publish"));
    }
    let cfg = config(ctx)?;
    let (proj, head, sd, hd) = snapshot_files(ctx)?;
    let (mark, md) = crate::cmd::store_cmds::mark_file(ctx)?;
    let snap = SnapshotFiles { projection: proj.as_deref(), head: head.as_deref(), projection_display: &sd, head_display: &hd, mark: mark.as_deref(), mark_display: &md };
    if let Some(bad) = timeline_errors(&entries, &cfg, Some(&snap)).first() {
        return Ok(ctx.cannot_run(&format!("refusing to publish an unclean timeline: {}", bad.message)));
    }
    after_validation();
    let (code, blob, err) = git_run(ctx, &["hash-object", "-w", "--stdin"], Some(&log_bytes));
    if code != 0 {
        return Ok(ctx.cannot_run(&format!("could not write the log blob: {err}")));
    }
    let (code, tree, err) = git_run(ctx, &["mktree"], Some(format!("100644 blob {blob}\tlog.jsonl\n").as_bytes()));
    if code != 0 {
        return Ok(ctx.cannot_run(&format!("could not build the tree: {err}")));
    }
    let (parent, parent_err) = ref_head(ctx);
    if let Some(e) = parent_err {
        return Ok(ctx.cannot_run(&format!("refusing to publish: the local publication register {PECIA_REF} exists but cannot be read: {e} — a register that cannot be read is unknown, not empty, so the fork refusal cannot run. Nothing was published; repair the read and publish again")));
    }
    if let Some(p) = &parent {
        let (code, prior, prior_err) = git::run_raw(&ctx.store.root, &["show", &format!("{p}:log.jsonl")], None);
        if code != 0 {
            let e = String::from_utf8_lossy(&prior_err).trim().to_string();
            return Ok(ctx.cannot_run(&format!("refusing to publish: could not read the published prefix ({p}:log.jsonl): {} — a failed read is an unknown register, not an empty one, so the fork refusal cannot run. Nothing was published; repair the read and publish again", if e.is_empty() { "git show failed".into() } else { e })));
        }
        let prior_lines = match published_blob_lines(&prior) {
            Ok(l) => l,
            Err(why) => return Ok(ctx.cannot_run(&format!("refusing to publish: {why} — the ref's current blob must be repaired, not silently normalized in comparison"))),
        };
        let mine: Vec<&[u8]> = source_lines(&log_bytes).into_iter().filter(|l| !l.iter().all(u8::is_ascii_whitespace)).collect();
        if mine.len() < prior_lines.len() || mine[..prior_lines.len()] != prior_lines[..] {
            return Ok(ctx.cannot_run(&format!("refusing to publish: the ref currently holds {} entries that are not a prefix of this timeline's {}. This is a fork, not an append — run `pecia sync`", prior_lines.len(), mine.len())));
        }
    }
    let msg = format!("pecia timeline: {} entries", entries.len());
    let mut argv = vec!["commit-tree", tree.as_str(), "-m", msg.as_str()];
    if let Some(p) = &parent {
        argv.extend(["-p", p.as_str()]);
    }
    let (code, commit, err) = git_run(ctx, &argv, None);
    if code != 0 {
        return Ok(ctx.cannot_run(&format!("could not commit the tree: {err}")));
    }
    let (code, _, err) = git_run(ctx, &["update-ref", PECIA_REF, &commit, parent.as_deref().unwrap_or("")], None);
    let readback = local_ref_readback(ctx, &commit);
    if code != 0 && readback.is_none() {
        return Ok(ctx.cannot_run(&format!("local ref CAS reported failure ({err}) and {PECIA_REF} reads back as the commit just built — the register DID take the write. Nothing was delivered to a remote, and re-running `pecia publish` is safe (the prefix guard will see its own work); the writer misreporting is what to look at (VP20)")));
    }
    if code != 0 {
        return Ok(ctx.cannot_run(&format!("local ref CAS refused: {err}")));
    }
    if let Some(got) = readback {
        return Ok(ctx.cannot_run(&format!("update-ref reported success and {PECIA_REF} reads back as {got} — the local register did not take the write. Delivery is confirmed by reading the target, never by the writer's exit code (VP20, pc-4d57)")));
    }
    let mut result: Vec<(&str, Value)> = vec![
        ("published_local", Value::Bool(true)),
        ("entries", Value::Int(entries.len() as i64)),
        ("head", head_of(&entries)),
        ("ref", s(PECIA_REF)),
        ("rule", s(RULE1)),
    ];
    let emit = |ctx: &Ctx, r: Vec<(&str, Value)>, code: u8| {
        ctx.emit(&obj(r));
        Ok(code)
    };
    let (code, remotes, remote_err) = git_run(ctx, &["remote"], None);
    if code != 0 {
        result.push(("published_remote", Value::Bool(false)));
        result.push(("refused", s(safe_text(&format!("could not enumerate remotes: {}", if remote_err.is_empty() { "git remote failed".into() } else { remote_err }), MESSAGE_CAP))));
        result.push(("next", s("remote discovery failed — a failure is unknown, not 'no remote'; fix git and publish again")));
        return emit(ctx, result, 1);
    }
    if let Some(unknown) = remote_selection_refusal(args.str("remote"), &remotes) {
        result.push(("published_remote", Value::Bool(false)));
        result.push(("refused", s(unknown)));
        result.push(("next", s("name a configured remote, or run `pecia publish` with no --remote to use the first one")));
        return emit(ctx, result, 1);
    }
    if remotes.is_empty() {
        result.push(("remote", s("none — the local timeline IS the timeline (format-v2.md 3.1)")));
        return emit(ctx, result, 0);
    }
    let remote = args.str("remote").filter(|r| !r.is_empty()).map(str::to_string).unwrap_or_else(|| remotes.split('\n').next().unwrap_or("").to_string());
    let refspec = format!("{PECIA_REF}:{PECIA_REF}");
    let (push_code, _, push_err) = git_run(ctx, &["push", &remote, &refspec], None);
    let (ls_code, lsr, ls_err) = git_run(ctx, &["ls-remote", &remote, PECIA_REF], None);
    let landed: Option<String> = lsr.split_whitespace().next().map(str::to_string);
    if ls_code != 0 {
        let mut msg = format!("could not read {PECIA_REF} back from {remote}: {}", if ls_err.is_empty() { "git ls-remote failed".into() } else { ls_err });
        if push_code != 0 {
            msg.push_str(&format!("; `git push` also exited {push_code}: {push_err}"));
        }
        result.push(("published_remote", s("unknown")));
        result.push(("refused", s(safe_text(&msg, MESSAGE_CAP))));
        result.push(("next", s("delivery is unknown until the ref is read — repair the read and publish again; a second publish of an already-delivered timeline is a no-op")));
        return emit(ctx, result, 1);
    }
    if landed.as_deref() == Some(commit.as_str()) && push_code != 0 {
        result.push(("published_remote", Value::Bool(true)));
        result.push(("remote", s(remote)));
        result.push(("read_back_matches", Value::Bool(true)));
        result.push(("writer_reported_failure", s(safe_text(&format!("`git push` exited {push_code}: {push_err}"), MESSAGE_CAP))));
        result.push(("next", s("the timeline IS published — the remote ref reads back as the commit just built — but the push reported failure while delivering it. There is nothing here to re-run and nothing for `pecia sync` to reconcile; the push tooling is what to look at")));
        return emit(ctx, result, 1);
    }
    if landed.as_deref() != Some(commit.as_str()) {
        let why = match &landed {
            None => format!("{remote} has no {PECIA_REF} — nothing was delivered, and the push is what failed"),
            Some(l) if Some(l) == parent.as_ref() => format!("{remote} still holds the timeline this publish built on — nothing was delivered, and the push is what failed"),
            Some(_) => "the remote advanced — run `pecia sync`, then publish again".into(),
        };
        result.push(("published_remote", Value::Bool(false)));
        result.push(("remote_head", landed.as_deref().map_or(Value::Null, |l| s(safe_id(l)))));
        if push_code != 0 {
            result.push(("refused", s(safe_text(&push_err, MESSAGE_CAP))));
        } else {
            let shown = landed.as_deref().map_or_else(|| "null".to_string(), render_str);
            result.push(("refused", s(safe_text(&format!("push reported success and {PECIA_REF} on {remote} reads back as {shown} — delivery is confirmed by reading the target, never by the writer's exit code (VP20)"), MESSAGE_CAP))));
        }
        result.push(("next", s(why)));
        return emit(ctx, result, 1);
    }
    result.push(("published_remote", Value::Bool(true)));
    result.push(("remote", s(remote)));
    result.push(("read_back_matches", Value::Bool(true)));
    emit(ctx, result, 0)
}

use crate::cmd::store_cmds::{log_refusal, write_log_validated};
use pecia_core::canonical::canonical;
use pecia_core::check::{chain_prefix, copy_parts_note, has_error, holds_marked_entry, mark_violation, snapshot_chain, snapshot_truncation_witness, MARK_REMEDY};
use pecia_core::finding::Code;
use pecia_core::record::{get_str, strict_int};
use pecia_core::sync::{derived_touched, touched_conflicts, transfer_field, unpublished_by};
use pecia_core::text::{capped_seq, MESSAGE_ITEM_CAP, MESSAGE_VALUE_CAP};
use pecia_core::write::{remove, set, Chain};
use std::collections::BTreeSet;

/// Refuse to move the register if the move would unpublish a landed
/// revision — the rewind the CAS refusal's own remedy used to perform.
fn register_rewind_refusal(ctx: &Ctx, before: Option<&str>, remote_commit: &str, about_to_write: &[Value], local: &[Value]) -> Option<String> {
    let before = before.filter(|b| !b.is_empty() && *b != remote_commit)?;
    let (code, blob, err) = git::run_raw(&ctx.store.root, &["show", &format!("{before}:log.jsonl")], None);
    if code != 0 {
        let e = String::from_utf8_lossy(&err).trim().to_string();
        return Some(format!("refusing to move {PECIA_REF}: could not read what it currently publishes ({before}:log.jsonl): {} — a failed read is an unknown register, not an empty one, so the rewind guard cannot run. Nothing was written; repair the read and sync again", if e.is_empty() { "git show failed".into() } else { e }));
    }
    let lines = match published_blob_lines(&blob) {
        Ok(l) => l,
        Err(why) => return Some(format!("refusing to move {PECIA_REF}: {why} — the register's own blob must be repaired, not silently normalized in comparison. Nothing was written")),
    };
    let held: Vec<Value> = lines.iter().filter_map(|l| std::str::from_utf8(l).ok().and_then(|t| parse(t).ok())).collect();
    let (rid, rev, lost) = unpublished_by(&held, about_to_write, local)?;
    Some(format!(
        "refusing to move {PECIA_REF} to {}: the register publishes {rid} rev {rev} and the timeline this sync would write {lost} — the move would unpublish a landed revision, which is the rewind the CAS refusal's own remedy used to perform (pc-1bb6). Nothing was written; a publication that won the local register has not reached the remote — run `pecia publish` to deliver it, then sync again",
        &remote_commit[..remote_commit.len().min(10)]
    ))
}

fn advance_register(ctx: &Ctx, before: Option<&str>, remote_commit: &str, about_to_write: &[Value], local: &[Value]) -> Option<String> {
    if let Some(r) = register_rewind_refusal(ctx, before, remote_commit, about_to_write, local) {
        return Some(r);
    }
    let (code, _, ref_err) = git_run(ctx, &["update-ref", PECIA_REF, remote_commit, before.unwrap_or("")], None);
    let readback = local_ref_readback(ctx, remote_commit);
    let said = format!("exit {code}") + &if ref_err.is_empty() { ", no message".into() } else { format!(": {ref_err}") };
    if code != 0 {
        let Some(readback) = readback else {
            return Some(format!("local register CAS reported failure ({said}) and {PECIA_REF} reads back as exactly the value this sync asked it to take — THE REGISTER DID MOVE, and this command moved it. The log was not written, because this refusal is ordered before the rename, so the store is now behind its own register: that state is recoverable and `pecia sync` is its recovery (v2.12, pc-d373). The writer misreported its own outcome, which is the thing to look at (VP20, pc-2390)"));
        };
        let unmoved = readback == before.unwrap_or("<unborn>");
        let why = if unmoved {
            "so the CAS failed for a reason of its own rather than to a competing writer"
        } else {
            "so another writer moved it while sync was running and stays the winner (first publish wins, format-v2.md 5)"
        };
        return Some(format!("local register CAS refused ({said}) — {PECIA_REF} now reads {readback}, {why}. Nothing was rewound and nothing was written — the local log and its snapshot are byte-identical to before (v2.12); run `pecia sync` again to reconcile against what the register now publishes"));
    }
    readback.map(|got| format!("update-ref reported success and {PECIA_REF} reads back as {got} — the local register did not take the write, so the log was not written either; the next publish would build on a stale parent (VP20, pc-4d57)"))
}

fn conflict_value(id: &str, kind: Option<&str>, fields: &BTreeSet<String>, your_rev: Option<&Value>, landed_rev: i64) -> Value {
    let mut v = vec![
        ("id", s(id)),
        ("fields", Value::Array(fields.iter().cloned().map(Value::Str).collect())),
        ("your_rev", your_rev.cloned().unwrap_or(Value::Null)),
        ("landed_rev", Value::Int(landed_rev)),
    ];
    if let Some(k) = kind {
        v.push(("kind", s(k)));
    }
    obj(v)
}

fn collision_note(ids: &[String]) -> String {
    if ids.is_empty() {
        return String::new();
    }
    let ids = capped_seq(ids, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, ", ");
    format!("ID COLLISION on {ids}: your revision 1 meets a LANDED revision 1 of the same id, so both sides minted it for unrelated records — this is not a concurrent edit of one record and has no field to reconcile. `--take-landed` is refused here and would be the wrong instruction: it discards a distinct piece of work, and editing the landed id then overwrites another writer's record with your text. Re-create your record with `pecia add` (it mints a fresh id) and drop the local one, or rewrite its id before syncing. Nothing was written. ")
}

pub fn sync(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    if ctx.store.log_path().is_none() {
        return Ok(ctx.cannot_run("not inside a git repository"));
    }
    let (register_before, register_err) = ref_head(ctx);
    if let Some(e) = register_err {
        return Ok(ctx.cannot_run(&format!("the local publication register {PECIA_REF} exists but cannot be read: {e} — a register that cannot be read is unknown, never unborn or empty (claim 5, format-v2.md 5); repair the read (the loose ref under .git/refs/pecia/) and sync again")));
    }
    let (code, remotes, remote_err) = git_run(ctx, &["remote"], None);
    if code != 0 {
        return Ok(ctx.cannot_run(&format!("could not enumerate remotes: {} — a discovery failure is unknown, not 'no remote'; syncing against the local register instead could report success while this clone stays behind the configured origin", if remote_err.is_empty() { "git remote failed".into() } else { remote_err })));
    }
    if let Some(u) = remote_selection_refusal(args.str("remote"), &remotes) {
        return Ok(ctx.cannot_run(&u));
    }
    let source_ref = if !remotes.is_empty() {
        let remote = args.str("remote").filter(|r| !r.is_empty()).map(str::to_string).unwrap_or_else(|| remotes.split('\n').next().unwrap_or("").to_string());
        let (code, _, err) = git_run(ctx, &["fetch", &remote, &format!("+{PECIA_REF}:refs/pecia/remote")], None);
        if code != 0 {
            return Ok(ctx.cannot_run(&format!("fetch failed: {err}")));
        }
        "refs/pecia/remote"
    } else if register_before.is_some() {
        PECIA_REF
    } else {
        return Ok(ctx.cannot_run("no remote configured and nothing published locally (refs/pecia/log is unborn) — there is nothing to sync against"));
    };
    let (code, blob, err) = git::run_raw(&ctx.store.root, &["show", &format!("{source_ref}:log.jsonl")], None);
    if code != 0 {
        return Ok(ctx.cannot_run(&format!("published ref {source_ref} carries no log.jsonl: {}", String::from_utf8_lossy(&err).trim())));
    }
    let (_, remote_commit, _) = git_run(ctx, &["rev-parse", "--verify", "--quiet", source_ref], None);
    if remote_commit.is_empty() {
        return Ok(ctx.cannot_run("fetched ref does not resolve to a commit"));
    }
    let _lock = ctx.store.lock()?;
    let cfg = config(ctx)?;
    let (mut local, mut findings) = load_entries(ctx)?;
    if !findings.is_empty() && findings.iter().all(|f| f.code == Code::E000 && f.message.contains("no timeline yet")) {
        local.clear();
        findings.clear();
    }
    if let Some(f) = findings.first() {
        return Ok(ctx.cannot_run(&f.message));
    }
    if let Some(bad) = timeline_errors(&local, &cfg, None).first() {
        return Ok(ctx.cannot_run(&format!("local timeline is not clean: {} — sync re-chains only revisions the CAS could have admitted; run `pecia check`, repair the local log, and sync again", bad.message)));
    }
    let their_lines = match published_blob_lines(&blob) {
        Ok(l) => l,
        Err(why) => return Ok(ctx.cannot_run(&format!("{why} — sync refuses to normalize an invalid published artifact in transport; repair the published timeline first"))),
    };
    let theirs: Vec<Value> = their_lines.iter().filter_map(|l| std::str::from_utf8(l).ok().and_then(|t| parse(t).ok())).collect();
    let mut common = 0;
    while common < local.len() && common < theirs.len() && canonical(&local[common]) == canonical(&theirs[common]) {
        common += 1;
    }
    let mut take_landed: Vec<String> = Vec::new();
    for t in args.list("take_landed") {
        if !take_landed.contains(&t) {
            take_landed.push(t);
        }
    }
    let (proj, head, _, _) = crate::cmd::store_cmds::snapshot_files(ctx)?;
    // A verified snapshot is adopted where the timeline stops short of it
    // (v3.6, pc-4f84768dbed1): where this log and the published timeline are
    // both prefixes of the snapshot's own chain, that chain is the timeline
    // this sync writes. Nothing either holds is lost, and the writer's entries
    // arrive with the writer's own hashes.
    let copy = snapshot_chain(head.as_deref(), proj.as_deref());
    if let Some(copy) = copy.as_deref().filter(|c| c.len() > local.len().max(theirs.len()) && chain_prefix(&local, c) && chain_prefix(&theirs, c)) {
        if !take_landed.is_empty() {
            return Ok(ctx.cannot_run(&format!("--take-landed named {} and this sync has no local-only revisions to discard — the snapshot's verified timeline is being adopted whole. Nothing was written; run `pecia sync` without the flag", take_landed.join(", "))));
        }
        let (mark, md) = crate::cmd::store_cmds::mark_file(ctx)?;
        if let Some(gone) = mark_violation(&local, mark.as_deref(), &md).filter(|_| !holds_marked_entry(copy, mark.as_deref())) {
            return Ok(ctx.cannot_run(&format!("sync refuses: {gone} (E019), and the snapshot's timeline does not hold the marked entry either. {MARK_REMEDY}")));
        }
        let cfg2 = cfg.clone();
        let also = move |parsed: &[Value]| timeline_errors(parsed, &cfg2, None).first().map(|f| format!("the snapshot's timeline is not clean: {}", f.message));
        let mut register_refusal: Option<String> = None;
        let mut take_register = |parsed: &[Value]| {
            register_refusal = advance_register(ctx, register_before.as_deref(), &remote_commit, parsed, &local);
            register_refusal.clone()
        };
        let outcome = write_log_validated(ctx, copy, Some(&also), Some(&mut take_register), true)?;
        if let Some(r) = register_refusal {
            return Ok(ctx.cannot_run(&r));
        }
        let adopted = match outcome {
            Ok(parsed) => parsed,
            Err(refusal) => return Ok(ctx.cannot_run(&format!("refusing to adopt the snapshot's timeline: {refusal} — the local log was never written"))),
        };
        ctx.emit(&obj(vec![
            ("synced", Value::Bool(true)),
            ("common_prefix", Value::Int(common as i64)),
            ("rechained", Value::Int(0)),
            ("fast_forwarded", Value::Int(theirs.len().saturating_sub(local.len()) as i64)),
            ("from_snapshot", Value::Int((copy.len() - local.len().max(theirs.len())) as i64)),
            ("head", head_of(&adopted)),
            ("rule", s(RULE1)),
        ]));
        return Ok(0);
    }
    let mine: Vec<usize> = (common..local.len()).collect();
    if mine.is_empty() {
        if !take_landed.is_empty() {
            return Ok(ctx.cannot_run(&format!("--take-landed named {} and this sync has no local-only revisions to discard — the published timeline is being adopted whole. Nothing was written; run `pecia sync` without the flag", take_landed.join(", "))));
        }
        if let Some(w) = snapshot_truncation_witness(&local, head.as_deref(), proj.as_deref(), crate::cmd::store_cmds::marked(ctx), Some(&theirs)) {
            return Ok(ctx.cannot_run(&format!("sync refuses to regenerate the snapshot: {w}{}", copy_parts_note(copy.as_deref().map(|c| c.as_slice()), &local, &theirs))));
        }
        // The same rule for the log's mark (v3.3): adopting a timeline that
        // holds the marked entry IS the recovery; one that does not would
        // move the mark past the loss.
        let (mark, md) = crate::cmd::store_cmds::mark_file(ctx)?;
        if let Some(gone) = mark_violation(&local, mark.as_deref(), &md).filter(|_| !holds_marked_entry(&theirs, mark.as_deref())) {
            return Ok(ctx.cannot_run(&format!("sync refuses: {gone} (E019), and the published timeline does not hold the marked entry either. {MARK_REMEDY}")));
        }
        let cfg2 = cfg.clone();
        let also = move |parsed: &[Value]| timeline_errors(parsed, &cfg2, None).first().map(|f| format!("remote timeline is not clean: {}", f.message));
        let mut register_refusal: Option<String> = None;
        let mut take_register = |parsed: &[Value]| {
            register_refusal = advance_register(ctx, register_before.as_deref(), &remote_commit, parsed, &local);
            register_refusal.clone()
        };
        let outcome = write_log_validated(ctx, &theirs, Some(&also), Some(&mut take_register), true)?;
        if let Some(r) = register_refusal {
            return Ok(ctx.cannot_run(&r));
        }
        let hydrated = match outcome {
            Ok(parsed) => parsed,
            Err(refusal) => return Ok(ctx.cannot_run(&format!("refusing to hydrate: {refusal} — the published timeline is not a valid timeline to adopt; the local log was never written"))),
        };
        ctx.emit(&obj(vec![
            ("synced", Value::Bool(true)),
            ("common_prefix", Value::Int(common as i64)),
            ("rechained", Value::Int(0)),
            ("fast_forwarded", Value::Int((theirs.len() - common) as i64)),
            ("head", head_of(&hydrated)),
            ("rule", s(RULE1)),
        ]));
        return Ok(0);
    }
    if let Some(chain) = log_refusal(&theirs) {
        return Ok(ctx.cannot_run(&format!("published timeline's chain is not valid: {chain} — refusing to re-chain onto it; repair the published timeline first. Nothing was written")));
    }
    if let Some(bad) = timeline_errors(&theirs, &cfg, None).first() {
        return Ok(ctx.cannot_run(&format!("published timeline is not clean: {} — refusing to re-chain onto it; repair the published timeline first (run `pecia check` against it for the full findings)", bad.message)));
    }

    fn rec_of(e: &Value) -> &pecia_core::record::Pairs {
        e.get("rec").and_then(as_obj).unwrap_or(&[])
    }
    let mut rebuilt = Chain::new(theirs.clone());
    let mut conflicts: Vec<Value> = Vec::new();
    let mut collision_ids: Vec<String> = Vec::new();
    let mut editable_ids: Vec<String> = Vec::new();
    let mut skipped_noops = 0usize;
    let mut already_landed = 0usize;
    let mut discarded: Vec<Value> = Vec::new();
    let mut discarded_ids: Vec<String> = Vec::new();
    for &idx in &mine {
        let rec = rec_of(&local[idx]);
        let rid = get_str(rec, "id").unwrap_or("").to_string();
        let landed: Vec<usize> = (common..theirs.len()).filter(|&i| get_str(rec_of(&theirs[i]), "id") == Some(rid.as_str())).collect();
        let per_landed: Vec<(i64, BTreeSet<String>)> = landed.iter().map(|&i| (strict_int(get(rec_of(&theirs[i]), "rev")).unwrap_or(0), derived_touched(&theirs, i))).collect();
        let theirs_touched: BTreeSet<String> = per_landed.iter().flat_map(|(_, t)| t.iter().cloned()).collect();
        let mine_touched = derived_touched(&local, idx);
        // A revision that landed as it stands is already true remotely (v3.6):
        // a clone that adopted a snapshot holds its writer's revisions
        // verbatim, and they land again, identical, once the writer re-chains
        // and publishes them. Nothing is lost and nothing needs re-chaining.
        // A creation keeps its own rule below (pc-e499).
        let mine_line = local[idx].get("rec").map(canonical);
        let revises = local[..idx].iter().any(|p| get_str(rec_of(p), "id") == Some(rid.as_str()));
        if revises && landed.iter().any(|&i| theirs[i].get("rec").map(canonical) == mine_line) {
            already_landed += 1;
            continue;
        }
        let overlap = touched_conflicts(&theirs_touched, &mine_touched);
        if !overlap.is_empty() {
            let touching: Vec<i64> = per_landed.iter().filter(|(_, t)| !touched_conflicts(t, &mine_touched).is_empty()).map(|(r, _)| *r).collect();
            let landed_rev = *touching.last().unwrap_or(&0);
            if take_landed.contains(&rid) {
                discarded.push(conflict_value(&rid, None, &overlap, get(&rec, "rev"), landed_rev));
                discarded_ids.push(rid);
                continue;
            }
            conflicts.push(conflict_value(&rid, None, &overlap, get(&rec, "rev"), landed_rev));
            editable_ids.push(rid);
            continue;
        }
        let base: Option<Vec<(String, Value)>> = rebuilt.head(&rid).map(<[_]>::to_vec);
        let has_local_base = local[..idx].iter().any(|p| get_str(rec_of(p), "id") == Some(rid.as_str()));
        let forced = get(&rec, "forced") == Some(&Value::Bool(true)) || get(&rec, "forced").is_some_and(|v| *v != Value::Null && *v != Value::Bool(false));
        if mine_touched.is_empty() {
            if let Some(b) = &base {
                if has_local_base {
                    if !forced {
                        skipped_noops += 1;
                        continue;
                    }
                } else {
                    let divergence: BTreeSet<String> = pecia_core::check::diff_fields(Some(b), &rec).into_iter().collect();
                    if !divergence.is_empty() {
                        conflicts.push(conflict_value(&rid, Some("id-collision"), &divergence, get(&rec, "rev"), strict_int(get(b, "rev")).unwrap_or(0)));
                        collision_ids.push(rid);
                        continue;
                    }
                    if !forced {
                        skipped_noops += 1;
                        continue;
                    }
                }
            }
        }
        let mut revised: Vec<(String, Value)> = rec.to_vec();
        if let Some(b) = &base {
            revised = b.clone();
            for f in &mine_touched {
                transfer_field(&mut revised, rec, f);
            }
            set(&mut revised, "rev", Value::Int(strict_int(get(b, "rev")).unwrap_or(0) + 1));
        }
        remove(&mut revised, "forced");
        if let Some(fv) = get(rec, "forced").filter(|_| forced) {
            set(&mut revised, "forced", fv.clone());
        }
        remove(&mut revised, "anchor");
        remove(&mut revised, "anchor_dirty");
        for k in ["anchor", "anchor_dirty"] {
            if let Some(v) = get(rec, k) {
                set(&mut revised, k, v.clone());
            }
        }
        rebuilt.append(Value::Object(revised));
    }
    let unused: Vec<String> = take_landed.iter().filter(|t| !discarded_ids.contains(t)).cloned().collect();
    if !unused.is_empty() {
        let colliding: Vec<String> = collision_ids.iter().filter(|c| unused.contains(c)).cloned().collect();
        if !colliding.is_empty() {
            return Ok(ctx.cannot_run(&format!("--take-landed named {}, which is an ID COLLISION, not a same-field conflict — two clones minted that id for unrelated records. The flag discards a losing REVISION and there is none here; discarding would drop a distinct record. Nothing was written. Re-create your record with `pecia add`, which mints a fresh id (pc-1c65)", colliding.join(", "))));
        }
        return Ok(ctx.cannot_run(&format!("--take-landed named {}, which has no same-field conflict in this sync — nothing was written. Run `pecia sync` first and take the ids from its `conflicts` list (pc-ef5b)", unused.join(", "))));
    }
    if !conflicts.is_empty() {
        let flags_rendered: Vec<String> = editable_ids.iter().map(|i| format!("--take-landed {i}")).collect();
        let flags = capped_seq(&flags_rendered, MESSAGE_VALUE_CAP, MESSAGE_ITEM_CAP, " ");
        // A budget's elision is never argv (pc-4b34): the command is printed
        // only when it fits whole; otherwise the note names `remedy_argv`,
        // which is always complete, as `conflicts` is.
        let remedy = if flags == flags_rendered.join(" ") {
            format!("`pecia sync {flags}`")
        } else {
            format!("`pecia sync` with `--take-landed <id>` for each of the {} ids — too many for this note, so the complete command is `remedy_argv` (the arguments after `pecia`)", editable_ids.len())
        };
        let mut note = collision_note(&collision_ids);
        if !editable_ids.is_empty() {
            note.push_str(&format!("same-record same-field appends do not commute — nothing was written, and this refusal is atomic: any id you named with --take-landed is reported under `would_discard`, which is what WOULD have been dropped had the sync completed, and is still present. Re-reading the record and re-applying your intent does NOT resolve this on its own: the re-applied edit is a new local revision on top of the losing one and the next sync refuses naming both (pc-ef5b). Name EVERY conflicting id in ONE invocation — resolving them one at a time never terminates, because each run refuses on the ones you left out (pc-42c5): {remedy}, which discards your local-only revisions of each and names them under `discarded` in the result, and then re-apply your intent with `pecia edit <id>`"));
        }
        let mut out = vec![
            ("synced", Value::Bool(false)),
            ("common_prefix", Value::Int(common as i64)),
            ("conflicts", Value::Array(conflicts)),
            ("note", s(note)),
        ];
        if !editable_ids.is_empty() {
            let argv = std::iter::once(s("sync")).chain(editable_ids.iter().flat_map(|i| [s("--take-landed"), s(i.clone())])).collect();
            out.push(("remedy_argv", Value::Array(argv)));
        }
        if !discarded.is_empty() {
            out.push(("would_discard", Value::Array(discarded)));
        }
        ctx.emit(&obj(out));
        return Ok(1);
    }
    let rebuilt = rebuilt.into_entries();
    let bad = timeline_errors(&rebuilt, &cfg, None);
    if has_error(&bad) {
        ctx.emit(&obj(vec![
            ("synced", Value::Bool(false)),
            ("common_prefix", Value::Int(common as i64)),
            ("rechained_would_be", Value::Int(mine.len() as i64)),
            ("refused", Value::Array(bad.iter().map(|f| s(f.code.as_str())).collect())),
            ("note", s("re-chaining these appends onto the remote timeline produces an unclean ledger — disjoint fields do not imply a clean composition (v2 trap 3). Nothing was written. Resolve the named findings and sync again")),
        ]));
        return Ok(1);
    }
    if let Some(w) = snapshot_truncation_witness(&local, head.as_deref(), proj.as_deref(), crate::cmd::store_cmds::marked(ctx), Some(&rebuilt)) {
        return Ok(ctx.cannot_run(&format!("sync refuses to regenerate the snapshot: {w}{}", copy_parts_note(copy.as_deref().map(|c| c.as_slice()), &local, &theirs))));
    }
    let (mark, md) = crate::cmd::store_cmds::mark_file(ctx)?;
    if let Some(gone) = mark_violation(&local, mark.as_deref(), &md).filter(|_| !holds_marked_entry(&rebuilt, mark.as_deref())) {
        return Ok(ctx.cannot_run(&format!("sync refuses: {gone} (E019), and the re-chained timeline does not hold the marked entry either. {MARK_REMEDY}")));
    }
    let mut register_refusal: Option<String> = None;
    let mut take_register = |parsed: &[Value]| {
        register_refusal = advance_register(ctx, register_before.as_deref(), &remote_commit, parsed, &local);
        register_refusal.clone()
    };
    let outcome = write_log_validated(ctx, &rebuilt, None, Some(&mut take_register), true)?;
    if let Some(r) = register_refusal {
        return Ok(ctx.cannot_run(&r));
    }
    let entries = match outcome {
        Ok(e) => e,
        Err(refusal) => return Ok(ctx.cannot_run(&format!("re-chained log does not read back cleanly: {refusal} — nothing was written; the local log is unchanged"))),
    };
    let mut out = vec![
        ("synced", Value::Bool(true)),
        ("common_prefix", Value::Int(common as i64)),
        ("rechained", Value::Int((rebuilt.len() - theirs.len()) as i64)),
        ("skipped_noops", Value::Int(skipped_noops as i64)),
        ("entries", Value::Int(entries.len() as i64)),
        ("head", head_of(&entries)),
        ("rule", s(RULE1)),
    ];
    if already_landed > 0 {
        out.push(("already_landed", Value::Int(already_landed as i64)));
    }
    if !discarded.is_empty() {
        out.push(("discarded", Value::Array(discarded)));
    }
    ctx.emit(&obj(out));
    Ok(0)
}


/// The chain head a command reports (v3.3): the hash of the last entry, or
/// null for an empty timeline.
pub(crate) fn head_of(entries: &[Value]) -> Value {
    pecia_core::check::log_head_hash(entries).map_or(Value::Null, Value::Str)
}

#[cfg(test)]
mod tests {
    use super::*;
    use pecia_store::Store;

    #[test]
    fn publish_uses_the_bytes_it_validated_even_after_an_append() {
        let root =
            std::env::temp_dir().join(format!("pecia-publish-generation-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("repo dir");
        assert_eq!(git::code(&root, &["init", "-q"]), Some(0));
        assert_eq!(
            git::code(&root, &["config", "user.name", "Pecia Test"]),
            Some(0)
        );
        assert_eq!(
            git::code(&root, &["config", "user.email", "pecia@example.invalid"]),
            Some(0)
        );
        let ctx = Ctx::new(Store::at(root.canonicalize().expect("root")));
        let rec = parse(r#"{"id":"pc-aaaa","rev":1,"type":"task","title":"t","status":"open","priority":2,"created":"2026-07-20","updated":"2026-07-20","edges":{"blocks":[],"retires":[]},"disposition":null,"evidence":"unknown","owner":"o","labels":[],"body":""}"#).expect("record");
        let entry = pecia_core::write::make_entry(&[], &rec);
        write_log_validated(&ctx, &[entry], None, None, true)
            .expect("writer ran")
            .expect("written");
        let target = ctx.store.log_path().expect("log");
        let validated = std::fs::read(&target).expect("read original");
        let args = match crate::args::parse(&["pecia".to_string(), "publish".to_string()]) {
            crate::args::Outcome::Run(args) => args,
            _ => panic!("publish args"),
        };
        let status = publish_with_after_validation(&ctx, &args, || {
            use std::io::Write;
            std::fs::OpenOptions::new()
                .append(true)
                .open(&target)
                .expect("log open")
                .write_all(b"not-json\n")
                .expect("interleaved append");
        })
        .expect("publish ran");
        assert_eq!(status, 0);
        let (code, blob, err) = git::run_raw(&root, &["show", "refs/pecia/log:log.jsonl"], None);
        assert_eq!(code, 0, "{}", String::from_utf8_lossy(&err));
        assert_eq!(blob, validated);
        assert_eq!(
            std::fs::read(&target).expect("current log"),
            [validated, b"not-json\n".to_vec()].concat()
        );
    }
}
