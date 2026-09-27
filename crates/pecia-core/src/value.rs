//! The value domain the format admits (spec/format-v2.md, v3.0 — `pc-ddd9`).
//!
//! Not a general JSON tree. The canonical form is RFC 8785 over a NARROWED
//! domain, and the narrowing is what makes the RFC cheap to implement exactly:
//! integers only, so ECMAScript number formatting reduces to decimal digits;
//! ASCII keys, so UTF-16 code-unit order equals code-point order. Representing
//! only what the format admits means a `Value` that exists cannot be
//! unserializable — the domain is enforced once, at the parse boundary, rather
//! than re-checked at every use.

use std::fmt;

/// The largest integer the canonical form admits (v3.0). Beyond this a
/// JavaScript client silently loses precision, and the MCP server's clients
/// are often TypeScript.
pub const SAFE_INTEGER: i64 = (1 << 53) - 1;

/// The format's declared nesting bound (v2.12, `pc-2e2f`). A structural guard:
/// it exists so no conforming line can drive a parser — or any downstream
/// consumer — to a stack overflow, which is why it is checked while parsing
/// rather than after.
pub const NESTING_BOUND: usize = 100;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Value {
    Null,
    Bool(bool),
    /// Always within ±`SAFE_INTEGER`, never `-0` — the parser refuses the rest.
    Int(i64),
    Str(String),
    Array(Vec<Value>),
    /// Insertion order, not sorted: the canonical form sorts on the way out,
    /// and keeping the source order lets a reader see the object as written.
    /// Keys are unique and ASCII by construction of the parser.
    Object(Vec<(String, Value)>),
}

impl Value {
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Object(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(n) => Some(*n),
            _ => None,
        }
    }
}

/// Why a line is not an admissible value. Carries the byte offset so a caller
/// can name the position, and a message the caller reports as its own E001 —
/// this crate does no IO and emits no findings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DomainError {
    pub offset: usize,
    pub message: String,
}

impl DomainError {
    pub(crate) fn at(offset: usize, message: impl Into<String>) -> Self {
        Self { offset, message: message.into() }
    }
}

impl fmt::Display for DomainError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} (at byte {})", self.message, self.offset)
    }
}

impl std::error::Error for DomainError {}
