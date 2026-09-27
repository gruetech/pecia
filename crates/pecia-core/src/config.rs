//! `.pecia/config.yaml`: a flat `key: value` / `key: [a, b]` reader.
//!
//! Not YAML. The reference implementation reads exactly this shape and nothing
//! more, and this reads it the same way: `#` starts a comment, a line without a
//! `:` is skipped, a bracketed value is a comma-separated list, an all-digit
//! value is an integer, anything else is a string. A later key overwrites an
//! earlier one.

use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigValue {
    List(Vec<String>),
    Int(i64),
    Str(String),
}

/// The config, with the vocabularies every check consults derived ONCE at
/// parse: a check that asks for them per record would otherwise rebuild the
/// core lists and the extensions on every call.
#[derive(Debug, Clone)]
pub struct Config {
    values: HashMap<String, ConfigValue>,
    statuses: Vec<String>,
    types: Vec<String>,
    evidence_commands: Vec<String>,
    resolvers: HashMap<String, String>,
}

impl Default for Config {
    fn default() -> Self {
        Config::from_values(HashMap::new())
    }
}

pub const CORE_STATUSES: &[&str] = &["open", "in-progress", "done", "dropped", "superseded"];
pub const TERMINAL_STATUSES: &[&str] = &["done", "dropped", "superseded"];
pub const CORE_TYPES: &[&str] = &["defect", "task", "decision", "milestone", "question"];
pub const DEFAULT_EVIDENCE_COMMANDS: &[&str] = &[
    "bash", "bun", "cargo", "deno", "dotnet", "env", "false", "git", "go", "gradle", "java",
    "make", "mvn", "node", "npm", "npx", "perl", "pnpm", "pytest", "python", "python3",
    "ruby", "sh", "swift", "test", "tox", "true", "uv", "uvx", "yarn", "zsh",
];
pub const DEFAULT_RESOLVERS: &[(&str, &str)] = &[("claims", "dev/claims-ref.py")];

