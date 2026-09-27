//! `board`: the read-only human projection — the whole ledger on one screen,
//! ANSI when a terminal can show it. Not JSON, like `gantt`. Exit 1 when the
//! checker rejects the ledger, so a glance and a script agree.

use crate::args::Parsed;
use crate::cmd::audit::{options, Env};
use crate::cmd::gantt::{board_width, print_board, project_name};
use crate::cmd::query::config;
use crate::cmd::store_cmds::load_entries;
use crate::ctx::{Ctx, RULE1};
use crate::sys;
use pecia_core::audit::audit_findings;
use pecia_core::board::{clip, column_width, columns, fit, pad_to, row, status_style, type_style, Board};
use pecia_core::check::run_checks;
use pecia_core::config::Config;
use pecia_core::finding::Severity;
use pecia_core::query::{compute_blockers, priority, ready};
use pecia_core::record::{get_str, Pairs};
use pecia_core::text::{safe_id, safe_text, visible_len};
use pecia_core::write::{require_heads, TITLE_CAP, VOCAB_CAP};
use pecia_core::Value;
use std::collections::{BTreeMap, HashMap};

/// `parts` joined by `sep` after `prefix`, wrapping to an indent of the
/// prefix's width when the next part would pass the board's edge.
fn wrapped(b: &mut Board, prefix: &str, parts: &[String], sep: &str) {
    let indent = " ".repeat(visible_len(prefix));
    let mut line = prefix.to_string();
    for (i, part) in parts.iter().enumerate() {
        if i > 0 && visible_len(&line) + visible_len(sep) + visible_len(part) > b.width {
            b.add(std::mem::take(&mut line));
            line = format!("{indent}{part}");
        } else if i == 0 {
            line.push_str(part);
        } else {
            line.push_str(sep);
            line.push_str(part);
        }
    }
    b.add(line);
}

fn label(h: &Pairs, key: &str) -> String {
    let t = safe_text(get_str(h, key).unwrap_or("?"), VOCAB_CAP);
    if t.is_empty() { "?".into() } else { t }
}

/// The status bar and legend, then the type counts.
fn distribution(b: &mut Board, heads: &[(String, &Pairs)]) {
    let mut by_status: BTreeMap<String, usize> = BTreeMap::new();
    let mut by_type: BTreeMap<String, usize> = BTreeMap::new();
    for (_, h) in heads {
        *by_status.entry(label(h, "status")).or_default() += 1;
        *by_type.entry(label(h, "type")).or_default() += 1;
    }
    let total = by_status.values().sum::<usize>().max(1);
    let sep = b.paint(&format!(" {} ", b.g.sep), &["grey"]);
    let mut order: Vec<&String> = by_status.keys().collect();
    order.sort_by(|x, y| by_status[*y].cmp(&by_status[*x]).then(x.cmp(y)));
    let legend: Vec<String> = order.iter().map(|s| b.paint(&format!("{} {s}", by_status[*s]), status_style(s))).collect();
    let lead = format!("   {}  ", b.paint("status", &["grey"]));
    let room = b.width as i64 - visible_len(&lead) as i64 - 2
        - legend.iter().map(|x| visible_len(x) as i64).sum::<i64>()
        - visible_len(&sep) as i64 * (legend.len() as i64 - 1);
    let bar_width = if room >= 10 { room.min(40) as usize } else { 0 };
    let mut bar = String::new();
    if bar_width > 0 {
        let mut cells: BTreeMap<&String, usize> = by_status.iter().map(|(s, n)| (s, (n * bar_width / total).max(1))).collect();
        while cells.values().sum::<usize>() > bar_width {
            let widest = cells.iter().max_by(|x, y| x.1.cmp(y.1).then(x.0.cmp(y.0))).map(|(s, _)| *s).expect("cells");
            *cells.get_mut(widest).expect("cell") -= 1;
        }
        for s in &order {
            bar += &b.paint(&b.g.full.repeat(cells[s]), status_style(s));
        }
        bar += "  ";
    }
    wrapped(b, &(lead + &bar), &legend, &sep);
    let type_room = b.width.saturating_sub(visible_len("   type    ")).max(1);
    let mut types: Vec<(&String, &usize)> = by_type.iter().collect();
    types.sort_by(|x, y| y.1.cmp(x.1).then(x.0.cmp(y.0)));
    let types: Vec<String> = types.iter().map(|(t, n)| b.paint(&clip(&format!("{t} {n}"), type_room), type_style(t))).collect();
    let prefix = format!("   {}    ", b.paint("type", &["grey"]));
    wrapped(b, &prefix, &types, &sep);
}

