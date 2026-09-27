//! The human projections' renderer (`board`, `gantt`): a width-bounded list
//! of lines, painted only when colour is on. Pure string building — whether
//! colour and Unicode are available is the caller's to decide.
//!
//! Every width here is measured off the COMPOSED line, never restated as a
//! constant: this renderer's whole history of overflow was a format string and
//! a parallel column tally drifting apart.

use crate::record::Pairs;
use crate::text::{char_cols, safe_id, safe_text, visible_len};
use crate::config::Config;

pub const BOARD_MIN_WIDTH: usize = 60;
pub const BOARD_MAX_WIDTH: usize = 160;
pub const BOARD_PIPED_WIDTH: usize = 80;

pub struct Glyphs {
    pub h: &'static str,
    pub rule: &'static str,
    pub tee: &'static str,
    pub end: &'static str,
    pub pipe: &'static str,
    pub full: &'static str,
    pub empty: &'static str,
    pub open: &'static str,
    pub ready: &'static str,
    pub active: &'static str,
    pub ok: &'static str,
    pub bad: &'static str,
    pub sep: &'static str,
}

pub const ASCII: Glyphs = Glyphs { h: "-", rule: "=", tee: "|-", end: "`-", pipe: "|", full: "#", empty: ".", open: "o", ready: "*", active: "@", ok: "ok", bad: "X", sep: "-" };
pub const UNICODE: Glyphs = Glyphs { h: "─", rule: "═", tee: "├", end: "└", pipe: "│", full: "█", empty: "░", open: "○", ready: "●", active: "◐", ok: "✓", bad: "✗", sep: "·" };

impl Glyphs {
    pub fn get(&self, key: &str) -> &'static str {
        match key {
            "h" => self.h, "rule" => self.rule, "tee" => self.tee, "end" => self.end,
            "pipe" => self.pipe, "full" => self.full, "empty" => self.empty, "open" => self.open,
            "ready" => self.ready, "active" => self.active, "ok" => self.ok, "bad" => self.bad,
            _ => self.sep,
        }
    }
}

fn sgr(name: &str) -> Option<&'static str> {
    Some(match name {
        "bold" => "1", "dim" => "2", "red" => "31", "green" => "32", "yellow" => "33",
        "blue" => "34", "magenta" => "35", "cyan" => "36", "grey" => "90", "bred" => "91",
        "bgreen" => "92", "byellow" => "93", "bblue" => "94", "bmagenta" => "95", "bcyan" => "96",
        _ => return None,
    })
}

pub fn priority_style(p: i64) -> &'static [&'static str] {
    match p {
        0 => &["bred", "bold"],
        1 => &["byellow"],
        2 => &["bcyan"],
        3 => &["grey"],
        _ => &["bcyan"],
    }
}

pub fn type_style(t: &str) -> &'static [&'static str] {
    match t {
        "defect" => &["red"], "task" => &["blue"], "question" => &["magenta"],
        "decision" => &["green"], "milestone" => &["yellow"], _ => &[],
    }
}

pub fn status_style(s: &str) -> &'static [&'static str] {
    match s {
        "open" => &["byellow"], "in-progress" => &["bcyan", "bold"], "done" => &["green"],
        "dropped" => &["grey"], "superseded" => &["magenta"], _ => &[],
    }
}

pub struct Board {
    pub width: usize,
    pub color: bool,
    pub g: &'static Glyphs,
    pub lines: Vec<String>,
}

impl Board {
    pub fn new(width: usize, color: bool, unicode: bool) -> Board {
        Board { width, color, g: if unicode { &UNICODE } else { &ASCII }, lines: Vec::new() }
    }

    pub fn paint(&self, text: &str, names: &[&str]) -> String {
        let codes: Vec<&str> = names.iter().filter_map(|n| sgr(n)).collect();
        if !self.color || codes.is_empty() || text.is_empty() {
            return text.to_string();
        }
        format!("\x1b[{}m{text}\x1b[0m", codes.join(";"))
    }

