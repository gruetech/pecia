//! `init` and `snapshot`: creating a store, and regenerating its projection.

use crate::args::Parsed;
use crate::ctx::{finding_value, obj, s, Ctx, RULE1};
use pecia_core::check::{Entries, has_error, log_head_hash, mark_text, mark_violation, projection_body, read_log, snapshot_truncation_witness, timeline_checks, SnapshotFiles, MARK_REMEDY};
use pecia_core::finding::{error, Code, Finding};
use pecia_core::index::Identity;
use pecia_core::Value;
use pecia_store::read_optional;

/// The log's entries and its read findings, or the E000 that says there is no
/// log to read — the reference's `load_entries`.
pub fn load_entries(ctx: &Ctx) -> Result<(Entries, Vec<Finding>), String> {
    load_entries_noting_identity(ctx).map(|(entries, findings, _)| (entries, findings))
}

/// As `load_entries`, with the identity of the log file when it held still
/// while it was read — what the query index is recorded against (v3.2).
pub fn load_entries_noting_identity(ctx: &Ctx) -> Result<(Entries, Vec<Finding>, Option<Identity>), String> {
    let Some(log) = ctx.store.log_path() else {
        return Ok((Entries::default(), vec![error(Code::E000, None, "not inside a git repository — the timeline lives under --git-common-dir (spec/format-v2.md 3.1)")], None));
    };
    let Some((bytes, identity)) = pecia_store::read_noting_identity(&log)? else {
        let has_remote = ctx.store.git(&["remote"]).is_some_and(|r| !r.is_empty());
        let msg = if has_remote {
            "no timeline yet, and this clone has a remote configured — run `pecia sync` first (safe by default: it is how a fresh clone of an already-published repo gets the timeline). If sync says nothing is published yet, run `pecia migrate` to build one from this repo's history, or `pecia init` if it has no pecia history either"
        } else {
            "no timeline yet — run `pecia migrate` to build one from this repo's history, or `pecia init` in a fresh repo"
        };
        return Ok((Entries::default(), vec![error(Code::E000, None, msg)], None));
    };
    let (entries, findings) = read_log(&bytes);
    Ok((entries, findings, identity))
}

/// VALIDATE, THEN RENAME (v2.12, pc-d373): the log is staged beside its
/// destination and read back through the same reader every command uses; only
/// a clean read-back, an `also` that passes, a projection that stages, and a
/// `before_commit` that succeeds let it replace the live log. On refusal the
/// target is byte-identical to before, because it was never opened.
pub fn write_log_validated(
    ctx: &Ctx,
    entries: &[Value],
    also: Option<&dyn Fn(&[Value]) -> Option<String>>,
    before_commit: Option<&mut dyn FnMut(&[Value]) -> Option<String>>,
    project: bool,
) -> Result<Result<Entries, String>, String> {
    let target = ctx.store.log_path().ok_or("not inside a git repository")?;
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let name = target.file_name().expect("file").to_string_lossy().to_string();
    let tmp = target.with_file_name(format!(".{name}.staged.{}", std::process::id()));
    struct Cleanup(std::path::PathBuf);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    let _cleanup = Cleanup(tmp.clone());
    let body: String = entries.iter().map(|e| pecia_core::canonical::canonical(e) + "\n").collect();
    std::fs::write(&tmp, &body).map_err(|e| e.to_string())?;
    let (parsed, findings) = read_log(&std::fs::read(&tmp).map_err(|e| e.to_string())?);
    if let Some(f) = findings.first() {
        return Ok(Err(f.message.clone()));
    }
    if let Some(also) = also {
        if let Some(p) = also(&parsed) {
            return Ok(Err(p));
        }
    }
    let staged = if project {
        let head = log_head_hash(&parsed).unwrap_or_default() + "\n";
        match ctx.store.stage_projection(&projection_body(&parsed), &head) {
            Ok(s) => Some(s),
            Err(p) => return Ok(Err(p)),
        }
    } else {
        None
    };
    // The log's high-water mark moves with it (v3.3), staged with the rest.
    let mark = match mark_text(&parsed).map(|text| ctx.store.stage_mark(&text)).transpose() {
        Ok(m) => m,
        Err(p) => return Ok(Err(p)),
    };
    if let Some(before) = before_commit {
        if let Some(p) = before(&parsed) {
            return Ok(Err(p));
        }
    }
    // Durable before it replaces the log, and its new name durable after
    // (pc-26a08d9c5d46): a sync that fails here refuses with nothing moved.
    if let Err(p) = pecia_store::sync_path(&tmp) {
        return Ok(Err(p));
    }
    std::fs::rename(&tmp, &target).map_err(|e| e.to_string())?;
    if let Some(dir) = target.parent() {
        pecia_store::sync_path(dir).map_err(|e| format!("the log was rewritten, but {e}"))?;
    }
    if let Some(m) = mark {
        m.commit()?;
    }
    if let Some(s) = staged {
        s.commit()?;
    }
    // The rewritten log's index, recorded as it lands (see append_record).
    if let Ok(file) = std::fs::File::open(&target) {
        if let (Ok(identity), Some(len)) = (pecia_store::identity(&file), parsed.byte_len()) {
            if identity.size == len {
                crate::cmd::query::refresh_index(ctx, &parsed, &identity);
            }
        }
    }
    Ok(Ok(parsed))
}

