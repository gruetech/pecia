//! The command-line surface, declared once as data.
//!
//! Hand-written rather than taken from a crate: the suite pins help CONTENT
//! (RULE 1 in the top-level help, `--format` in graph's, the width clamp in
//! board's and gantt's) and nothing of any library's layout, so owning the text
//! is the whole requirement, and this keeps the binary's dependency list to the
//! one crate the chain actually needs. It follows argparse's conventions, which
//! is what the reference implementation's users met: unique-prefix
//! abbreviation of long options, `--opt=value`, repeated `append` options, and
//! a usage error on stderr at exit 2.

use std::collections::HashMap;

#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    /// `--flag`, no value.
    Flag,
    /// `--opt VALUE`; the last occurrence wins.
    Value,
    /// `--opt VALUE`, repeatable, collected in order.
    Append,
    /// A required positional.
    Positional,
}

pub struct Arg {
    pub name: &'static str,
    pub dest: &'static str,
    pub kind: Kind,
    pub required: bool,
    pub int: bool,
    pub choices: &'static [&'static str],
    pub help: &'static str,
    /// argparse's `default=`: what the option reads as when it is not given.
    pub default: Option<&'static str>,
}

const fn arg(name: &'static str, dest: &'static str, kind: Kind) -> Arg {
    Arg { name, dest, kind, required: false, int: false, choices: &[], help: "", default: None }
}

