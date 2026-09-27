//! The single constructor for every diagnostic, and therefore the choke point
//! where every code inherits the bounding transform.

use crate::text::{safe_id, safe_text, MESSAGE_CAP};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Severity {
    Warning,
    Error,
    Fatal,
}

impl Severity {
    pub fn as_str(self) -> &'static str {
        match self {
            Severity::Warning => "warning",
            Severity::Error => "error",
            Severity::Fatal => "fatal",
        }
    }
}

/// Every code the format defines, as a closed set. A string code could be
/// emitted without being documented, or documented without being reachable;
/// an exhaustive enum makes the first a compile error and the second visible,
/// and `dev/spec-agree.py` can read this list instead of scraping literals.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Code {
    E000, E001, E002, E003, E004, E005, E006, E007, E008, E009,
    E011, E012, E013, E014, E015, E016, E017, E018, E019,
}

impl Code {
    pub fn as_str(self) -> &'static str {
        match self {
            Code::E000 => "E000", Code::E001 => "E001", Code::E002 => "E002",
            Code::E003 => "E003", Code::E004 => "E004", Code::E005 => "E005",
            Code::E006 => "E006", Code::E007 => "E007", Code::E008 => "E008",
            Code::E009 => "E009", Code::E011 => "E011", Code::E012 => "E012",
            Code::E013 => "E013", Code::E014 => "E014", Code::E015 => "E015",
            Code::E016 => "E016", Code::E017 => "E017", Code::E018 => "E018",
            Code::E019 => "E019",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub severity: Severity,
    pub code: Code,
    pub id: Option<String>,
    pub message: String,
    /// A GROUPED finding's participants (E012, E016, E009), kept beside the
    /// finding and never emitted: the write gate's narrowing rule reads them,
    /// so a repair that drops one participant is not refused as a new error
    /// (pc-54c7). `subject` is the record the finding is ABOUT, or None when
    /// the anchor is merely a representative of the group.
    pub group: Option<Vec<String>>,
    pub subject: Option<String>,
}

impl Finding {
    pub fn grouped(mut self, group: &[String], subject: Option<&str>) -> Self {
        let mut g: Vec<String> = group.iter().map(|p| safe_id(p)).collect();
        g.sort();
        g.dedup();
        self.group = Some(g);
        self.subject = subject.map(safe_id);
        self
    }

    /// The finding's identity as the write gate compares it: its emitted form.
    pub fn key(&self) -> (Severity, Code, Option<String>, String) {
        (self.severity, self.code, self.id.clone(), self.message.clone())
    }
}

/// Both fields are bounded, not just the message: a checker passes a record's
/// raw id before deciding it is malformed, so an unbounded id would sit one key
/// away from a sanitized message.
pub fn finding(severity: Severity, code: Code, rid: Option<&str>, message: &str) -> Finding {
    Finding {
        severity,
        code,
        id: rid.map(safe_id),
        message: safe_text(message, MESSAGE_CAP),
        group: None,
        subject: None,
    }
}

pub fn error(code: Code, rid: Option<&str>, message: &str) -> Finding {
    finding(Severity::Error, code, rid, message)
}

pub fn warning(code: Code, rid: Option<&str>, message: &str) -> Finding {
    finding(Severity::Warning, code, rid, message)
}