/// Why `entries` would not read back as a valid log, or None: serialized and
/// read back through the one reader every command uses.
pub fn log_refusal(entries: &[Value]) -> Option<String> {
    let body: String = entries.iter().map(|e| pecia_core::canonical::canonical(e) + "\n").collect();
    read_log(body.as_bytes()).1.first().map(|f| f.message.clone())
}

/// Write the projection for `entries`: body and witness, staged together.
pub fn write_snapshot(ctx: &Ctx, entries: &Entries) -> Result<(), String> {
    let head = log_head_hash(entries).unwrap_or_default() + "\n";
    ctx.store.stage_projection(&projection_body(entries), &head)?.commit()
}

/// The log's high-water mark as its file holds it, and how a message names it.
pub fn mark_file(ctx: &Ctx) -> Result<(Option<Vec<u8>>, String), String> {
    match ctx.store.mark_path() {
        Some(p) => Ok((read_optional(&p)?, ctx.store.display(&p))),
        None => Ok((None, String::new())),
    }
}

/// Whether the log's high-water mark file exists. Existence is the claim the
/// truncation witness needs: a store that has written has a mark.
pub fn marked(ctx: &Ctx) -> bool {
    ctx.store.mark_path().is_some_and(|p| p.exists())
}

pub fn snapshot_files(ctx: &Ctx) -> Result<(Option<Vec<u8>>, Option<Vec<u8>>, String, String), String> {
    let (sp, hp) = (ctx.store.snapshot_path(), ctx.store.snapshot_head_path());
    Ok((read_optional(&sp)?, read_optional(&hp)?, ctx.store.display(&sp), ctx.store.display(&hp)))
}

pub fn snapshot(ctx: &Ctx, _args: &Parsed) -> Result<u8, String> {
    let (entries, findings) = load_entries(ctx)?;
    if let Some(first) = findings.first() {
        return Ok(ctx.cannot_run(&format!(
            "refusing to regenerate: the log does not read whole — {}. The projection is derived from the log, so repair the log first and then regenerate; nothing was written here and the existing projection is untouched",
            first.message
        )));
    }
    let (proj, head, _, _) = snapshot_files(ctx)?;
    if let Some(w) = snapshot_truncation_witness(&entries, head.as_deref(), proj.as_deref(), marked(ctx), None) {
        return Ok(ctx.cannot_run(&format!("refusing to regenerate: {w}")));
    }
    write_snapshot(ctx, &entries)?;
    ctx.emit(&obj(vec![
        ("regenerated", s(ctx.store.snapshot_path().display().to_string())),
        ("entries", Value::Int(entries.len() as i64)),
        ("rule", s(RULE1)),
    ]));
    Ok(0)
}

const DEFAULT_CONFIG: &str = "# pecia config — flat keys only. Core vocabulary may be extended, not redefined.\n# extra_statuses: []\n# extra_types: []\n# planned: []\nstale_days: 7\nrot_days: 14\n";

fn byte_lines(bytes: &[u8]) -> impl Iterator<Item = &[u8]> {
    bytes.split(|b| *b == b'\n')
}

fn is_git_attribute_space(byte: &u8) -> bool {
    matches!(*byte, b' ' | b'\t' | b'\r' | b'\n')
}