impl Arg {
    const fn help(mut self, h: &'static str) -> Self { self.help = h; self }
    const fn required(mut self) -> Self { self.required = true; self }
    const fn int(mut self) -> Self { self.int = true; self }
    const fn choices(mut self, c: &'static [&'static str]) -> Self { self.choices = c; self }
    const fn default(mut self, d: &'static str) -> Self { self.default = Some(d); self }
}

pub struct Command {
    pub name: &'static str,
    pub help: &'static str,
    pub args: Vec<Arg>,
}

const JSON_HELP: &str = "print JSON, one document per line, for scripts (the default output is for people)";
const FORCE_HELP: &str = "bypass the write gate; brands the revision forced:true (checker still sees everything)";
const PRIORITIES: &[&str] = &["0", "1", "2", "3", "4"];
const TERMINAL: &[&str] = &["done", "dropped", "superseded"];
// The numbers are the renderers' clamp, stated where the help prints them; a
// test holds the two equal by measuring rendered output.
const WIDTH_HELP: &str = "override terminal width; the value is clamped to 60-160 columns, because the frame does not fit below 60 and stops being readable above 160 (piped output is fixed at 80)";
const NO_COLOR_HELP: &str = "disable ANSI colour; NO_COLOR is honoured too";

fn scalar_edges(help: &'static str) -> Vec<Arg> {
    [("--parent", "parent"), ("--duplicate-of", "duplicate_of"), ("--discovered-from", "discovered_from"),
     ("--caused-by", "caused_by"), ("--validates", "validates"), ("--supersedes", "supersedes")]
        .into_iter()
        .map(|(n, d)| arg(n, d, Kind::Value).help(help))
        .collect()
}

pub fn commands() -> Vec<Command> {
    let json = || arg("--json", "json", Kind::Flag).help(JSON_HELP);
    let force = || arg("--force", "force", Kind::Flag).help(FORCE_HELP);
    let mut add = vec![
        arg("--type", "type", Kind::Value).required(),
        arg("--title", "title", Kind::Value).required(),
        arg("--priority", "priority", Kind::Value).int().choices(PRIORITIES).default("2"),
        arg("--owner", "owner", Kind::Value),
        arg("--body", "body", Kind::Value).default(""),
        arg("--target", "target", Kind::Value).help("milestone target date YYYY-MM-DD"),
        arg("--context", "context", Kind::Value).help("a <scheme>:<target> reference into a shared orientation document (v2.5), e.g. doc:docs/orientation.md#anchor; the scheme must be declared in resolvers"),
        arg("--blocks", "blocks", Kind::Append),
        arg("--retires", "retires", Kind::Append).help("an id this record's work resolves: closing this record closes it"),
    ];
    add.extend(scalar_edges(""));
    add.extend([arg("--label", "label", Kind::Append), force(), json()]);

    let mut edit = vec![
        arg("id", "id", Kind::Positional),
        arg("--title", "title", Kind::Value),
        arg("--status", "status", Kind::Value),
        arg("--body", "body", Kind::Value),
        arg("--owner", "owner", Kind::Value),
        arg("--evidence", "evidence", Kind::Value),
        arg("--disposition", "disposition", Kind::Value),
        arg("--target", "target", Kind::Value),
        arg("--priority", "priority", Kind::Value).int().choices(PRIORITIES),
        arg("--context", "context", Kind::Value).help("a <scheme>:<target> reference into a shared orientation document (v2.5); 'none' clears it"),
        arg("--ratify", "ratify", Kind::Flag).help("mark this decision record accepted by the human: stamps ratified_by (PECIA_OWNER or the login user) and today's date in a new revision (v2.5). Decision records only; never required at creation (rule 3)"),
        arg("--blocks", "blocks", Kind::Append).help("id; repeat to add several. `--blocks none` clears the list"),
        arg("--retires", "retires", Kind::Append).help("replace the retires list. Refused by E012 if this record is already terminal and a target is not — use --also-closes for that"),
        arg("--also-closes", "also_closes", Kind::Append).help("add to retires AND close the target in the same atomic write, inheriting this record's disposition. Requires this record to be terminal already (the retroactive case); emits a JSON list"),
    ];
    edit.extend(scalar_edges("id, or 'none' to clear"));
    edit.extend([
        arg("--no-edges", "no_edges", Kind::Flag).help("declare this record intentionally has no edges"),
        arg("--label", "label", Kind::Append),
        force(),
        json(),
    ]);

    vec![
        Command { name: "init", help: "create the timeline and its snapshot projection — in .pecia/, or in the pinned store when PECIA_LOG_DIR is set (and strip any v1 merge attribute)", args: vec![json()] },
        Command { name: "add", help: "create a record (only type and title required — rule 3)", args: add },
        Command { name: "edit", help: "append a new revision with changes", args: edit },
        Command { name: "close", help: "append a terminal revision (disposition required)", args: vec![
            arg("id", "id", Kind::Positional),
            arg("--disposition", "disposition", Kind::Value).required(),
            arg("--status", "status", Kind::Value).choices(TERMINAL).default("done"),
            arg("--evidence", "evidence", Kind::Value),
            arg("--also-closes", "also_closes", Kind::Append).help("an id this closure also retires: adds the retires edge AND closes the target in the same atomic write, giving it this disposition. Emits a JSON list, in write order (targets first)"),
            force(),
            json(),
        ]},
        Command { name: "check", help: "structural checker (E001-E009, E011-E017). Exit 0 means \"well-formed,\" never \"true.\"", args: vec![
            arg("--ledger", "ledger", Kind::Value).help("path override (default .pecia/work.jsonl)"),
            arg("--config", "config", Kind::Value).help("config override, so a staged ledger is checked against its staged config, never a worktree one"),
            json(),
        ]},
        Command { name: "ready", help: "", args: vec![json()] },
        Command { name: "blocked", help: "", args: vec![json()] },
        Command { name: "graph", help: "", args: vec![
            arg("--format", "format", Kind::Value).choices(&["json", "mermaid"]).help("json or mermaid; without it graph prints for people, or JSON with --json"),
            arg("--json", "json", Kind::Flag).help("print JSON for scripts; an explicit --format wins (the default output is for people)"),
        ]},
        Command { name: "show", help: "one record as stored, every field (v3.3)", args: vec![
            arg("id", "id", Kind::Positional),
            arg("--history", "history", Kind::Flag).help("every entry that revised it, in log order, with its touched set"),
            json(),
        ]},
        Command { name: "gantt", help: "milestone timeline (ASCII by default; --mermaid for the portable projection)", args: vec![
            arg("--mermaid", "mermaid", Kind::Flag).help("emit Mermaid gantt source instead — for docs/CI, not a terminal"),
            arg("--width", "width", Kind::Value).int().help(WIDTH_HELP),
            arg("--no-color", "no_color", Kind::Flag).help(NO_COLOR_HELP),
        ]},
        Command { name: "next", help: "ready work, priorities 0-3, total order", args: vec![
            arg("--limit", "limit", Kind::Value).int().default("10"),
            json(),
        ]},
        Command { name: "audit", help: "advisory anti-rot surface + sampled truth audit", args: vec![
            arg("--sample", "sample", Kind::Value).int().default("0").help("deterministic sample of closed records for human truth-audit"),
            arg("--historical", "historical", Kind::Flag).help("list prose-only-linkage findings on records closed before the retires edge landed (v1.13); by default they are one counted line (D12)"),
            json(),
        ]},
        Command { name: "board", help: "read-only human projection (ANSI; not JSON, like gantt)", args: vec![
            arg("--width", "width", Kind::Value).int().help(WIDTH_HELP),
            arg("--no-color", "no_color", Kind::Flag).help(NO_COLOR_HELP),
        ]},
        Command { name: "doctor", help: "is the enforcement ACTIVE? (posture, never correctness)", args: vec![
            arg("--fix", "fix", Kind::Flag).help("apply the configuration fixes (hooks path, ignore rule, v1 merge attribute removal)"),
            json(),
        ]},
        Command { name: "publish", help: "publish the timeline to refs/pecia/log and the remote", args: vec![
            arg("--remote", "remote", Kind::Value).help("remote name (default: the first configured)"),
            json(),
        ]},
        Command { name: "sync", help: "re-chain onto a remote timeline that advanced first", args: vec![
            arg("--remote", "remote", Kind::Value).help("remote name (default: the first configured)"),
            arg("--take-landed", "take_landed", Kind::Append).help("resolve a same-field conflict on ID by taking the LANDED revision: your local-only revisions of it are discarded, each named with its rev and fields in the result. Repeat for several ids. Refused if ID has no such conflict (pc-ef5b)"),
            json(),
        ]},
        Command { name: "snapshot", help: "regenerate .pecia/work.jsonl from the log", args: vec![json()] },
        Command { name: "migrate", help: "reconstruct the v2 single timeline from all refs (pc-5c7c)", args: vec![
            arg("--dry-run", "dry_run", Kind::Flag).help("enumerate what would be written without writing it"),
            arg("--force", "force", Kind::Flag).help("rewrite an existing log — a timeline rewrite, never routine"),
            arg("--force-drop", "force_drop", Kind::Flag).help("accept erasing entries that exist only in the log (pc-824a)"),
            arg("--remote", "remote", Kind::Value).help("remote name to check the published timeline against when no local ref exists (default: the first configured)"),
            json(),
        ]},
        Command { name: MCP, help: "serve this repository's ledger to an MCP client over stdio: every command above is a tool, and each result carries its exit code and rule 1", args: vec![] },
    ]
}

/// The subcommand that serves the others; it is not itself a tool.
pub const MCP: &str = "mcp";

const RULE1: &str = "Exit 0 means \"well-formed,\" never \"true.\"";

#[derive(Debug, Clone)]
pub enum Val {
    Flag(bool),
    Str(String),
    List(Vec<String>),
}

pub struct Parsed {
    pub command: &'static str,
    values: HashMap<&'static str, Val>,
    /// An argument was not valid UTF-8. The reference decodes such bytes to
    /// lone surrogates, which its write gate refuses as E001; a Rust String
    /// cannot hold one, so the fact travels here and the write path refuses.
    pub tainted: bool,
}

impl Parsed {
    pub fn flag(&self, dest: &str) -> bool {
        matches!(self.values.get(dest), Some(Val::Flag(true)))
    }
    pub fn str(&self, dest: &str) -> Option<&str> {
        match self.values.get(dest) {
            Some(Val::Str(s)) => Some(s),
            _ => None,
        }
    }
    pub fn int(&self, dest: &str) -> Option<i64> {
        self.str(dest).and_then(|s| s.trim().parse().ok())
    }
    pub fn list(&self, dest: &str) -> Vec<String> {
        match self.values.get(dest) {
            Some(Val::List(v)) => v.clone(),
            _ => Vec::new(),
        }
    }
    pub fn has(&self, dest: &str) -> bool {
        self.values.contains_key(dest)
    }
}

pub enum Outcome {
    Run(Parsed),
    /// Help was asked for and printed: exit 0.
    Help(String),
    /// A usage error: printed to stderr, exit 2.
    Usage(String),
}

fn metavar(a: &Arg) -> String {
    a.dest.to_uppercase()
}

fn usage_line(cmd: Option<&Command>) -> String {
    match cmd {
        None => format!("usage: pecia [-h] [--version] {{{}}} ...", commands().iter().map(|c| c.name).collect::<Vec<_>>().join(",")),
        Some(c) => {
            let mut parts = vec![format!("usage: pecia {} [-h]", c.name)];
            for a in &c.args {
                let body = match a.kind {
                    Kind::Flag => a.name.to_string(),
                    Kind::Positional => a.name.to_string(),
                    _ => format!("{} {}", a.name, metavar(a)),
                };
                parts.push(if a.required || a.kind == Kind::Positional { body } else { format!("[{body}]") });
            }
            parts.join(" ")
        }
    }
}

fn help_text(cmd: Option<&Command>, all: &[Command]) -> String {
    match cmd {
        None => {
            let mut s = format!("{}\n\npecia — deterministic work ledger. RULE 1: {RULE1}\n\npositional arguments:\n", usage_line(None));
            for c in all {
                s.push_str(&format!("    {:<18}{}\n", c.name, c.help));
            }
            s.push_str("\noptions:\n  -h, --help            show this help message and exit\n  --version             show program's version number and exit\n\n");
            s.push_str("Exit codes: 0 clean (warnings allowed) / 1 error findings / 2 cannot-run. Output is for people; --json prints JSON, one document per line, for scripts (graph --format mermaid emits Mermaid source); board and gantt are human views only. Spec: spec/format-v2.md\n");
            s
        }
        Some(c) => {
            let mut s = usage_line(Some(c)) + "\n";
            if !c.help.is_empty() {
                s.push_str(&format!("\n{}\n", c.help));
            }
            let pos: Vec<&Arg> = c.args.iter().filter(|a| a.kind == Kind::Positional).collect();
            if !pos.is_empty() {
                s.push_str("\npositional arguments:\n");
                for a in pos {
                    s.push_str(&format!("  {}\n", a.name));
                }
            }
            s.push_str("\noptions:\n  -h, --help            show this help message and exit\n");
            for a in c.args.iter().filter(|a| a.kind != Kind::Positional) {
                let head = match a.kind {
                    Kind::Flag => a.name.to_string(),
                    _ if !a.choices.is_empty() => format!("{} {{{}}}", a.name, a.choices.join(",")),
                    _ => format!("{} {}", a.name, metavar(a)),
                };
                if a.help.is_empty() {
                    s.push_str(&format!("  {head}\n"));
                } else {
                    s.push_str(&format!("  {head:<22}{}\n", a.help));
                }
            }
            s
        }
    }
}

fn usage_error(cmd: Option<&Command>, msg: &str) -> Outcome {
    let prog = cmd.map_or("pecia".to_string(), |c| format!("pecia {}", c.name));
    Outcome::Usage(format!("{}\n{prog}: error: {msg}\n", usage_line(cmd)))
}

pub fn parse(argv: &[String]) -> Outcome {
    let all = commands();
    let mut it = argv.iter().skip(1).peekable();
    let Some(first) = it.next() else {
        return usage_error(None, "the following arguments are required: command");
    };
    if first == "-h" || first == "--help" {
        return Outcome::Help(help_text(None, &all));
    }
    // The workspace's one version (pc-00c9d07f9bde), as argparse's
    // action="version" prints it: to stdout, exit 0.
    if first == "--version" {
        return Outcome::Help(format!("pecia {}\n", env!("CARGO_PKG_VERSION")));
    }
    let Some(cmd) = all.iter().find(|c| c.name == first) else {
        let names: Vec<&str> = all.iter().map(|c| c.name).collect();
        return usage_error(None, &format!("argument command: invalid choice: '{first}' (choose from {})", names.join(", ")));
    };
    let mut values: HashMap<&'static str, Val> = HashMap::new();
    let mut positionals = cmd.args.iter().filter(|a| a.kind == Kind::Positional);
    let rest: Vec<&String> = it.collect();
    let mut i = 0;
    let mut unrecognized: Vec<String> = Vec::new();
    while i < rest.len() {
        let tok = rest[i].as_str();
        i += 1;
        if tok == "-h" || tok == "--help" {
            return Outcome::Help(help_text(Some(cmd), &all));
        }
        if tok.starts_with("--") && tok.len() > 2 {
            let (name, inline) = match tok.split_once('=') {
                Some((n, v)) => (n, Some(v.to_string())),
                None => (tok, None),
            };
            // Exact match first; otherwise a unique prefix, as argparse allows.
            let opts: Vec<&Arg> = cmd.args.iter().filter(|a| a.kind != Kind::Positional).collect();
            let found: Vec<&&Arg> = match opts.iter().find(|a| a.name == name) {
                Some(a) => vec![a],
                None => opts.iter().filter(|a| a.name.starts_with(name)).collect(),
            };
            let a = match found.as_slice() {
                [a] => **a,
                [] => { unrecognized.push(tok.to_string()); continue; }
                many => {
                    let names: Vec<&str> = many.iter().map(|a| a.name).collect();
                    return usage_error(Some(cmd), &format!("ambiguous option: {name} could match {}", names.join(", ")));
                }
            };
            match a.kind {
                Kind::Flag => {
                    if inline.is_some() {
                        return usage_error(Some(cmd), &format!("argument {}: ignored explicit argument '{}'", a.name, inline.unwrap()));
                    }
                    values.insert(a.dest, Val::Flag(true));
                }
                Kind::Value | Kind::Append => {
                    let value = match inline {
                        Some(v) => v,
                        None => match rest.get(i) {
                            Some(v) if !(v.starts_with('-') && v.len() > 1 && v.parse::<f64>().is_err()) => { i += 1; v.to_string() }
                            _ => return usage_error(Some(cmd), &format!("argument {}: expected one argument", a.name)),
                        },
                    };
                    if let Err(refusal) = check_value(cmd, a, &value) {
                        return refusal;
                    }
                    if a.kind == Kind::Append {
                        match values.entry(a.dest).or_insert_with(|| Val::List(Vec::new())) {
                            Val::List(v) => v.push(value),
                            other => *other = Val::List(vec![value]),
                        }
                    } else {
                        values.insert(a.dest, Val::Str(value));
                    }
                }
                Kind::Positional => unreachable!(),
            }
        } else if let Some(p) = positionals.next() {
            values.insert(p.dest, Val::Str(tok.to_string()));
        } else {
            unrecognized.push(tok.to_string());
        }
    }
    finish(cmd, values, &unrecognized)
}

/// An option's value against its declaration: an integer where one is
/// declared, one of the choices where there are choices.
fn check_value(cmd: &Command, a: &Arg, value: &str) -> Result<(), Outcome> {
    if a.int && value.trim().parse::<i64>().is_err() {
        return Err(usage_error(Some(cmd), &format!("argument {}: invalid int value: '{value}'", a.name)));
    }
    if !a.choices.is_empty() {
        let norm = if a.int { value.trim().parse::<i64>().map(|n| n.to_string()).unwrap_or_default() } else { value.to_string() };
        if !a.choices.contains(&norm.as_str()) {
            let shown = if a.int { norm } else { format!("'{value}'") };
            let choices = if a.int { a.choices.join(", ") } else { a.choices.iter().map(|c| format!("'{c}'")).collect::<Vec<_>>().join(", ") };
            return Err(usage_error(Some(cmd), &format!("argument {}: invalid choice: {shown} (choose from {choices})", a.name)));
        }
    }
    Ok(())
}

/// Required arguments present, nothing unrecognized, defaults filled.
fn finish(cmd: &Command, mut values: HashMap<&'static str, Val>, unrecognized: &[String]) -> Outcome {
    let missing: Vec<&str> = cmd
        .args
        .iter()
        .filter(|a| (a.required || a.kind == Kind::Positional) && !values.contains_key(a.dest))
        .map(|a| a.name)
        .collect();
    if !missing.is_empty() {
        return usage_error(Some(cmd), &format!("the following arguments are required: {}", missing.join(", ")));
    }
    if !unrecognized.is_empty() {
        return usage_error(None, &format!("unrecognized arguments: {}", unrecognized.join(" ")));
    }
    for a in &cmd.args {
        if let (Some(d), false) = (a.default, values.contains_key(a.dest)) {
            values.insert(a.dest, Val::Str(d.to_string()));
        }
    }
    Outcome::Run(Parsed { command: cmd.name, values, tainted: false })
}

/// A value supplied by NAME rather than on a command line — what an MCP tool
/// call carries.
#[derive(Debug, Clone)]
pub enum Named {
    Flag(bool),
    Str(String),
    Int(i64),
    List(Vec<String>),
}

/// Parse a command from named values, with the same declarations, checks
/// and defaults as `parse`. There is no tokenizing, so a value that starts
/// with `-` is a value, never an option.
pub fn parse_named(command: &str, named: &[(String, Named)]) -> Outcome {
    let all = commands();
    let Some(cmd) = all.iter().find(|c| c.name == command && c.name != MCP) else {
        let names: Vec<&str> = all.iter().map(|c| c.name).filter(|n| *n != MCP).collect();
        return usage_error(None, &format!("argument command: invalid choice: '{command}' (choose from {})", names.join(", ")));
    };
    let mut values: HashMap<&'static str, Val> = HashMap::new();
    let mut unrecognized: Vec<String> = Vec::new();
    for (key, value) in named {
        let Some(a) = cmd.args.iter().find(|a| a.dest == key) else {
            unrecognized.push(key.clone());
            continue;
        };
        let wrong = |what: &str| usage_error(Some(cmd), &format!("argument {}: expected {what}", a.name));
        match (a.kind, value) {
            (Kind::Flag, Named::Flag(true)) => {
                values.insert(a.dest, Val::Flag(true));
            }
            (Kind::Flag, Named::Flag(false)) => {}
            (Kind::Flag, _) => return wrong("a boolean"),
            (Kind::Append, Named::List(items)) => {
                for item in items {
                    if let Err(refusal) = check_value(cmd, a, item) {
                        return refusal;
                    }
                }
                values.insert(a.dest, Val::List(items.clone()));
            }
            (Kind::Value | Kind::Positional | Kind::Append, Named::Str(_) | Named::Int(_)) => {
                let text = match value {
                    Named::Int(n) => n.to_string(),
                    Named::Str(t) => t.clone(),
                    _ => unreachable!(),
                };
                if let Err(refusal) = check_value(cmd, a, &text) {
                    return refusal;
                }
                values.insert(a.dest, if a.kind == Kind::Append { Val::List(vec![text]) } else { Val::Str(text) });
            }
            (Kind::Value | Kind::Positional, _) => return wrong("one value"),
            (Kind::Append, _) => return wrong("a list of values"),
        }
    }
    finish(cmd, values, &unrecognized)
}
