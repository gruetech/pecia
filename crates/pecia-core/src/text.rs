//! Bounding authored text so it cannot escape the frame it is emitted in.
//!
//! Buys FORM, not TRUTH: a title reading "ignore all previous instructions"
//! passes through unchanged, deliberately — a heuristic "does this look like an
//! instruction" check over a field the writer controls is satisfied or evaded
//! to order. What this removes is the ability to BREAK THE FRAME: control and
//! format code points (ANSI escapes, bidi overrides, zero-width marks), newlines
//! and tabs (a faked message boundary), fence openers (escaping a code block),
//! and volume.
//!
//! Truncation is IDENTITY-PRESERVING: a truncated value carries a digest of the
//! original, because things downstream compare these strings and a bare "…"
//! would make two different values equal. Distinctness holds to 2^32; a bound,
//! not a proof.

use crate::canonical::canonical;
use crate::value::Value;
use sha2::{Digest, Sha256};

/// The assembled diagnostic's ceiling.
pub const MESSAGE_CAP: usize = 8192;
/// One authored value or participant list inside a message.
pub const MESSAGE_VALUE_CAP: usize = 512;
/// One element inside such a list.
pub const MESSAGE_ITEM_CAP: usize = 128;
/// An id as emitted. `pc-`+hash is 15; adapter-derived ids stay short.
pub const ID_CAP: usize = 512;

/// Unicode general category Cf (format), Unicode 15.0 — generated from the
/// reference implementation's `unicodedata`, so the two strip the same set.
/// Cc is `char::is_control` exactly; Cs cannot occur in a Rust string.
const FORMAT_CHARS: &[(u32, u32)] = &[
    (0x00AD, 0x00AD),
    (0x0600, 0x0605),
    (0x061C, 0x061C),
    (0x06DD, 0x06DD),
    (0x070F, 0x070F),
    (0x0890, 0x0891),
    (0x08E2, 0x08E2),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x206F),
    (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB),
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
    (0x13430, 0x1343F),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0001, 0xE0001),
    (0xE0020, 0xE007F),
];

fn is_format(c: char) -> bool {
    let cp = c as u32;
    FORMAT_CHARS.iter().any(|&(lo, hi)| (lo..=hi).contains(&cp))
}

/// The whitespace this layer normalises: Rust's White_Space plus the four
/// information separators U+001C–U+001F, which the reference implementation's
/// `str.isspace` counts (bidi class B/S) and `char::is_whitespace` does not.
/// Measured against it exhaustively: those four are the only difference.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// First eight hex of SHA-256 over the ORIGINAL text.
pub fn digest8(raw: &str) -> String {
    crate::hex(&Sha256::digest(raw.as_bytes())[..4])
}

/// `cap` of 0 means no cap. Lengths are in code points, as the reference
/// implementation measures them.
pub fn safe_text(raw: &str, cap: usize) -> String {
    let mut kept = String::with_capacity(raw.len());
    for ch in raw.chars() {
        if is_space(ch) {
            kept.push(' '); // newlines/tabs are control: normalise, don't drop
        } else if !(ch.is_control() || is_format(ch)) {
            kept.push(ch);
        }
    }
    let collapsed = kept.split(' ').filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ");
    let text = collapse_fences(&collapsed);
    let len = text.chars().count();
    if cap > 0 && len > cap {
        let head: String = text.chars().take(cap.saturating_sub(9)).collect();
        return format!("{head}…{}", digest8(raw));
    }
    text
}

/// One line of a record's text as `show` prints it for a person (v3.5):
/// control and format characters removed, other whitespace a space, and
/// nothing else touched — a body keeps its words, its spacing and its fences.
pub fn plain_line(line: &str) -> String {
    let mut kept = String::with_capacity(line.len());
    for ch in line.chars() {
        if is_space(ch) {
            kept.push(' ');
        } else if !(ch.is_control() || is_format(ch)) {
            kept.push(ch);
        }
    }
    kept
}

/// Runs of three or more backticks collapse to one, so an inline code span
/// survives and a fence opener does not.
fn collapse_fences(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut run = 0usize;
    for ch in s.chars() {
        if ch == '`' {
            run += 1;
            continue;
        }
        flush_ticks(&mut out, run);
        run = 0;
        out.push(ch);
    }
    flush_ticks(&mut out, run);
    out
}

fn flush_ticks(out: &mut String, run: usize) {
    match run {
        0 => {}
        1 | 2 => out.extend(std::iter::repeat_n('`', run)),
        _ => out.push('`'),
    }
}

/// An id, bounded but still distinguishing: whenever the transform is lossy
/// at all, the original's digest is appended.
pub fn safe_id(raw: &str) -> String {
    let text = safe_text(raw, 0);
    if text == raw && text.chars().count() <= ID_CAP {
        return text;
    }
    let head: String = text.chars().take(ID_CAP.saturating_sub(9)).collect();
    format!("{head}…{}", digest8(raw))
}

/// A value as a diagnostic shows it: its canonical JSON (v3.0). Shows the
/// value's type and boundaries — `"1"` and `1` are different malformations —
/// in any language.
pub fn render_value(v: &Value) -> String {
    canonical(v)
}

/// A string rendered as the JSON string it would be in a record.
pub fn render_str(s: &str) -> String {
    canonical(&Value::Str(s.to_string()))
}

pub fn capped_value(v: &Value, cap: usize) -> String {
    safe_text(&render_value(v), cap)
}