    pub fn link(&self, url: &str, text: &str) -> String {
        if !self.color {
            return text.to_string();
        }
        format!("\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\")
    }

    pub fn add(&mut self, text: impl Into<String>) {
        self.lines.push(text.into());
    }

    /// A section rule: `── label ───────── note`. A note too wide to sit beside
    /// its label SPILLS to its own lines rather than being cut — a note is
    /// supplementary in layout and sometimes load-bearing in meaning ("—
    /// nothing to chart"), and only the layout may yield.
    pub fn rule(&mut self, label: &str, note: &str, heavy: bool) {
        let glyph = if heavy { self.g.rule } else { self.g.h };
        let width = self.width;
        let composed = |lab: &str, nt: &str| -> String {
            let tail = if nt.is_empty() { String::new() } else { format!(" {nt}") };
            let fill = (width as i64 - (visible_len(lab) as i64 + 4) - visible_len(&tail) as i64).max(1) as usize;
            format!("{} {lab} {}{tail}", glyph.repeat(2), glyph.repeat(fill))
        };
        let (mut label, mut note) = (label.to_string(), note.to_string());
        let mut spill = String::new();
        if !note.is_empty() && visible_len(&composed(&label, &note)) > width {
            spill = std::mem::take(&mut note);
        }
        let over = visible_len(&composed(&label, &note)) as i64 - width as i64;
        if over > 0 {
            label = budget_clip(&label, (visible_len(&label) as i64 - over).max(1) as usize);
        }
        let tail = if note.is_empty() { String::new() } else { format!(" {note}") };
        let fill = (width as i64 - (visible_len(&label) as i64 + 4) - visible_len(&tail) as i64).max(1) as usize;
        let line = self.paint(&format!("{} ", glyph.repeat(2)), &["grey"])
            + &self.paint(&label, &["bold"])
            + &self.paint(&format!(" {}", glyph.repeat(fill)), &["grey"])
            + &if tail.is_empty() { String::new() } else { self.paint(&tail, &["grey"]) };
        self.add(line);
        if !spill.is_empty() {
            let indent = "   ";
            let budget = self.width.saturating_sub(indent.len()).max(1);
            for ln in textwrap(&safe_text(&spill, 0), budget) {
                let l = self.paint(&format!("{indent}{}", budget_clip(&ln, budget)), &["grey"]);
                self.add(l);
            }
        }
    }

    pub fn render(&self) -> String {
        self.lines.iter().map(|l| l.trim_end().to_string()).collect::<Vec<_>>().join("\n")
    }
}

/// The first `cols` terminal columns of `text`.
pub fn take_cols(text: &str, cols: usize) -> String {
    let mut out = String::new();
    let mut used = 0;
    for ch in text.chars() {
        let w = char_cols(ch);
        if used + w > cols {
            break;
        }
        out.push(ch);
        used += w;
    }
    out
}

/// Clip to a width with a trailing ellipsis; widths of 1 or less are left alone.
pub fn clip(text: &str, width: usize) -> String {
    let text = safe_text(text, 0);
    if width <= 1 || visible_len(&text) <= width {
        return text;
    }
    take_cols(&text, width - 1) + "…"
}

/// Clip to a hard budget: never wider than `width`, even at width 1.
pub fn budget_clip(text: &str, width: usize) -> String {
    let text = safe_text(text, 0);
    if width == 0 {
        return String::new();
    }
    if visible_len(&text) <= width {
        return text;
    }
    if width > 1 { take_cols(&text, width - 1) + "…" } else { "…".into() }
}

pub fn pad_to(b: &Board, text: &str, width: usize, style: &[&str]) -> String {
    b.paint(text, style) + &" ".repeat(width.saturating_sub(visible_len(text)))
}

/// A row's text after its prefix, clipped to what is left. Below `floor`
/// remaining columns the text moves to its own indented line instead.
pub fn fit(b: &Board, prefix: &str, text: &str, suffix: &str, floor: usize, style: &[&str]) -> String {
    let room = b.width as i64 - visible_len(prefix) as i64 - visible_len(suffix) as i64;
    if room < floor as i64 {
        let indent = " ".repeat(8);
        let room = (b.width as i64 - indent.len() as i64 - visible_len(suffix) as i64).max(1) as usize;
        return format!("{}\n{indent}{}{suffix}", prefix.trim_end(), b.paint(&clip(text, room), style));
    }
    format!("{prefix}{}{suffix}", b.paint(&clip(text, (room as usize).max(floor)), style))
}

pub fn column_width<'a>(values: impl Iterator<Item = &'a str>) -> usize {
    values.map(visible_len).max().unwrap_or(1)
}

pub fn row_prefix(b: &Board, rid: &str, id_width: usize, rtype: &str, type_width: usize, priority: i64, glyph_key: &str) -> String {
    let gutter = b.paint(&format!("p{priority}"), priority_style(priority));
    let mark = b.paint(b.g.get(glyph_key), priority_style(priority));
    format!(
        "   {gutter} {mark} {} {}  ",
        pad_to(b, rid, id_width, &["bold"]),
        pad_to(b, &budget_clip(rtype, type_width), type_width, type_style(rtype))
    )
}