fn trim_git_attribute_space(bytes: &[u8]) -> &[u8] {
    let start = bytes.iter().position(|b| !is_git_attribute_space(b)).unwrap_or(bytes.len());
    let end = bytes.iter().rposition(|b| !is_git_attribute_space(b)).map_or(start, |i| i + 1);
    &bytes[start..end]
}

fn legacy_union_line(line: &[u8]) -> Option<(&[u8], Vec<&[u8]>)> {
    let bare = trim_git_attribute_space(line);
    if bare.is_empty() || bare.starts_with(b"#") {
        return None;
    }
    let mut parts = bare.split(is_git_attribute_space).filter(|part| !part.is_empty());
    let pattern = parts.next()?;
    let attributes: Vec<&[u8]> = parts.collect();
    if (pattern == b".pecia/work.jsonl" || pattern == b"work.jsonl")
        && attributes.contains(&(b"merge=union" as &[u8])) {
        Some((pattern, attributes))
    } else {
        None
    }
}

pub(crate) fn legacy_union_declared_in_bytes(bytes: &[u8]) -> bool {
    byte_lines(bytes).any(|line| legacy_union_line(line).is_some())
}

fn gitignore_lists_lock(bytes: &[u8]) -> bool {
    bytes.split(|b| *b == b'\n')
        .any(|line| line.strip_suffix(b"\r").unwrap_or(line) == b".pecia/.lock")
}

/// Whether git ignores the lock — ASKED OF GIT (pc-4c7d), which composes every
/// pattern, negation and exclude file; only when git cannot answer does the
/// exact-line test stand in.
pub fn lock_is_ignored(ctx: &Ctx) -> bool {
    match pecia_store::git::code(&ctx.store.root, &["check-ignore", "-q", "--", ".pecia/.lock"]) {
        Some(0) => true,
        Some(1) => false,
        _ => std::fs::read(ctx.store.root.join(".gitignore"))
            .map(|bytes| gitignore_lists_lock(&bytes))
            .unwrap_or(false),
    }
}

