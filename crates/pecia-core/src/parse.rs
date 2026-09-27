//! Strict parse of one stored line into the v3.0 domain (`pc-ddd9`).
//!
//! WHY THIS IS NOT `serde_json`. The port plan said serde_json would parse and
//! only the writer would be ours. Measured against the domain, it cannot carry
//! three of the rules without wrapping it in as much code as this file:
//! `serde_json::Map` keeps the LAST of a duplicate key silently, where RFC 8785
//! requires unique names; `-0` and `0` are indistinguishable once decoded, so
//! the token is gone before a check could see it; and its recursion limit is
//! 128 against this format's declared bound of 100, so leaning on it would
//! admit depths the spec refuses. Owning the parse also means the refusal
//! messages are the format's own, which is what E001 reports.
//!
//! The grammar is RFC 8259 narrowed: integers only, ASCII unique keys, no lone
//! surrogate. Anything RFC 8259 refuses this refuses too — leading zeros, a
//! bare `+`, a raw control character in a string, trailing content.

use crate::value::{DomainError, Value, NESTING_BOUND, SAFE_INTEGER};

pub fn parse(text: &str) -> Result<Value, DomainError> {
    parse_noting_canonical(text).map(|(value, _)| value)
}

/// Parse, and say whether `text` was ALREADY the value's canonical form —
/// `canonical(&value) == text` — decided in the same pass: no whitespace
/// outside strings, object keys strictly ascending, and every escape the one
/// the canonical writer would choose. A reader that hashes the canonical form
/// can then hash the stored bytes instead of re-serializing them, which a
/// conforming log makes the ordinary case (v3.0: writers store canonical
/// lines).
pub fn parse_noting_canonical(text: &str) -> Result<(Value, bool), DomainError> {
    // A cheap count gates the string-aware scan, so the ordinary line pays
    // nothing; a line that could exceed the bound is measured BEFORE parsing,
    // and the refusal names the depth it found.
    if text.bytes().filter(|b| *b == b'[' || *b == b'{').count() > NESTING_BOUND {
        let depth = json_nesting_depth(text);
        if depth > NESTING_BOUND {
            return Err(DomainError::at(0, format!(
                "value nests {depth} levels deep — the record-line format's declared nesting bound is {NESTING_BOUND} (v2.12, pc-2e2f), a structural guard that keeps every parse and serialization away from the interpreter's recursion boundary"
            )));
        }
    }
    let mut p = Parser { s: text, b: text.as_bytes(), pos: 0, canonical: true };
    p.ws();
    let value = p.value(1)?;
    p.ws();
    if p.pos != p.b.len() {
        return Err(DomainError::at(p.pos, "trailing content after the value — one JSON value per line"));
    }
    Ok((value, p.canonical))
}

/// Maximum bracket depth of a candidate line, skipping brackets inside
/// strings. CONTAINERS count, scalars do not — a scalar inside a container at
/// the bound is at the bound, not past it.
pub fn json_nesting_depth(text: &str) -> usize {
    let (mut depth, mut max, mut in_string, mut escaped) = (0usize, 0usize, false, false);
    for ch in text.chars() {
        if in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
        } else if ch == '"' {
            in_string = true;
        } else if ch == '[' || ch == '{' {
            depth += 1;
            max = max.max(depth);
        } else if ch == ']' || ch == '}' {
            depth = depth.saturating_sub(1);
        }
    }
    max
}

