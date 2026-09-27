//! `pecia check` — the structural checker. RULE 1: exit 0 means
//! "well-formed," never "true."

use crate::ctx::{finding_value, obj, Ctx, RULE1};
use crate::args::Parsed;
use pecia_core::check::{has_error, read_log, read_record_lines, run_checks, timeline_checks, SnapshotFiles};
use pecia_core::config::Config;
use pecia_core::finding::Severity;
use pecia_core::Value;
use pecia_store::read_optional;

pub fn run(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let store = &ctx.store;
    let config_path = args.str("config").map(std::path::PathBuf::from).unwrap_or_else(|| store.config_path());
    let cfg = match read_optional(&config_path)? {
        Some(b) => Config::parse(&String::from_utf8_lossy(&b)),
        None => Config::default(),
    };

    let (record_count, findings) = if let Some(ledger) = args.str("ledger") {
        let Some(bytes) = read_optional(std::path::Path::new(ledger))? else {
            return Ok(ctx.cannot_run(&format!("no ledger at {ledger}")));
        };
        let (records, parse_findings, lines) = read_record_lines(&bytes);
        let findings = run_checks(&records.iter().collect::<Vec<_>>(), parse_findings, &cfg, Some(&lines));
        (records.len(), findings)
    } else {
        let Some(log) = store.log_path() else {
            return Ok(ctx.cannot_run("not inside a git repository — the timeline lives under --git-common-dir (spec/format-v2.md 3.1)"));
        };
        let Some(bytes) = read_optional(&log)? else {
            let has_remote = store.git(&["remote"]).is_some_and(|r| !r.is_empty());
            return Ok(ctx.cannot_run(if has_remote {
                "no timeline yet, and this clone has a remote configured — run `pecia sync` first (safe by default: it is how a fresh clone of an already-published repo gets the timeline). If sync says nothing is published yet, run `pecia migrate` to build one from this repo's history, or `pecia init` if it has no pecia history either"
            } else {
                "no timeline yet — run `pecia migrate` to build one from this repo's history, or `pecia init` in a fresh repo"
            }));
        };
        let (entries, parse_findings) = read_log(&bytes);
        let log_complete = !has_error(&parse_findings);
        let records = pecia_core::check::records_of(&entries);
        let lines: Vec<usize> = (1..=records.len()).collect();
        let mut findings = run_checks(&records, parse_findings, &cfg, Some(&lines));
        let (sp, hp) = (store.snapshot_path(), store.snapshot_head_path());
        let (proj, head) = (read_optional(&sp)?, read_optional(&hp)?);
        let (sd, hd) = (store.display(&sp), store.display(&hp));
        let mp = store.mark_path();
        let mark = match &mp {
            Some(p) => read_optional(p)?,
            None => None,
        };
        let md = mp.as_deref().map(|p| store.display(p)).unwrap_or_default();
        findings.extend(timeline_checks(&entries, Some(&SnapshotFiles {
            projection: proj.as_deref(),
            head: head.as_deref(),
            projection_display: &sd,
            head_display: &hd,
            mark: mark.as_deref(),
            mark_display: &md,
        }), log_complete));
        (records.len(), findings)
    };

    for f in &findings {
        ctx.emit(&finding_value(f));
    }
    if has_error(&findings) {
        return Ok(1);
    }
    let warnings = findings.iter().filter(|f| f.severity == Severity::Warning).count();
    ctx.emit(&obj(vec![
        ("ok", Value::Bool(true)),
        ("note", Value::Str(RULE1.into())),
        ("records", Value::Int(record_count as i64)),
        ("warnings", Value::Int(warnings as i64)),
    ]));
    Ok(0)
}
