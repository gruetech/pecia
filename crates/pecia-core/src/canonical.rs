//! The canonical form: RFC 8785 over the v3.0 domain (`pc-ddd9`, `pc-71fb`).
//!
//! Two of RFC 8785's rules are the hard ones to implement byte-exactly, and
//! the domain removes both. Numbers are serialized by ECMAScript's IEEE-754
//! algorithm, which for integers within ±(2^53−1) is plain decimal digits.
//! Keys are sorted by UTF-16 code unit, which for ASCII keys is byte order.
//! What is left is escaping and the absence of whitespace.
//!
//! On this domain the bytes are also Matrix's canonical JSON, so two
//! independent specifications can serve as oracles for the same output.

use crate::value::Value;

/// The canonical serialization. Total: every `Value` is admissible by
/// construction of the parser, so this cannot fail.
pub fn canonical(value: &Value) -> String {
    let mut out = String::new();
    write_to(value, &mut out);
    out
}

/// The canonical form of an object given by its members — a record read in
/// place, without assembling a `Value` around a copy of it.
pub fn canonical_object(pairs: &[(String, Value)]) -> String {
    let mut out = String::new();
    write_object(pairs, &mut out);
    out
}

/// The canonical serialization, appended to `out` — for a caller that
/// serializes many values and can reuse one buffer.
pub fn write_to(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(n) => write_int(*n, out),
        Value::Str(s) => write_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_to(item, out);
            }
            out.push(']');
        }
        Value::Object(pairs) => write_object(pairs, out),
    }
}

/// Members sorted by key BYTES. The keys are ASCII (the parser refuses the
/// rest), so byte order, code-point order and RFC 8785's UTF-16 code-unit
/// order are the same order — which is why the domain carries that
/// restriction. An object read from a stored line is already in that order,
/// so it is written as it stands.
fn write_object(pairs: &[(String, Value)], out: &mut String) {
    out.push('{');
    if pairs.is_sorted_by(|a, b| a.0.as_bytes() < b.0.as_bytes()) {
        write_members(pairs.iter(), out);
    } else {
        let mut sorted: Vec<&(String, Value)> = pairs.iter().collect();
        sorted.sort_unstable_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
        write_members(sorted.into_iter(), out);
    }
    out.push('}');
}

fn write_members<'a>(members: impl Iterator<Item = &'a (String, Value)>, out: &mut String) {
    for (i, (key, item)) in members.enumerate() {
        if i > 0 {
            out.push(',');
        }
        write_string(key, out);
        out.push(':');
        write_to(item, out);
    }
}

/// Decimal digits with no allocation: the domain's integers fit in 17 bytes.
fn write_int(n: i64, out: &mut String) {
    let mut buf = [0u8; 20];
    let mut i = buf.len();
    let mut m = n.unsigned_abs();
    loop {
        i -= 1;
        buf[i] = b'0' + (m % 10) as u8;
        m /= 10;
        if m == 0 {
            break;
        }
    }
    if n < 0 {
        i -= 1;
        buf[i] = b'-';
    }
    out.push_str(std::str::from_utf8(&buf[i..]).expect("ascii digits"));
}

/// RFC 8785's escaping: `"` and `\` escaped; U+0000–U+001F as the five
/// shortcuts or lowercase `\u00hh`; everything else raw UTF-8 — U+2028 and
/// U+2029 INCLUDED, which is why every reader of a stored line must split on
/// LF alone (v2.12, `pc-d96d`). Runs that need no escape are copied whole;
/// every byte that does is ASCII, so each run ends on a character boundary.
fn write_string(s: &str, out: &mut String) {
    /// The escape each byte needs: 0 for none, 1 for `\u00hh`, else the
    /// letter of its shortcut (`"` and `\` stand for themselves).
    const ESCAPE: [u8; 256] = {
        let mut t = [0u8; 256];
        let mut b = 0;
        while b < 0x20 {
            t[b] = 1;
            b += 1;
        }
        t[0x08] = b'b';
        t[0x09] = b't';
        t[0x0a] = b'n';
        t[0x0c] = b'f';
        t[0x0d] = b'r';
        t[b'"' as usize] = b'"';
        t[b'\\' as usize] = b'\\';
        t
    };
    const HEX: &[u8; 16] = b"0123456789abcdef";
    out.reserve(s.len() + 2);
    out.push('"');
    let bytes = s.as_bytes();
    let mut start = 0;
    for (i, &b) in bytes.iter().enumerate() {
        let esc = ESCAPE[usize::from(b)];
        if esc == 0 {
            continue;
        }
        out.push_str(&s[start..i]);
        out.push('\\');
        if esc == 1 {
            out.push_str("u00");
            out.push(char::from(HEX[usize::from(b >> 4)]));
            out.push(char::from(HEX[usize::from(b & 0xf)]));
        } else {
            out.push(char::from(esc));
        }
        start = i + 1;
    }
    out.push_str(&s[start..]);
    out.push('"');
}
