//! Kills and controls for the v3.0 domain and the canonical form (`pc-ddd9`).
//!
//! Every rule gets a kill AND a control at the boundary it draws, because a
//! refusal that fires on everything measures nothing: `2^53-1` is admitted
//! beside `2^53`, an escaped surrogate PAIR beside a lone one, `0` beside
//! `-0`. That discipline came from the Python's own adversarial rounds, and
//! the port inherits the discipline, not only the rules.

use pecia_core::{canonical, entry_hash, parse, Value};

fn refused(text: &str) -> String {
    match parse(text) {
        Ok(v) => panic!("admitted {text} as {v:?} - it is outside the domain"),
        Err(e) => e.message,
    }
}

fn admitted(text: &str) -> Value {
    parse(text).unwrap_or_else(|e| panic!("refused {text}: {e}"))
}

// ---------------------------------------------------------------- numbers
#[test]
fn kill_a_float_is_refused_and_named_as_one() {
    for text in [r#"{"a":1.5}"#, r#"{"a":1e2}"#, r#"{"a":1E2}"#, r#"{"a":1.0}"#] {
        assert!(refused(text).contains("not an integer"), "{text}");
    }
}

#[test]
fn kill_an_overflowing_exponent_is_refused_as_a_non_integer() {
    // The Python's pc-d502: valid JSON numeric syntax that decodes to
    // infinity. Here it never becomes a number at all.
    assert!(refused(r#"{"a":1e1000000}"#).contains("not an integer"));
}

#[test]
fn kill_the_integer_range_is_enforced_at_both_signs() {
    assert!(refused(r#"{"a":9007199254740992}"#).contains("outside"));
    assert!(refused(r#"{"a":-9007199254740992}"#).contains("outside"));
    assert!(refused(&format!(r#"{{"a":{}}}"#, "9".repeat(5000))).contains("outside"));
}

#[test]
fn control_the_range_edge_is_admitted() {
    assert_eq!(
        admitted(r#"{"a":9007199254740991}"#).get("a"),
        Some(&Value::Int(9007199254740991))
    );
    assert_eq!(
        admitted(r#"{"a":-9007199254740991}"#).get("a"),
        Some(&Value::Int(-9007199254740991))
    );
}

#[test]
fn kill_negative_zero_is_not_canonical() {
    assert!(refused(r#"{"a":-0}"#).contains("-0"));
    assert_eq!(admitted(r#"{"a":0}"#).get("a"), Some(&Value::Int(0)));
    assert_eq!(admitted(r#"{"a":-1}"#).get("a"), Some(&Value::Int(-1)));
}

#[test]
fn kill_json_syntax_this_narrowing_must_not_have_loosened() {
    for text in [
        r#"{"a":01}"#,
        r#"{"a":+1}"#,
        r#"{"a":.5}"#,
        r#"{"a":NaN}"#,
        r#"{"a":Infinity}"#,
        r#"{"a":1}x"#,
        r#"{"a":}"#,
    ] {
        parse(text).expect_err(&format!("admitted {text}"));
    }
}

// ------------------------------------------------------------------- keys
#[test]
fn kill_a_duplicate_key_is_refused_rather_than_silently_last_wins() {
    // serde_json keeps the last of these without a word; RFC 8785's input rule
    // forbids it, and a silent collapse makes the stored line and the hashed
    // value disagree.
    assert!(refused(r#"{"a":1,"a":2}"#).contains("twice"));
}

#[test]
fn kill_a_non_ascii_key_is_refused() {
    assert!(refused("{\"caf\u{e9}\":1}").contains("ASCII"));
}

#[test]
fn control_an_ascii_key_and_a_non_ascii_value_are_admitted() {
    // The restriction is on keys alone: a title is prose and must carry any
    // language. This is the line the domain actually draws.
    let v = admitted("{\"title\":\"caf\u{e9} \u{2014} \u{1f600}\"}");
    assert_eq!(
        v.get("title").and_then(Value::as_str),
        Some("caf\u{e9} \u{2014} \u{1f600}")
    );
}

// ---------------------------------------------------------------- strings
#[test]
fn kill_a_lone_surrogate_has_no_canonical_bytes() {
    for text in [r#"{"a":"\ud800"}"#, r#"{"a":"\udc00"}"#, r#"{"a":"\ud800x"}"#] {
        assert!(refused(text).contains("lone surrogate"), "{text}");
    }
}

#[test]
fn control_a_surrogate_pair_is_one_scalar() {
    // The input must carry the ESCAPES, not the character: a literal emoji
    // here would exercise the raw-UTF-8 path and leave the pair-joining code
    // untested while still passing. Asserted, not assumed.
    let input = "{\"a\":\"\\ud83d\\ude00\"}";
    assert!(input.is_ascii(), "the input must be escapes, not a raw scalar");
    assert_eq!(admitted(input).get("a").and_then(Value::as_str), Some("\u{1f600}"));
}

#[test]
fn kill_a_raw_control_character_must_be_escaped() {
    assert!(refused("{\"a\":\"x\ny\"}").contains("control"));
}

// --------------------------------------------------------------- nesting
#[test]
fn kill_the_nesting_bound_is_a_guard_not_a_report() {
    // Deeper than the bound is refused BEFORE recursing there, which is the
    // whole point of a structural guard (v2.12, pc-2e2f).
    let deep = format!("{}{}", "[".repeat(200), "]".repeat(200));
    let why = refused(&deep);
    assert!(why.contains("200 levels deep") && why.contains("nesting bound"), "{why}");
}

#[test]
fn control_the_bound_itself_is_admitted() {
    let at_bound = format!("{}{}", "[".repeat(100), "]".repeat(100));
    parse(&at_bound).expect("the declared bound is admissible");
    // A SCALAR inside the innermost container is still at the bound: containers
    // count, scalars do not. The first draft refused this, and an empty
    // innermost array — the only shape the control above tests — hid it.
    let with_scalar = format!("{}1{}", "[".repeat(100), "]".repeat(100));
    parse(&with_scalar).expect("a scalar at the bound is inside the bound");
    let string_brackets = format!("[\"{}\"]", "[".repeat(300));
    parse(&string_brackets).expect("brackets inside a string are not nesting");
}

// ------------------------------------------------------- the canonical form
#[test]
fn keys_are_sorted_and_nothing_is_padded() {
    let v = admitted(r#"{ "b" : 1 , "a" : [ 1 , 2 ] , "c" : { "z" : null , "y" : true } }"#);
    assert_eq!(canonical(&v), r#"{"a":[1,2],"b":1,"c":{"y":true,"z":null}}"#);
}

#[test]
fn escaping_is_the_rfc_set_and_no_more() {
    // Built from pieces so the SOURCE stays ASCII: the input carries the JSON
    // escapes \\u0001 and \\u2028, not the characters themselves.
    let input = "{\"a\":\"q\\\"b\\\\c\\nd\\u0001e\\u2028f\"}";
    let v = admitted(input);
    // The control becomes lowercase-hex; U+2028 comes back RAW, which is why
    // every reader of a stored line must split on LF alone (v2.12, pc-d96d).
    let want = format!("{}{}{}{}", "{\"a\":\"q\\\"b\\\\c\\nd",
                       "\\u0001e", '\u{2028}', "f\"}");
    assert_eq!(canonical(&v), want);
    assert_eq!(canonical(&admitted(r#"{"a":"/"}"#)), r#"{"a":"/"}"#);
}

#[test]
fn canonicalizing_is_idempotent_and_reparses() {
    let once = canonical(&admitted("{\"b\":1,\"a\":\"\u{e9}\\u2028\"}"));
    let twice = canonical(&parse(&once).expect("canonical output must reparse"));
    assert_eq!(once, twice);
}

#[test]
fn the_hash_is_lowercase_hex_sha256_of_those_bytes() {
    let v = admitted("{}");
    assert_eq!(canonical(&v), "{}");
    // sha256("{}") - an external constant, not one this code produced.
    assert_eq!(
        entry_hash(&v),
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    );
}

#[test]
fn a_unicode_escape_is_exactly_four_hex_digits() {
    // `from_str_radix` accepts a leading `+`; JSON does not, and neither does
    // the reference. The escapes are built from ASCII so no tool can decode them.
    for bad in ["{\"a\":\"\\u+041\"}", "{\"a\":\"\\u 041\"}", "{\"a\":\"\\u004\"}", "{\"a\":\"\\u00g1\"}"] {
        assert!(bad.is_ascii());
        assert!(pecia_core::parse(bad).is_err(), "{bad} was admitted");
    }
    assert_eq!(pecia_core::parse("{\"a\":\"\\u0041\\u00e9\"}").map(|v| pecia_core::canonical(&v)).ok().as_deref(), Some("{\"a\":\"Aé\"}"));
}