pub fn init(ctx: &Ctx, _args: &Parsed) -> Result<u8, String> {
    let store = &ctx.store;
    let Some(target) = store.log_path() else {
        return Ok(ctx.cannot_run("not inside a git repository — run `git init` first (the timeline lives under --git-common-dir)"));
    };
    let (mark, md) = mark_file(ctx)?;
    if let Some(bytes) = read_optional(&target)? {
        let (entries, log_findings) = read_log(&bytes);
        let (proj, head, sd, hd) = snapshot_files(ctx)?;
        let mut witness: Vec<Finding> = log_findings.clone();
        let files = SnapshotFiles { projection: proj.as_deref(), head: head.as_deref(), projection_display: &sd, head_display: &hd, mark: mark.as_deref(), mark_display: &md };
        witness.extend(
            timeline_checks(&entries, Some(&files), !has_error(&log_findings))
                .into_iter()
                .filter(|f| f.code == Code::E015 || f.code == Code::E019),
        );
        witness.retain(|f| f.severity == pecia_core::finding::Severity::Error);
        if !witness.is_empty() {
            for f in &witness {
                ctx.emit(&finding_value(f));
            }
            ctx.emit(&obj(vec![
                ("initialized", Value::Bool(false)),
                ("note", s("a timeline already exists here and its state does not read back cleanly — repeat init will not regenerate the snapshot over it, because the disagreement is evidence (pc-0ff7). Investigate with `pecia check`; regenerate deliberately with `pecia snapshot`")),
            ]));
            return Ok(1);
        }
    } else if let Some(gone) = mark_violation(&[], mark.as_deref(), &md) {
        // No log at all, and a mark saying this store wrote one (v3.3): the
        // log was deleted, and an empty timeline here would bury that.
        return Ok(ctx.cannot_run(&format!("no timeline here, but {gone} (E019). {MARK_REMEDY}")));
    } else if read_optional(&store.snapshot_path())?.is_some_and(|b| !b.iter().all(u8::is_ascii_whitespace)) {
        return Ok(ctx.cannot_run(&format!(
            "no timeline here, but {} carries records — the fresh-clone shape (a plain clone never fetches the log). init would erase the projection's content under an empty timeline. Run `pecia sync` to hydrate the published timeline, or `pecia migrate` to rebuild one from this repo's history",
            store.display(&store.snapshot_path())
        )));
    }
    let io = |e: std::io::Error| e.to_string();
    std::fs::create_dir_all(store.root.join(".pecia")).map_err(io)?;
    let config = store.config_path();
    if !config.exists() {
        std::fs::write(&config, DEFAULT_CONFIG).map_err(io)?;
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(io)?;
    }
    if !target.exists() {
        std::fs::write(&target, "").map_err(io)?;
    }
    let (entries, _) = read_log(&std::fs::read(&target).map_err(io)?);
    write_snapshot(ctx, &entries)?;

    // Strip v1's merge=union for the projection: under a single timeline the
    // projection is regenerated, never merged, and the attribute would make
    // git merge two projections line by line into one no log produced.
    let attrs = store.root.join(".gitattributes");
    if let Ok(bytes) = std::fs::read(&attrs) {
        let mut kept: Vec<Vec<u8>> = Vec::new();
        let mut changed = false;
        let lines: Vec<&[u8]> = byte_lines(&bytes).collect();
        let mut dropped_final_line = false;
        for (index, ln) in lines.iter().enumerate() {
            if let Some((pattern, attributes)) = legacy_union_line(ln) {
                changed = true;
                let remaining: Vec<&[u8]> = attributes.into_iter()
                    .filter(|a| *a != b"merge=union").collect();
                if !remaining.is_empty() {
                    let mut rewritten = pattern.to_vec();
                    for attribute in remaining {
                        rewritten.push(b' ');
                        rewritten.extend_from_slice(attribute);
                    }
                    if ln.ends_with(b"\r") {
                        rewritten.push(b'\r');
                    }
                    kept.push(rewritten);
                    continue;
                }
                while kept.last().is_some_and(|k| trim_git_attribute_space(k).starts_with(b"#")) {
                    kept.pop();
                }
                if kept.last().is_some_and(|k| trim_git_attribute_space(k).is_empty()) {
                    kept.pop();
                }
                dropped_final_line = index == lines.len() - 1;
                continue;
            }
            kept.push(ln.to_vec());
        }
        if changed {
            if dropped_final_line && !kept.is_empty() {
                kept.push(Vec::new());
            }
            let mut rewritten = Vec::new();
            for (index, line) in kept.iter().enumerate() {
                if index > 0 {
                    rewritten.push(b'\n');
                }
                rewritten.extend_from_slice(line);
            }
            std::fs::write(&attrs, rewritten).map_err(io)?;
        }
    }
    if !lock_is_ignored(ctx) {
        let gi = store.root.join(".gitignore");
        let existing = std::fs::read(&gi).unwrap_or_default();
        let mut add = String::new();
        if !existing.is_empty() && !existing.ends_with(b"\n") {
            add.push('\n');
        }
        add.push_str(".pecia/.lock\n");
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&gi).map_err(io)?;
        std::io::Write::write_all(&mut f, add.as_bytes()).map_err(io)?;
    }
    ctx.emit(&obj(vec![
        ("initialized", s(store.snapshot_dir().display().to_string())),
        ("log", s(target.display().to_string())),
        ("rule", s(RULE1)),
    ]));
    Ok(0)
}

#[cfg(test)]
mod tests {
    //! RefusedWritesLeaveTheStoreByteIdentical's primitive arms (pc-d373),
    //! restated: the writer's refusal leaves its target byte-identical, never
    //! creates an absent one, and leaves no staging file — and the control
    //! shows the same writer does write.
    use super::*;
    use pecia_store::Store;

    #[test]
    fn lock_fallback_reads_non_utf8_gitignore_lines() {
        assert!(gitignore_lists_lock(b"*.caf\xe9\r\n.pecia/.lock\r\n"));
        assert!(!gitignore_lists_lock(b"*.caf\xe9.pecia/.lock\n"));
        assert!(!gitignore_lists_lock(b"*.caf\xe9\r.pecia/.lock\n"));
        assert!(!gitignore_lists_lock(b".pecia/.lock\r\r\n"));
    }