struct Parser<'a> {
    /// The input, as text and as bytes: scanning is by byte, and a run is
    /// taken as `&str` — already valid UTF-8, so never validated twice.
    s: &'a str,
    b: &'a [u8],
    pos: usize,
    /// Every byte so far is what the canonical writer would have written.
    canonical: bool,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        let start = self.pos;
        while matches!(self.b.get(self.pos), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.pos += 1;
        }
        if self.pos != start {
            self.canonical = false;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.b.get(self.pos).copied()
    }

    fn eat(&mut self, c: u8) -> Result<(), DomainError> {
        if self.peek() == Some(c) {
            self.pos += 1;
            Ok(())
        } else {
            Err(DomainError::at(self.pos, format!("expected {:?}", c as char)))
        }
    }

    fn literal(&mut self, word: &str, value: Value) -> Result<Value, DomainError> {
        if self.b[self.pos..].starts_with(word.as_bytes()) {
            self.pos += word.len();
            Ok(value)
        } else {
            Err(DomainError::at(self.pos, "not a JSON value"))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Value, DomainError> {
        // Checked on the way INTO a container, so the bound guards the
        // recursion rather than reporting it. Scalars do not count: the
        // first draft checked every value and refused a scalar sitting inside
        // a container AT the bound, which the reference certifies. The
        // prescan in `parse` normally refuses first; this is the backstop.
        if depth > NESTING_BOUND && matches!(self.peek(), Some(b'{' | b'[')) {
            return Err(DomainError::at(
                self.pos,
                format!("value nests more than {NESTING_BOUND} levels deep — the record-line format's declared nesting bound is {NESTING_BOUND} (v2.12, pc-2e2f)"),
            ));
        }
        match self.peek() {
            Some(b'{') => self.object(depth),
            Some(b'[') => self.array(depth),
            Some(b'"') => Ok(Value::Str(self.string()?)),
            Some(b't') => self.literal("true", Value::Bool(true)),
            Some(b'f') => self.literal("false", Value::Bool(false)),
            Some(b'n') => self.literal("null", Value::Null),
            _ if self.non_json_token().is_some() => {
                let tok = self.non_json_token().expect("checked");
                Err(DomainError::at(self.pos, format!(
                    "{tok} is not a JSON token — the one-JSON-object-per-line contract admits strict JSON only (pc-6af9)"
                )))
            }
            Some(c) if c == b'-' || c.is_ascii_digit() => self.integer(),
            Some(_) => Err(DomainError::at(self.pos, "not a JSON value")),
            None => Err(DomainError::at(self.pos, "unexpected end of input")),
        }
    }

    fn object(&mut self, depth: usize) -> Result<Value, DomainError> {
        self.eat(b'{')?;
        let mut pairs: Vec<(String, Value)> = Vec::new();
        self.ws();
        if self.peek() == Some(b'}') {
            self.pos += 1;
            return Ok(Value::Object(pairs));
        }
        loop {
            self.ws();
            let at = self.pos;
            let key = self.string()?;
            if !key.is_ascii() {
                return Err(DomainError::at(
                    at,
                    "object key is not ASCII — the canonical form admits ASCII keys only (v3.0, pc-ddd9)",
                ));
            }
            // Keys in ascending order cannot repeat, so only an input that
            // has broken the canonical order pays the duplicate scan.
            match pairs.last() {
                Some((last, _)) if key.as_bytes() <= last.as_bytes() => self.canonical = false,
                _ => {}
            }
            if !self.canonical && pairs.iter().any(|(k, _)| *k == key) {
                return Err(DomainError::at(
                    at,
                    format!("object key {key:?} appears twice — the canonical form admits unique keys only (v3.0, pc-ddd9)"),
                ));
            }
            self.ws();
            self.eat(b':')?;
            self.ws();
            let v = self.value(depth + 1)?;
            pairs.push((key, v));
            self.ws();
            match self.peek() {
                Some(b',') => self.pos += 1,
                Some(b'}') => {
                    self.pos += 1;
                    return Ok(Value::Object(pairs));
                }
                _ => return Err(DomainError::at(self.pos, "expected ',' or '}'")),
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<Value, DomainError> {
        self.eat(b'[')?;
        let mut items = Vec::new();
        self.ws();
        if self.peek() == Some(b']') {
            self.pos += 1;
            return Ok(Value::Array(items));
        }
        loop {
            self.ws();
            items.push(self.value(depth + 1)?);
            self.ws();
            match self.peek() {
                Some(b',') => self.pos += 1,
                Some(b']') => {
                    self.pos += 1;
                    return Ok(Value::Array(items));
                }
                _ => return Err(DomainError::at(self.pos, "expected ',' or ']'")),
            }
        }
    }

    /// The tokens some JSON parsers accept and JSON does not (RFC 8259 §6),
    /// named as such rather than reported as a bare syntax error.
    fn non_json_token(&self) -> Option<&'static str> {
        let rest = &self.b[self.pos..];
        ["-Infinity", "Infinity", "NaN"].into_iter().find(|t| rest.starts_with(t.as_bytes()))
    }

    fn integer(&mut self) -> Result<Value, DomainError> {
        let start = self.pos;
        if self.peek() == Some(b'-') {
            self.pos += 1;
        }
        let digits_start = self.pos;
        match self.peek() {
            // JSON forbids leading zeros, so a `0` stands alone.
            Some(b'0') => self.pos += 1,
            Some(c) if c.is_ascii_digit() => {
                while matches!(self.peek(), Some(d) if d.is_ascii_digit()) {
                    self.pos += 1;
                }
            }
            _ => return Err(DomainError::at(self.pos, "not a number")),
        }
        if matches!(self.peek(), Some(d) if d.is_ascii_digit()) {
            return Err(DomainError::at(start, "leading zero"));
        }
        // A fraction or exponent is where a float would be, and the domain has
        // none: named as what it is rather than as a syntax error.
        if matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            return Err(DomainError::at(
                start,
                "number is not an integer — the canonical form admits integers only (v3.0, pc-ddd9); store a non-integer as a string",
            ));
        }
        let text = &self.s[start..self.pos];
        let negative = self.b[start] == b'-';
        if negative && &self.b[digits_start..self.pos] == b"0" {
            return Err(DomainError::at(start, "-0 is not canonical (v3.0, pc-ddd9)"));
        }
        // Length first: a token with thousands of digits must be refused
        // without being turned into a number at all.
        if self.pos - digits_start > 16 {
            return Err(DomainError::at(start, out_of_range()));
        }
        let n: i64 = text.parse().map_err(|_| DomainError::at(start, out_of_range()))?;
        if n.abs() > SAFE_INTEGER {
            return Err(DomainError::at(start, out_of_range()));
        }
        Ok(Value::Int(n))
    }

    fn string(&mut self) -> Result<String, DomainError> {
        self.eat(b'"')?;
        let mut out = String::new();
        loop {
            let at = self.pos;
            match self.peek() {
                None => return Err(DomainError::at(at, "unterminated string")),
                Some(b'"') => {
                    self.pos += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.pos += 1;
                    let esc = self.peek().ok_or_else(|| DomainError::at(at, "unterminated escape"))?;
                    self.pos += 1;
                    match esc {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => {
                            // Legal JSON, but the canonical writer leaves `/` bare.
                            self.canonical = false;
                            out.push('/');
                        }
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => out.push(self.unicode_escape(at)?),
                        _ => return Err(DomainError::at(at, "unknown string escape")),
                    }
                }
                Some(c) if c < 0x20 => {
                    return Err(DomainError::at(at, "raw control character in a string — it must be escaped"))
                }
                Some(_) => {
                    // A run of plain characters, taken whole. It ends at a
                    // quote, a backslash or a control byte — all ASCII, so all
                    // character boundaries of the &str this was built from.
                    // (The first draft decoded one character at a time by
                    // validating the REST of the line each time: quadratic,
                    // and every command read the log slower than the
                    // reference did.)
                    let len = self.b[self.pos..].iter().take_while(|&&c| c != b'"' && c != b'\\' && c >= 0x20).count();
                    out.push_str(&self.s[self.pos..self.pos + len]);
                    self.pos += len;
                }
            }
        }
    }

    /// A `\uXXXX` escape, joining a surrogate PAIR and refusing a lone one:
    /// UTF-8 cannot carry a lone surrogate, so such a value has no canonical
    /// bytes and no hash (v3.0, pc-ddd9).
    fn unicode_escape(&mut self, at: usize) -> Result<char, DomainError> {
        let first = self.hex4(at)?;
        if (0xD800..0xDC00).contains(&first) {
            if !self.b[self.pos..].starts_with(b"\\u") {
                return Err(DomainError::at(at, lone_surrogate()));
            }
            self.pos += 2;
            let second = self.hex4(at)?;
            if !(0xDC00..0xE000).contains(&second) {
                return Err(DomainError::at(at, lone_surrogate()));
            }
            let combined = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00);
            // The canonical writer carries a non-BMP character raw.
            self.canonical = false;
            return char::from_u32(combined).ok_or_else(|| DomainError::at(at, lone_surrogate()));
        }
        if (0xDC00..0xE000).contains(&first) {
            return Err(DomainError::at(at, lone_surrogate()));
        }
        // It writes `\u00hh` only for a control character with no shortcut.
        if first >= 0x20 || matches!(first, 0x08 | 0x09 | 0x0a | 0x0c | 0x0d) {
            self.canonical = false;
        }
        char::from_u32(first).ok_or_else(|| DomainError::at(at, lone_surrogate()))
    }

    /// Exactly four hex digits. Checked byte by byte: `from_str_radix` alone
    /// admits a leading `+`, so `\u+041` read as U+0041 where JSON (and the
    /// reference) refuse it.
    fn hex4(&mut self, at: usize) -> Result<u32, DomainError> {
        let end = self.pos + 4;
        let digits = self.b.get(self.pos..end).ok_or_else(|| DomainError::at(at, "truncated \\u escape"))?;
        let mut n = 0u32;
        for &d in digits {
            let v = match d {
                b'0'..=b'9' => d - b'0',
                b'a'..=b'f' => d - b'a' + 10,
                b'A'..=b'F' => {
                    self.canonical = false; // the canonical writer's hex is lowercase
                    d - b'A' + 10
                }
                _ => return Err(DomainError::at(at, "invalid \\u escape")),
            };
            n = n * 16 + u32::from(v);
        }
        self.pos = end;
        Ok(n)
    }
}

fn out_of_range() -> String {
    format!("integer is outside ±(2^53−1) — the canonical form's range (v3.0, pc-ddd9)")
}

fn lone_surrogate() -> &'static str {
    "a string carries a lone surrogate — not valid Unicode, so UTF-8 cannot carry it and the canonical form has no bytes for it (v3.0, pc-ddd9)"
}
