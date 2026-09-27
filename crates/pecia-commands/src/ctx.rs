//! What every command shares: the resolved store, and the reply it builds.
//!
//! Commands never print (design/rust-port.md §5). Each one ADDS to a reply —
//! JSON values and text lines for stdout, fatal findings for stderr — and
//! returns its exit code. The CLI renders the reply to its two streams; the
//! MCP server renders the same reply as a tool result. One core, two
//! renderers, and neither renderer re-parses the other's output.

use crate::output;
use pecia_core::finding::Finding;
use pecia_core::text::{safe_text, MESSAGE_CAP};
use pecia_core::Value;
use pecia_store::Store;
use std::cell::RefCell;

pub const RULE1: &str = "Exit 0 means \"well-formed,\" never \"true.\"";

/// One item a command writes to stdout.
#[derive(Debug, Clone, PartialEq)]
pub enum Out {
    /// A JSON value, one line in the CLI's output encoding.
    Json(Value),
    /// Human text — a board, a chart, Mermaid source — as its lines.
    Text(String),
}

/// A finished command: what it wrote, and its exit code (0 clean, 1
/// findings, 2 cannot-run).
#[derive(Debug, Clone, PartialEq)]
pub struct Reply {
    pub stdout: Vec<Out>,
    pub stderr: Vec<Value>,
    pub exit: u8,
}

impl Reply {
    /// A cannot-run before any command ran — no store to run it against.
    pub fn fatal(message: &str) -> Reply {
        Reply { stdout: Vec::new(), stderr: vec![fatal(message)], exit: 2 }
    }

    /// stdout as the CLI prints it.
    pub fn stdout_text(&self) -> String {
        self.stdout.iter().map(|o| match o {
            Out::Json(v) => output::dumps(v) + "\n",
            Out::Text(t) => format!("{t}\n"),
        }).collect()
    }

    /// stderr as the CLI prints it.
    pub fn stderr_text(&self) -> String {
        self.stderr.iter().map(|v| output::dumps(v) + "\n").collect()
    }
}

pub struct Ctx {
    pub store: Store,
    out: RefCell<Vec<Out>>,
    err: RefCell<Vec<Value>>,
}

pub fn obj(pairs: Vec<(&str, Value)>) -> Value {
    Value::Object(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

pub fn s(v: impl Into<String>) -> Value {
    Value::Str(v.into())
}

pub fn finding_value(f: &Finding) -> Value {
    obj(vec![
        ("severity", s(f.severity.as_str())),
        ("code", s(f.code.as_str())),
        ("id", f.id.clone().map_or(Value::Null, Value::Str)),
        ("message", s(f.message.clone())),
    ])
}

/// The one fatal E000 a cannot-run reports, bounded like any message.
pub fn fatal(message: &str) -> Value {
    obj(vec![
        ("severity", s("fatal")),
        ("code", s("E000")),
        ("id", Value::Null),
        ("message", s(safe_text(message, MESSAGE_CAP))),
    ])
}

impl Ctx {
    pub fn new(store: Store) -> Ctx {
        Ctx { store, out: RefCell::new(Vec::new()), err: RefCell::new(Vec::new()) }
    }

    pub fn emit(&self, v: &Value) {
        self.out.borrow_mut().push(Out::Json(v.clone()));
    }

    pub fn text(&self, t: impl Into<String>) {
        self.out.borrow_mut().push(Out::Text(t.into()));
    }

    /// Where the two streams stand now, so a caller can read back what a
    /// nested command emitted (doctor --fix's init, pc-c810).
    pub fn mark(&self) -> (usize, usize) {
        (self.out.borrow().len(), self.err.borrow().len())
    }

    /// The JSON objects emitted on stdout, and those on stderr, since `mark`.
    pub fn since(&self, mark: (usize, usize)) -> (Vec<Value>, Vec<Value>) {
        let out = self.out.borrow()[mark.0..]
            .iter()
            .filter_map(|o| match o {
                Out::Json(v) => Some(v.clone()),
                Out::Text(_) => None,
            })
            .collect();
        (out, self.err.borrow()[mark.1..].to_vec())
    }

    /// A cannot-run: one fatal E000 on stderr, exit 2.
    pub fn cannot_run(&self, message: &str) -> u8 {
        self.err.borrow_mut().push(fatal(message));
        2
    }

    pub fn finish(self, exit: u8) -> Reply {
        Reply { stdout: self.out.into_inner(), stderr: self.err.into_inner(), exit }
    }
}
