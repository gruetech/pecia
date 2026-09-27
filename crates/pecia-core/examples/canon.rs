//! Canonicalize stdin, one value per LF-delimited line, for the
//! cross-implementation differential (`dev/rust-differential.py`).
//!
//! Output is one line per input line: `<sha256>\t<canonical>` for an
//! admissible value, or `!<message>` for a refusal. Line correspondence is the
//! contract, so a refusal still occupies its line.

use pecia_core::{canonical, entry_hash, parse};
use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("stdin");
    let out = io::stdout();
    let mut out = io::BufWriter::new(out.lock());
    for line in input.split('\n') {
        if line.is_empty() {
            continue;
        }
        match parse(line) {
            Ok(value) => {
                writeln!(out, "{}\t{}", entry_hash(&value), canonical(&value)).expect("write");
            }
            Err(e) => writeln!(out, "!{}", e.message).expect("write"),
        }
    }
}
