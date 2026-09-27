//! pecia's format core: the value domain, the canonical form, the hash chain.
//!
//! NO IO, deliberately. The checker's purity is what makes it testable and
//! what gives the formal model's correspondence claim a referent (v2.13,
//! `pc-d0dd`, `pc-8a93`): if this layer could read a file, the model would be
//! modelling something the code does not do. Storage, git and the CLI surface
//! live in crates that depend on this one, never the other way.
//!
//! RULE 1 applies here as everywhere: a value parsing clean means well-formed,
//! never true.

pub mod audit;
pub mod board;
pub mod canonical;
pub mod casefold_table;
pub mod check;
pub mod config;
pub mod dates;
pub mod finding;
pub mod index;
pub mod par;
pub mod parse;
pub mod query;
pub mod record;
pub mod sync;
pub mod text;
pub mod value;
pub mod width_tables;
pub mod write;

pub use canonical::canonical;
pub use parse::parse;
pub use value::{DomainError, Value, NESTING_BOUND, SAFE_INTEGER};

use sha2::{Digest, Sha256};

/// SHA-256 over an entry's canonical form, lowercase hex — the chain link.
///
/// The chain is self-verifying WITHOUT git: git supplies transport,
/// serialization and compare-and-swap, never immutability
/// (spec/format-v2.md §3.2).
pub fn entry_hash(entry: &Value) -> String {
    sha256_hex(canonical(entry).as_bytes())
}

/// The SHA-256 of an entry's canonical form, as bytes.
pub fn entry_digest(entry: &Value) -> [u8; 32] {
    Sha256::digest(canonical(entry).as_bytes()).into()
}

/// Lowercase-hex SHA-256 of `bytes`.
pub fn sha256_hex(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes)[..])
}

/// Lowercase hex, two digits a byte.
pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(DIGITS[usize::from(b >> 4)] as char);
        out.push(DIGITS[usize::from(b & 0xf)] as char);
    }
    out
}