    #[test]
    fn union_attribute_parser_handles_non_utf8_neighbors() {
        assert!(legacy_union_declared_in_bytes(
            b"# legacy \xff\n.pecia/work.jsonl text merge=union\n"));
        assert!(!legacy_union_declared_in_bytes(
            b".pecia/work.jsonl\x0bmerge=union\n.pecia/work.jsonl\x0cmerge=union\n"));
        assert!(legacy_union_declared_in_bytes(
            b".pecia/work.jsonl\rmerge=union\n"));
    }

    fn repo(tag: &str) -> Ctx {
        let root = std::env::temp_dir().join(format!("pecia-writer-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("dir");
        assert_eq!(pecia_store::git::code(&root, &["init", "-q"]), Some(0));
        Ctx::new(Store::at(root.canonicalize().expect("root")))
    }

    fn one_entry() -> Vec<Value> {
        let rec = pecia_core::parse::parse(r#"{"id":"pc-aaaa","rev":1,"type":"task","title":"t","status":"open","priority":2,"created":"2026-07-20","updated":"2026-07-20","edges":{"blocks":[],"retires":[]},"disposition":null,"evidence":"unknown","owner":"o","labels":[],"body":""}"#).expect("record");
        vec![pecia_core::write::make_entry(&[], &rec)]
    }

    fn with(mut e: Value, key: &str, v: Value) -> Value {
        pecia_core::write::set(pecia_core::write::pairs_mut(&mut e), key, v);
        e
    }

    fn leftovers(ctx: &Ctx) -> Vec<String> {
        let dir = ctx.store.log_dir().expect("dir");
        std::fs::read_dir(dir).map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| n.contains(".staged.")).collect()).unwrap_or_default()
    }

    fn seed(ctx: &Ctx) -> std::path::PathBuf {
        let target = ctx.store.log_path().expect("log");
        std::fs::create_dir_all(target.parent().expect("dir")).expect("dir");
        std::fs::write(&target, "ORIGINAL CONTENT\n").expect("seed");
        target
    }

    #[test]
    fn kill_the_writer_does_not_touch_its_target_on_refusal() {
        let ctx = repo("untouched");
        let target = seed(&ctx);
        let bad = vec![with(one_entry().remove(0), "seq", Value::Int(7))];
        assert!(write_log_validated(&ctx, &bad, None, None, false).expect("ran").is_err());
        assert_eq!(std::fs::read_to_string(&target).expect("read"), "ORIGINAL CONTENT\n");
        assert!(leftovers(&ctx).is_empty());
    }

    #[test]
    fn kill_the_writer_does_not_create_an_absent_target_on_refusal() {
        let ctx = repo("absent");
        let bad = vec![with(one_entry().remove(0), "prev", Value::Str("0".repeat(64)))];
        assert!(write_log_validated(&ctx, &bad, None, None, false).expect("ran").is_err());
        assert!(!ctx.store.log_path().expect("log").exists());
        assert!(leftovers(&ctx).is_empty());
    }

    #[test]
    fn kill_an_extra_check_refuses_before_the_rename() {
        let ctx = repo("also");
        let target = seed(&ctx);
        let no = |_: &[Value]| Some("the caller says no".to_string());
        assert_eq!(write_log_validated(&ctx, &one_entry(), Some(&no), None, false).expect("ran"), Err("the caller says no".to_string()));
        assert_eq!(std::fs::read_to_string(&target).expect("read"), "ORIGINAL CONTENT\n");
        assert!(leftovers(&ctx).is_empty());
    }

    #[test]
    fn control_the_writer_writes_when_everything_passes() {
        let ctx = repo("green");
        let target = seed(&ctx);
        let parsed = write_log_validated(&ctx, &one_entry(), None, None, false).expect("ran").expect("written");
        assert_eq!(parsed.len(), 1);
        assert_ne!(std::fs::read_to_string(&target).expect("read"), "ORIGINAL CONTENT\n");
        assert!(leftovers(&ctx).is_empty());
    }

    #[test]
    fn the_refusal_check_does_not_write_at_all() {
        let ctx = repo("refusal-only");
        assert_eq!(log_refusal(&one_entry()), None);
        assert!(log_refusal(&[with(one_entry().remove(0), "seq", Value::Int(4))]).is_some());
        assert!(!ctx.store.log_path().expect("log").exists(), "validating must not create the target");
    }
}
