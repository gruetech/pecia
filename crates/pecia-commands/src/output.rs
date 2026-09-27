//! The CLI's output encoding: sorted keys, `", "` and `": "` separators, and
//! everything outside printable ASCII escaped — `\uXXXX` in lowercase hex, a
//! non-BMP character as a surrogate pair, DEL included.
//!
//! This is NOT the canonical form, and it is deliberately not: stored lines are
//! canonical (RFC 8785, v3.0) because they are hashed; command output is read
//! by people, terminals and log scrapers, so it stays pure ASCII whatever a
//! record carries. It is the reference implementation's output byte for byte,
//! which the suite relies on in places (`"unreadable_lines": 1`).

use pecia_core::Value;

pub fn dumps(v: &Value) -> String {
    let mut out = String::new();
    write(v, &mut out);
    out
}

fn write(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Int(n) => out.push_str(&n.to_string()),
        Value::Str(s) => string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, x) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write(x, out);
            }
            out.push(']');
        }
        Value::Object(pairs) => {
            let mut sorted: Vec<&(String, Value)> = pairs.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(&b.0));
            out.push('{');
            for (i, (k, x)) in sorted.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                string(k, out);
                out.push_str(": ");
                write(x, out);
            }
            out.push('}');
        }
    }
}

fn string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(ch),
            c => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

// The ceiling is a stated bound the tests hold real output to; nothing in
// the binary computes with it.

/// Output bytes per capped code point in the worst case: a non-BMP character
/// is written as a surrogate pair, `\udXXX\udXXX`. The caps count CODE
/// POINTS and the output is bytes (v2.16, pc-7b6c).
#[cfg_attr(not(test), allow(dead_code))]
pub const JSON_WORST_BYTES_PER_CODE_POINT: usize = 12;

/// The key names, braces, quotes, commas and integers around one projected
/// record — generous, so the ceiling is an upper bound rather than tight.
#[cfg_attr(not(test), allow(dead_code))]
pub const PROJECTION_FRAME_BYTES: usize = 512;

/// The most bytes one projected record can occupy on stdout, DERIVED from the
/// caps and this encoder rather than restated beside them: `edge_targets` is
/// the part a caller bounds.
#[cfg_attr(not(test), allow(dead_code))]
pub fn projection_ceiling_bytes(edge_targets: usize) -> usize {
    use pecia_core::text::ID_CAP;
    use pecia_core::write::{DATE_CAP, OWNER_CAP, TITLE_CAP, VOCAB_CAP};
    let scalars = ID_CAP + 2 * VOCAB_CAP + TITLE_CAP + OWNER_CAP + 3 * DATE_CAP;
    let per_target = ID_CAP * JSON_WORST_BYTES_PER_CODE_POINT + 4; // quotes, comma
    scalars * JSON_WORST_BYTES_PER_CODE_POINT + PROJECTION_FRAME_BYTES + edge_targets * per_target
}

#[cfg(test)]
mod tests {
    //! ThePerRecordCeilingIsDerivedAndTrue and OutputCaps' volume arm,
    //! restated: the derived ceiling holds against real output — the bytes
    //! `add` and `edit` print — at the caps, in astral characters.
    use super::*;
    use pecia_core::config::Config;
    use pecia_core::write::{project, OWNER_CAP, TITLE_CAP};

    fn written_bytes(title: &str, owner: &str, blocks: usize) -> usize {
        let edges = Value::Object(vec![
            ("blocks".into(), Value::Array((0..blocks).map(|i| Value::Str(format!("pc-t{i:04}"))).collect())),
            ("retires".into(), Value::Array(vec![])),
        ]);
        let rec = vec![
            ("id".to_string(), Value::Str("pc-aaaa".into())),
            ("rev".to_string(), Value::Int(1)),
            ("type".to_string(), Value::Str("task".into())),
            ("title".to_string(), Value::Str(title.into())),
            ("status".to_string(), Value::Str("open".into())),
            ("priority".to_string(), Value::Int(2)),
            ("created".to_string(), Value::Str("2026-07-20".into())),
            ("updated".to_string(), Value::Str("2026-07-20".into())),
            ("owner".to_string(), Value::Str(owner.into())),
            ("edges".to_string(), edges),
        ];
        dumps(&project("written", &rec, &Config::default())).len() + 1
    }

    #[test]
    fn kill_a_title_at_the_cap_in_astral_characters_is_under() {
        let size = written_bytes(&"\u{1F600}".repeat(TITLE_CAP), "t", 0);
        assert!(size > 16_256, "the premise: this beats the figure the register used to state");
        assert!(size <= projection_ceiling_bytes(0));
    }

    #[test]
    fn kill_every_capped_scalar_at_once_is_under() {
        let e = "\u{1F600}";
        assert!(written_bytes(&e.repeat(TITLE_CAP), &e.repeat(OWNER_CAP), 0) <= projection_ceiling_bytes(0));
    }

    #[test]
    fn kill_a_two_thousand_element_edge_is_under_the_stated_term() {
        let size = written_bytes("many edges", "t", 2000);
        assert!(size > 16_256, "the premise, on the second route");
        assert!(size <= projection_ceiling_bytes(2000));
    }

    #[test]
    fn control_an_ordinary_record_is_far_under() {
        assert!(written_bytes("ordinary", "t", 0) * 50 < projection_ceiling_bytes(0));
    }

    #[test]
    fn caps_still_bound_worst_case_volume() {
        assert!(projection_ceiling_bytes(0) < 131_072, "drifted past 'unrealistic but cheap'");
        assert!(projection_ceiling_bytes(1) > projection_ceiling_bytes(0), "the edge term is what the caller bounds");
    }
}