/// Line breaks as the reference implementation's `str.splitlines` sees them.
/// This is a config file an adopter edits by hand, not a stored line, so the
/// LF-only rule of the log and projection (v2.12) does not apply here.
pub fn py_splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let bytes: Vec<(usize, char)> = text.char_indices().collect();
    let mut i = 0;
    while i < bytes.len() {
        let (pos, ch) = bytes[i];
        let is_break = matches!(
            ch,
            '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}'
                | '\u{2028}' | '\u{2029}'
        );
        if is_break {
            out.push(&text[start..pos]);
            let mut next = pos + ch.len_utf8();
            if ch == '\r' && bytes.get(i + 1).map(|b| b.1) == Some('\n') {
                next += 1;
                i += 1;
            }
            start = next;
        }
        i += 1;
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

pub fn py_strip(s: &str) -> &str {
    s.trim_matches(crate::text::is_space)
}

impl Config {
    pub fn parse(text: &str) -> Config {
        let mut values = HashMap::new();
        for raw in py_splitlines(text) {
            let line = py_strip(raw.split('#').next().unwrap_or(""));
            let Some((key, value)) = line.split_once(':') else { continue };
            if line.is_empty() {
                continue;
            }
            let value = py_strip(value);
            let key = py_strip(key).to_string();
            if value.starts_with('[') && value.ends_with(']') && value.len() >= 2 {
                let inner = &value[1..value.len() - 1];
                let items = inner
                    .split(',')
                    .map(py_strip)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .collect();
                values.insert(key, ConfigValue::List(items));
            } else if !value.is_empty() {
                // ASCII digits only. The reference's `str.isdigit` also admits
                // other Unicode digits, and superscripts among them then crash
                // its int(); no config key this tool reads is an integer.
                let parsed = if value.bytes().all(|b| b.is_ascii_digit()) {
                    value.parse::<i64>().ok().map(ConfigValue::Int)
                } else {
                    None
                };
                values.insert(key, parsed.unwrap_or_else(|| ConfigValue::Str(value.to_string())));
            }
        }
        Config::from_values(values)
    }

    fn from_values(values: HashMap<String, ConfigValue>) -> Config {
        let mut c = Config { values, statuses: Vec::new(), types: Vec::new(), evidence_commands: Vec::new(), resolvers: HashMap::new() };
        let own = |core: &[&str], extra: Vec<String>| core.iter().map(|s| s.to_string()).chain(extra).collect();
        c.statuses = own(CORE_STATUSES, c.list("extra_statuses"));
        c.types = own(CORE_TYPES, c.list("extra_types"));
        c.evidence_commands = own(DEFAULT_EVIDENCE_COMMANDS, c.list("extra_evidence_commands"));
        c.resolvers = DEFAULT_RESOLVERS.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        for entry in c.list("resolvers") {
            if let Some((scheme, command)) = entry.split_once('=') {
                let (scheme, command) = (py_strip(scheme), py_strip(command));
                if !scheme.is_empty() && !command.is_empty() {
                    c.resolvers.insert(scheme.to_string(), command.to_string());
                }
            }
        }
        c
    }

    pub fn get(&self, key: &str) -> Option<&ConfigValue> {
        self.values.get(key)
    }

    /// A list-valued key. A bare scalar is read as a one-element list: the
    /// reference iterated a string character by character, so
    /// `extra_statuses: parked` admitted `p`, `a`, `r`, ... as statuses.
    pub fn list(&self, key: &str) -> Vec<String> {
        match self.values.get(key) {
            Some(ConfigValue::List(items)) => items.clone(),
            Some(ConfigValue::Str(s)) => vec![s.clone()],
            _ => Vec::new(),
        }
    }

    pub fn allowed_statuses(&self) -> &[String] {
        &self.statuses
    }

    pub fn allowed_types(&self) -> &[String] {
        &self.types
    }

    pub fn planned_ids(&self) -> Vec<String> {
        self.list("planned")
    }

    pub fn resolvers(&self) -> &HashMap<String, String> {
        &self.resolvers
    }

    pub fn evidence_commands(&self) -> &[String] {
        &self.evidence_commands
    }
}

/// POSIX `shlex.split` as the reference implementation performs it: whitespace
/// splitting, single quotes literal, double quotes where only `"` and `\` may be
/// escaped, a backslash escaping the next character outside quotes. An
/// unterminated quote or a trailing backslash is an error, not a token.
pub fn shlex_split(s: &str) -> Result<Vec<String>, &'static str> {
    #[derive(Clone, Copy, PartialEq)]
    enum St { Between, Word, Single, Double, Escape(Ret) }
    #[derive(Clone, Copy, PartialEq)]
    enum Ret { Word, Double }
    let mut out = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    let mut state = St::Between;
    for ch in s.chars() {
        state = match state {
            St::Between => match ch {
                ' ' | '\t' | '\r' | '\n' => St::Between,
                '\\' => St::Escape(Ret::Word),
                '\'' => { quoted = true; St::Single }
                '"' => { quoted = true; St::Double }
                c => { token.push(c); St::Word }
            },
            St::Word => match ch {
                ' ' | '\t' | '\r' | '\n' => {
                    out.push(std::mem::take(&mut token));
                    quoted = false;
                    St::Between
                }
                '\\' => St::Escape(Ret::Word),
                '\'' => { quoted = true; St::Single }
                '"' => { quoted = true; St::Double }
                c => { token.push(c); St::Word }
            },
            St::Single => match ch {
                '\'' => St::Word,
                c => { token.push(c); St::Single }
            },
            St::Double => match ch {
                '"' => St::Word,
                '\\' => St::Escape(Ret::Double),
                c => { token.push(c); St::Double }
            },
            St::Escape(ret) => {
                if ret == Ret::Double && ch != '"' && ch != '\\' {
                    token.push('\\');
                }
                token.push(ch);
                match ret { Ret::Word => St::Word, Ret::Double => St::Double }
            }
        };
    }
    match state {
        St::Single | St::Double => Err("No closing quotation"),
        St::Escape(_) => Err("No escaped character"),
        St::Word => { out.push(token); Ok(out) }
        St::Between => {
            if quoted { out.push(token); }
            Ok(out)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shlex_matches_the_posix_rules_the_reference_uses() {
        assert_eq!(shlex_split("a b  c").unwrap(), ["a", "b", "c"]);
        assert_eq!(shlex_split(r#"sh -c 'x y' "a\"b" "a\$b""#).unwrap(), ["sh", "-c", "x y", "a\"b", "a\\$b"]);
        assert_eq!(shlex_split(r"a\ b").unwrap(), ["a b"]);
        assert_eq!(shlex_split("''").unwrap(), [""]);
        assert!(shlex_split("'open").is_err());
        assert!(shlex_split("trailing\\").is_err());
    }

    #[test]
    fn config_reads_the_flat_shape() {
        let c = Config::parse("extra_statuses: [parked, held] # c\nplanned: [pc-x]\nn: 7\nbad line\n");
        assert_eq!(c.list("extra_statuses"), ["parked", "held"]);
        assert_eq!(c.planned_ids(), ["pc-x"]);
        assert_eq!(c.values.get("n"), Some(&ConfigValue::Int(7)));
    }
}