/// The id and type column widths for a set of rows.
pub fn columns(b: &Board, rows: &[&Pairs]) -> (usize, usize) {
    let ids: Vec<String> = rows.iter().map(|r| safe_id(crate::record::get_str(r, "id").unwrap_or(""))).collect();
    let types: Vec<String> = rows.iter().map(|r| safe_text(crate::record::get_str(r, "type").unwrap_or(""), crate::write::VOCAB_CAP)).collect();
    let fixed = visible_len(&row_prefix(b, "", 0, "", 0, 2, "open"));
    let room = b.width.saturating_sub(fixed).max(2);
    let id_w = column_width(ids.iter().map(String::as_str)).min(room - 1).max(1);
    let type_w = column_width(types.iter().map(String::as_str)).min(room.saturating_sub(id_w)).max(1);
    (id_w, type_w)
}

pub fn row(b: &Board, head: &Pairs, glyph_key: &str, id_width: usize, type_width: usize, cfg: &Config) -> String {
    let view = crate::write::project("board", head, cfg);
    let get = |k: &str| view.get(k).cloned();
    let priority = get("priority").and_then(|v| v.as_i64()).unwrap_or(2);
    let rid = get("id").and_then(|v| v.as_str().map(str::to_string)).filter(|s| !s.is_empty()).unwrap_or_else(|| "?".into());
    let rtype = get("type").and_then(|v| v.as_str().map(str::to_string)).filter(|s| !s.is_empty()).unwrap_or_else(|| "?".into());
    let title = get("title").and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default();
    fit(b, &row_prefix(b, &rid, id_width, &rtype, type_width, priority, glyph_key), &title, "", 10, &[])
}

fn is_letter(c: char) -> bool {
    c.is_alphabetic()
}

/// `textwrap.wrap(text, width)` with its defaults: whitespace collapses,
/// hyphenated words may break after a hyphen between letters, and a word
/// longer than the width is broken at the width.
pub fn textwrap(text: &str, width: usize) -> Vec<String> {
    // Chunks: runs of whitespace, and words split after an in-word hyphen
    // preceded by two letters and followed by a letter.
    let mut chunks: Vec<String> = Vec::new();
    for word in text.split_inclusive(|c: char| c.is_whitespace()) {
        let (w, ws) = match word.char_indices().last() {
            Some((i, c)) if c.is_whitespace() => (&word[..i], " "),
            _ => (word, ""),
        };
        let chars: Vec<char> = w.chars().collect();
        let mut start = 0;
        for i in 0..chars.len() {
            if chars[i] == '-' && i >= 2 && is_letter(chars[i - 1]) && is_letter(chars[i - 2]) && chars.get(i + 1).is_some_and(|c| is_letter(*c)) {
                chunks.push(chars[start..=i].iter().collect());
                start = i + 1;
            }
        }
        if start < chars.len() {
            chunks.push(chars[start..].iter().collect());
        }
        if !ws.is_empty() {
            chunks.push(" ".into());
        }
    }
    let mut lines: Vec<String> = Vec::new();
    let mut cur: Vec<String> = Vec::new();
    let mut cur_len = 0usize;
    chunks.reverse();
    while !chunks.is_empty() {
        cur.clear();
        cur_len = 0;
        if chunks.last().is_some_and(|c| c.trim().is_empty()) && !lines.is_empty() {
            chunks.pop();
        }
        while let Some(chunk) = chunks.last() {
            let l = chunk.chars().count();
            if cur_len + l <= width {
                cur_len += l;
                cur.push(chunks.pop().expect("chunk"));
            } else {
                break;
            }
        }
        if let Some(chunk) = chunks.last() {
            if chunk.chars().count() > width {
                let space_left = if width < 1 { 1 } else { width - cur_len };
                if space_left > 0 {
                    let c = chunks.pop().expect("chunk");
                    let head: String = c.chars().take(space_left).collect();
                    let rest: String = c.chars().skip(space_left).collect();
                    cur.push(head);
                    cur_len += space_left;
                    chunks.push(rest);
                } else if cur.is_empty() {
                    cur.push(chunks.pop().expect("chunk"));
                }
            }
        }
        if cur.last().is_some_and(|c| c.trim().is_empty()) {
            cur_len -= cur.last().map_or(0, |c| c.chars().count());
            cur.pop();
        }
        if !cur.is_empty() {
            lines.push(cur.concat());
        }
    }
    let _ = cur_len;
    lines
}