/// A participant list under a budget. Names as many elements as fit, then
/// says HOW MANY it did not name and out of how many, with a digest of the
/// whole — for E016 and E012 the list IS the remedy, and a reader who cannot
/// see how much is missing cannot tell a complete instruction from a partial
/// one. `rendered` holds each element's UNCAPPED rendering.
pub fn capped_seq(rendered: &[String], cap: usize, item_cap: usize, sep: &str) -> String {
    let capped: Vec<String> = rendered.iter().map(|r| safe_text(r, item_cap)).collect();
    let whole = capped.join(sep);
    if whole.chars().count() <= cap {
        return whole;
    }
    let total = capped.len();
    let digest = digest8(&rendered.join(sep));
    let reserve = format!("{sep}… and {total} more of {total} (…{digest})").chars().count();
    let budget = cap.saturating_sub(reserve);
    let mut kept: Vec<&str> = Vec::new();
    let mut used = 0usize;
    for r in &capped {
        let step = r.chars().count() + if kept.is_empty() { 0 } else { sep.chars().count() };
        if used + step > budget {
            break;
        }
        kept.push(r);
        used += step;
    }
    if kept.is_empty() {
        return format!("… {total} elided (…{digest})");
    }
    format!("{}{sep}… and {} more of {total} (…{digest})", kept.join(sep), total - kept.len())
}

/// `touched`-shaped: a bracketed array whose elements are joined with a bare
/// comma, so an array that fits reads as its canonical JSON. A non-array is
/// rendered as the value it is — the finding for a malformed envelope is ABOUT
/// the value not being an array.
pub fn capped_array(v: &Value, cap: usize) -> String {
    match v {
        Value::Array(items) => {
            let rendered: Vec<String> = items.iter().map(render_value).collect();
            format!("[{}]", capped_seq(&rendered, cap, MESSAGE_ITEM_CAP, ","))
        }
        other => capped_value(other, cap),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_breakers_are_removed_and_whitespace_collapses() {
        assert_eq!(safe_text("a\nb\t c", 0), "a b c");
        assert_eq!(safe_text("x\u{202e}y\u{200b}z", 0), "xyz");
        assert_eq!(safe_text("```fence```", 0), "`fence`");
        assert_eq!(safe_text("``two``", 0), "``two``");
        // The information separators are whitespace here, as in the reference.
        assert_eq!(safe_text("a\u{1c}b", 0), "a b");
        assert_eq!(safe_text("  lead and trail  ", 0), "lead and trail");
    }

    #[test]
    fn truncation_keeps_identity_and_lands_exactly_on_the_cap() {
        let long = "x".repeat(100);
        let out = safe_text(&long, 20);
        assert_eq!(out.chars().count(), 20);
        assert!(out.ends_with(&digest8(&long)));
        assert_ne!(safe_text(&format!("{long}a"), 20), safe_text(&format!("{long}b"), 20));
    }
}

fn in_ranges(table: &[(u32, u32)], c: char) -> bool {
    let cp = c as u32;
    table.binary_search_by(|&(lo, hi)| {
        if hi < cp { std::cmp::Ordering::Less } else if lo > cp { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Equal }
    }).is_ok()
}

/// Terminal columns one character occupies: 0, 1 or 2.
pub fn char_cols(c: char) -> usize {
    if in_ranges(crate::width_tables::COMBINING, c) {
        0
    } else if in_ranges(crate::width_tables::WIDE, c) {
        2
    } else {
        1
    }
}

/// Width as a terminal sees it — SGR and OSC-8 escape sequences occupy none.
pub fn visible_len(text: &str) -> usize {
    strip_ansi(text).chars().map(char_cols).sum()
}

/// `\x1b[...m` and `\x1b]8;;...\x1b\\` removed.
pub fn strip_ansi(text: &str) -> String {
    let b: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == '\x1b' && b.get(i + 1) == Some(&'[') {
            let mut j = i + 2;
            while j < b.len() && (b[j].is_ascii_digit() || b[j] == ';') {
                j += 1;
            }
            if b.get(j) == Some(&'m') {
                i = j + 1;
                continue;
            }
        }
        if b[i] == '\x1b' && b.get(i + 1) == Some(&']') && b.get(i + 2) == Some(&'8') && b.get(i + 3) == Some(&';') && b.get(i + 4) == Some(&';') {
            let mut j = i + 5;
            while j + 1 < b.len() && !(b[j] == '\x1b' && b[j + 1] == '\\') {
                j += 1;
            }
            if j + 1 < b.len() {
                i = j + 2;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    out
}

/// Full case folding, as the reference's `str.casefold` computes it: the
/// caseless form a "same title" comparison needs (`ß` and `ss` agree, so do
/// `ς` and `σ`), which lowercasing is not.
pub fn casefold(s: &str) -> String {
    use crate::casefold_table::CASEFOLD;
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if c.is_ascii() {
            // No ASCII character is in the table: it folds to its lowercase.
            out.push(c.to_ascii_lowercase());
            continue;
        }
        match CASEFOLD.binary_search_by(|(k, _)| k.cmp(&c)) {
            Ok(i) => out.push_str(CASEFOLD[i].1),
            Err(_) => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// A regex `\w` character in the reference's Unicode mode: alphanumeric or
/// the underscore.
pub fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Whitespace-separated words, as the reference's `str.split()` counts them.
pub fn word_count(s: &str) -> usize {
    s.split(is_space).filter(|w| !w.is_empty()).count()
}
