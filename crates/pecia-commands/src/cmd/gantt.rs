//! `gantt`: the milestone timeline — ASCII for a terminal, Mermaid for docs.

use crate::args::Parsed;
use crate::cmd::query::{config, with_heads, Select};
use crate::ctx::{finding_value, Ctx};
use crate::sys;
use pecia_core::board::{columns, row, Board, BOARD_MAX_WIDTH, BOARD_MIN_WIDTH, BOARD_PIPED_WIDTH};
use pecia_core::config::{Config, ConfigValue};
use pecia_core::dates::parse_day;
use pecia_core::finding::{error, Code, Finding};
use pecia_core::query::{mermaid_id, mermaid_label, mermaid_safe_id_display, milestone_is_overdue};
use pecia_core::record::{get, get_str, is_date, strict_int, Pairs};
use pecia_core::text::{render_str, render_value, safe_text, visible_len};
use pecia_core::write::{is_terminal, project, DATE_CAP, TITLE_CAP};
use pecia_core::Value;

pub fn date_part(v: &str) -> String {
    safe_text(v, DATE_CAP).chars().take(10).collect()
}

fn json_type(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Int(_) => "integer",
        Value::Str(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

pub fn project_name(ctx: &Ctx, cfg: &Config, cap: usize) -> String {
    let _ = cap;
    match cfg_value(cfg, "project_name") {
        Some(n) if !n.is_empty() => n,
        _ => ctx.store.root.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
    }
}

fn cfg_value(cfg: &Config, key: &str) -> Option<String> {
    match cfg.get(key) {
        Some(ConfigValue::Str(s)) => Some(s.clone()),
        Some(ConfigValue::Int(n)) => Some(n.to_string()),
        Some(ConfigValue::List(l)) => Some(format!("[{}]", l.iter().map(|x| format!("'{x}'")).collect::<Vec<_>>().join(", "))),
        None => None,
    }
}

const GANTT_DIRECTIVES: &[&str] = &[
    "gantt", "title", "section", "dateformat", "axisformat", "tickinterval", "includes", "excludes",
    "todaymarker", "inclusiveenddates", "topaxis", "displaymode", "weekday", "acctitle", "accdescr", "click",
];

fn task_label(label: &str) -> String {
    let first = label.split(' ').next().unwrap_or("").to_lowercase();
    if !label.is_empty() && GANTT_DIRECTIVES.contains(&first.as_str()) { format!("\"{label}\"") } else { label.to_string() }
}

/// Milestones a chart cannot place: a missing `created`, a date-shaped value
/// that is not a calendar day, or a value that is not date-shaped at all.
fn unprojectable(heads: &[(String, &Pairs)]) -> Vec<Finding> {
    let mut out = Vec::new();
    let mut sorted: Vec<&(String, &Pairs)> = heads.iter().collect();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    for (rid, head) in sorted {
        if get_str(head, "type") != Some("milestone") {
            continue;
        }
        for field in ["created", "target"] {
            let Some(value) = get(head, field) else {
                if field == "created" {
                    out.push(error(Code::E018, Some(rid), "created is ABSENT — it is a required field, `pecia check` refuses this record with E001, and the chart reads it directly, so there is no day to start the bar from. It reached the projection through a write that bypassed the gate or an import that never ran it; restore the created date (v2.17)"));
                }
                continue;
            };
            if let Value::Str(s) = value {
                if is_date(s) {
                    if parse_day(&date_part(s)).is_none() {
                        out.push(error(Code::E018, Some(rid), &format!(
                            "{field} {} is date-shaped but not a calendar day — the structural gates accept it (E001 is shape-only, v1.12) and no chart can place it; repair the {field} (v2.8)",
                            render_str(&date_part(s))
                        )));
                    }
                    continue;
                }
            }
            let shown = match value {
                Value::Str(s) => render_str(&safe_text(s, DATE_CAP)),
                other => render_str(&safe_text(&render_value(other), DATE_CAP)),
            };
            out.push(error(Code::E018, Some(rid), &format!(
                "{field} {shown} ({}) is not date-shaped, so no chart can place it — and unlike the shape-valid case this is NOT state the structural gates accept: `pecia check` refuses this record with E001, which is where to start. It reached the projection through a write that bypassed the gate or an import that never ran it; repair the {field} (v2.13)",
                json_type(value)
            )));
        }
    }
    out
}

fn mermaid_lines(ctx: &Ctx, heads: &[(String, &Pairs)], total: usize, cfg: &Config, cutoff: &str) -> Vec<String> {
    let title = mermaid_label(&project_name(ctx, cfg, TITLE_CAP), TITLE_CAP);
    let mut lines = vec![
        "gantt".to_string(),
        "  dateFormat YYYY-MM-DD".into(),
        format!("  title {title} milestones"),
        "  %% dates are truncated to the day: this chart's dateFormat is YYYY-MM-DD (spec v1.12 allows more precision in the ledger)".into(),
    ];
    let mut milestones: Vec<&Pairs> = heads.iter().map(|(_, h)| *h).filter(|h| get_str(h, "type") == Some("milestone")).collect();
    if milestones.is_empty() {
        lines.push(format!("  %% no milestone records in this ledger ({total} records, 0 of type milestone) — nothing to chart"));
    }
    let key = |h: &Pairs| (get_str(h, "target").filter(|t| !t.is_empty()).unwrap_or("9999").to_string(), get_str(h, "id").unwrap_or("").to_string());
    milestones.sort_by_key(|h| key(h));
    for m in milestones {
        let view = project("gantt", m, cfg);
        let s = |k: &str| view.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let label = mermaid_label(&s("title"), 60);
        let target = view.get("target").and_then(Value::as_str).unwrap_or("");
        if target.is_empty() {
            lines.push(format!("  %% {} {label} — no target date", s("id")));
            continue;
        }
        let state = if is_terminal(get_str(m, "status")) { "done, " } else if milestone_is_overdue(m, &cutoff) { "crit, " } else { "" };
        lines.push(format!(
            "  {} ({}) :{state}{}, {}, {}",
            task_label(&label), mermaid_safe_id_display(&s("id")), mermaid_id(&s("id")), date_part(&s("created")), date_part(target)
        ));
    }
    lines
}

pub fn print_board(ctx: &Ctx, b: &Board, unicode: bool) {
    let text = b.render();
    if unicode {
        ctx.text(text);
    } else {
        // A non-UTF terminal: record text degrades to `?`, as an encode with
        // `replace` would, and never raises.
        ctx.text(text.chars().map(|c| if c.is_ascii() { c } else { '?' }).collect::<String>());
    }
}

pub fn board_width(args: &Parsed) -> usize {
    let width = match args.int("width") {
        Some(w) if w != 0 => w.max(0) as usize,
        _ => if sys::stdout_is_tty() { sys::terminal_columns() } else { BOARD_PIPED_WIDTH },
    };
    width.clamp(BOARD_MIN_WIDTH, BOARD_MAX_WIDTH)
}

fn ascii(ctx: &Ctx, heads: &[(String, &Pairs)], total: usize, cfg: &Config, args: &Parsed, cutoff: &str) {
    let unicode = sys::supports_unicode();
    let mut b = Board::new(board_width(args), sys::supports_color(args.flag("no_color")), unicode);
    let name = safe_text(&project_name(ctx, cfg, TITLE_CAP), TITLE_CAP);
    let milestones: Vec<&Pairs> = heads.iter().map(|(_, h)| *h).filter(|h| get_str(h, "type") == Some("milestone")).collect();
    if milestones.is_empty() {
        b.rule(&format!("{name} milestones"), &format!("no milestone records in this ledger ({total} records, 0 of type milestone) — nothing to chart"), false);
        print_board(ctx, &b, unicode);
        return;
    }
    let today = parse_day(cutoff).unwrap_or(0);
    let has_target = |h: &&Pairs| get_str(h, "target").is_some_and(|t| !t.is_empty());
    let mut dated: Vec<&Pairs> = milestones.iter().copied().filter(has_target).collect();
    dated.sort_by_key(|h| (get_str(h, "target").unwrap_or("").to_string(), get_str(h, "id").unwrap_or("").to_string()));
    let mut undated: Vec<&Pairs> = milestones.iter().copied().filter(|h| !has_target(h)).collect();
    undated.sort_by_key(|h| (strict_int(get(h, "priority")).unwrap_or(2), get_str(h, "id").unwrap_or("").to_string()));
    let sep = b.g.sep;
    let note = if !undated.is_empty() {
        format!("{} dated {sep} {} undated {sep} today {cutoff}", dated.len(), undated.len())
    } else {
        format!("{} {sep} today {cutoff}", dated.len())
    };
    b.rule(&format!("{name} milestones"), &note, false);
    if !dated.is_empty() {
        let (id_w, type_w) = columns(&b, &dated);
        let day = |h: &Pairs, k: &str| parse_day(&date_part(get_str(h, k).unwrap_or(""))).unwrap_or(0);
        let domain_start = dated.iter().map(|h| day(h, "created")).min().unwrap_or(today);
        let max_end = dated.iter().map(|h| day(h, "target")).chain(std::iter::once(today)).max().unwrap_or(today);
        let span = (max_end - domain_start).max(1);
        let status_note = |h: &Pairs| -> String {
            if is_terminal(get_str(h, "status")) {
                return format!("{} done", b.g.ok);
            }
            let target = day(h, "target");
            if milestone_is_overdue(h, &cutoff) {
                return format!("{} overdue {}d", b.g.bad, today - target);
            }
            let remaining = target - today;
            if remaining == 0 { "due today".into() } else { format!("{remaining}d left") }
        };
        let notes: Vec<String> = dated.iter().map(|h| status_note(h)).collect();
        let widest = notes.iter().map(|n| visible_len(n)).max().unwrap_or(0);
        let track_width = (b.width as i64 - 34 - widest as i64).clamp(10, 40) as usize;
        let col = |d: i64| -> usize {
            let offset = (d - domain_start).clamp(0, span) as f64;
            (offset / span as f64 * track_width as f64).round_ties_even() as usize
        };
        for (h, plain) in dated.iter().zip(&notes) {
            let start_col = col(day(h, "created")).min(track_width - 1);
            let end_col = (start_col + 1).max(col(day(h, "target")).min(track_width));
            let full = b.g.full;
            let (segments, glyph_key, style): (Vec<(usize, usize, &str, &[&str])>, &str, &str) = if is_terminal(get_str(h, "status")) {
                (vec![(start_col, end_col, full, &["green"])], "ok", "green")
            } else if milestone_is_overdue(h, &cutoff) {
                let overrun = end_col.max(col(today).min(track_width));
                (vec![(start_col, end_col, full, &["byellow"]), (end_col, overrun, full, &["bred"])], "bad", "bred")
            } else {
                let elapsed = start_col.max(col(today).min(end_col));
                (vec![(start_col, elapsed, full, &["bcyan"]), (elapsed, end_col, b.g.empty, &["grey"])], "open", "grey")
            };
            let r = row(&b, h, glyph_key, id_w, type_w, cfg);
            b.add(r);
            let mut track = String::new();
            let mut pos = 0;
            for (start, end, glyph, st) in &segments {
                if *start > pos {
                    track.push_str(&" ".repeat(start - pos));
                }
                track.push_str(&b.paint(&glyph.repeat(end.saturating_sub(*start)), st));
                pos = *end;
            }
            if pos < track_width {
                track.push_str(&" ".repeat(track_width - pos));
            }
            let pipe = b.g.pipe;
            let bar = format!("        {} {pipe}{track}{pipe} {}", date_part(get_str(h, "created").unwrap_or("")), date_part(get_str(h, "target").unwrap_or("")));
            if visible_len(&bar) + 2 + visible_len(plain) <= b.width {
                let line = format!("{bar}  {}", b.paint(plain, &[style]));
                b.add(line);
            } else {
                b.add(bar);
                let line = format!("        {}", b.paint(plain, &[style]));
                b.add(line);
            }
        }
    }
    if !undated.is_empty() {
        b.add("");
        let line = format!("   {}", b.paint("no target date", &["grey"]));
        b.add(line);
        let (id_w, type_w) = columns(&b, &undated);
        for h in &undated {
            let r = row(&b, h, "open", id_w, type_w, cfg);
            b.add(r);
        }
    }
    print_board(ctx, &b, unicode);
}

pub fn gantt(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let cfg = config(ctx)?;
    let cutoff = sys::today()?;
    // A chart reads the milestones, and the count of everything else.
    let got = with_heads(ctx, Select::Kind("milestone"), |heads, total, _| {
        let bad = unprojectable(heads);
        if !bad.is_empty() {
            for f in &bad {
                ctx.emit(&finding_value(f));
            }
            return 1u8;
        }
        if args.flag("mermaid") {
            ctx.text(mermaid_lines(ctx, heads, total, &cfg, &cutoff).join("\n"));
        } else {
            ascii(ctx, heads, total, &cfg, args, &cutoff);
        }
        0u8
    })?;
    match got {
        Ok(code) => Ok(code),
        Err(m) => Ok(ctx.cannot_run(&m)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// MermaidIdInjection's agreement arm (pc-87e0): dev/report.py keeps its
    /// own directive list, independently, as a consumer of the format; the
    /// two must name the same words.
    #[test]
    fn the_two_directive_lists_agree() {
        let report = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../dev/report.py")).expect("dev/report.py");
        let start = report.find("GANTT_DIRECTIVES = frozenset({").expect("the list");
        let body = &report[start..start + report[start..].find("})").expect("closed")];
        let mut theirs: Vec<&str> = body.split('"').skip(1).step_by(2).collect();
        let mut ours: Vec<&str> = GANTT_DIRECTIVES.to_vec();
        theirs.sort();
        ours.sort();
        assert_eq!(ours, theirs);
    }
}