fn section(b: &mut Board, cfg: &Config, label: &str, rows: &[&Pairs], glyph: &str, empty_note: &str, note: Option<String>) {
    b.add("");
    if rows.is_empty() {
        b.rule(label, empty_note, false);
        return;
    }
    b.rule(label, &note.unwrap_or_else(|| rows.len().to_string()), false);
    let (id_w, type_w) = columns(b, rows);
    for r in rows {
        let line = row(b, r, glyph, id_w, type_w, cfg);
        b.add(line);
    }
}

pub fn board(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let (entries, findings) = load_entries(ctx)?;
    let records = pecia_core::check::records_of(&entries);
    let heads = match require_heads(&records, &findings) {
        Ok(h) => h,
        Err(m) => return Ok(ctx.cannot_run(&m)),
    };
    let cfg = config(ctx)?;
    let env = Env::new(&ctx.store.root);
    let audit = match audit_findings(&heads, &records, &cfg, &options(0, false, false)?, &env) {
        Ok(a) => a,
        Err(m) => return Ok(ctx.cannot_run(&m)),
    };
    let unicode = sys::supports_unicode();
    let mut b = Board::new(board_width(args), sys::supports_color(args.flag("no_color")), unicode);
    let errors = run_checks(&records, Vec::new(), &cfg, None).iter().filter(|f| f.severity == Severity::Error).count();
    let blocked_by = compute_blockers(&heads);
    let has_blockers = |id: &str| blocked_by.get(id).is_some_and(|v| !v.is_empty());
    let by_id: HashMap<&str, &Pairs> = heads.iter().map(|(id, h)| (id.as_str(), *h)).collect();
    let ready_rows = ready(&heads, None);
    let sorted_by = |status: &str, blocked: Option<bool>| -> Vec<&Pairs> {
        let mut rows: Vec<(&String, &Pairs)> = heads
            .iter()
            .filter(|(id, h)| get_str(h, "status") == Some(status) && blocked.is_none_or(|want| has_blockers(id) == want))
            .map(|(id, h)| (id, *h))
            .collect();
        rows.sort_by(|x, y| (priority(x.1), x.0).cmp(&(priority(y.1), y.0)));
        rows.into_iter().map(|(_, h)| h).collect()
    };
    let blocked = sorted_by("open", Some(true));
    let active = sorted_by("in-progress", None);

    let name = safe_text(&project_name(ctx, &cfg, TITLE_CAP), TITLE_CAP);
    let state = if errors == 0 {
        b.paint(&format!("{} check clean", b.g.ok), &["bgreen"])
    } else {
        b.paint(&format!("{} check {errors} error(s)", b.g.bad), &["bred"])
    };
    let plural = if heads.len() == 1 { "record" } else { "records" };
    b.rule(&name, &format!("{} {plural} {} {state}", heads.len(), b.g.sep), true);
    let snapshot = ctx.store.snapshot_path();
    let parts = [
        b.paint(&b.link(&format!("file://{}", snapshot.display()), &ctx.store.display(&snapshot)), &["grey"]),
        b.paint(RULE1, &["dim"]),
    ];
    wrapped(&mut b, "   ", &parts, "  ");
    b.add("");
    distribution(&mut b, &heads);

    let capped = ready_rows.iter().filter(|h| priority(h) > 3).count();
    let sep = b.g.sep;
    let mut note = format!("{} {sep} total order", ready_rows.len());
    if capped > 0 {
        note += &format!(" {sep} {capped} below `next` cut-off");
    }
    section(&mut b, &cfg, "ready", &ready_rows, "ready", "nothing ready — every open record is blocked", Some(note));
    section(&mut b, &cfg, "in progress", &active, "active", "nobody is working on anything", None);
    b.add("");
    if blocked.is_empty() {
        b.rule("blocked", "nothing is blocked", false);
    } else {
        b.rule("blocked", &blocked.len().to_string(), false);
        let (id_w, type_w) = columns(&b, &blocked);
        for h in &blocked {
            let line = row(&b, h, "open", id_w, type_w, &cfg);
            b.add(line);
            let ids = blocked_by.get(get_str(h, "id").unwrap_or("")).cloned().unwrap_or_default();
            let is_question = |i: &String| by_id.get(i.as_str()).is_some_and(|d| get_str(d, "type") == Some("question"));
            let questions: Vec<&String> = ids.iter().filter(|i| is_question(i)).collect();
            let others: Vec<&String> = ids.iter().filter(|i| !is_question(i)).collect();
            let groups: Vec<(&str, Vec<(String, &String)>)> = [("blocked by", others), ("awaiting", questions)]
                .into_iter()
                .filter(|(_, items)| !items.is_empty())
                .map(|(lbl, items)| (lbl, items.into_iter().map(|i| (safe_id(i), i)).collect()))
                .collect();
            let label_width = column_width(groups.iter().map(|(l, _)| *l));
            let dep_stem = format!("        {} {}  ", b.g.tee, " ".repeat(label_width));
            let dep_width = column_width(groups.iter().flat_map(|(_, items)| items.iter().map(|(d, _)| d.as_str())))
                .min(b.width.saturating_sub(visible_len(&dep_stem)).max(1));
            for (gi, (lbl, items)) in groups.iter().enumerate() {
                let last_group = gi == groups.len() - 1;
                for (ii, (dep, raw)) in items.iter().enumerate() {
                    let last = last_group && ii == items.len() - 1;
                    let stem = if last { b.g.end } else { b.g.tee };
                    let dep_head = by_id.get(raw.as_str()).copied();
                    let dep_status = dep_head.map_or_else(|| "?".to_string(), |d| label(d, "status"));
                    let shown = format!("{:<label_width$}", if ii == 0 { *lbl } else { "" });
                    let prefix = format!("        {}{}  ", b.paint(&format!("{stem} {shown}  "), &["grey"]), pad_to(&b, dep, dep_width, &["bold"]));
                    let title = dep_head.and_then(|d| get_str(d, "title")).unwrap_or("?");
                    let suffix = format!(" {}", b.paint(&format!("({dep_status})"), status_style(&dep_status)));
                    let line = fit(&b, &prefix, title, &suffix, 10, &[]);
                    b.add(line);
                }
            }
        }
    }
    b.add("");
    if audit.is_empty() {
        b.rule("audit", "no advisory findings", false);
    } else {
        b.rule("audit", &format!("{} advisory {} never blocking", audit.len(), b.g.sep), false);
        let text = |f: &Value, k: &str| f.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let kind_width = column_width(audit.iter().filter_map(|f| f.get("kind").and_then(Value::as_str)));
        let mut sorted: Vec<&Value> = audit.iter().collect();
        sorted.sort_by_key(|f| (text(f, "kind"), text(f, "id")));
        for f in sorted {
            let id = text(f, "id");
            let prefix = format!(
                "   {}  {}  ",
                b.paint(&format!("{:<kind_width$}", text(f, "kind")), &["yellow"]),
                b.paint(if id.is_empty() { "-" } else { &id }, &["bold"])
            );
            let line = fit(&b, &prefix, &text(f, "note"), "", 20, &["grey"]);
            b.add(line);
        }
    }
    b.add("");
    print_board(ctx, &b, unicode);
    Ok(if errors > 0 { 1 } else { 0 })
}
