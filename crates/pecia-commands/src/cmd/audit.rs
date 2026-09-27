//! `audit`: the advisory anti-rot surface. Findings never block.

use crate::args::Parsed;
use crate::cmd::query::config;
use crate::cmd::store_cmds::load_entries;
use crate::ctx::{obj, s, Ctx};
use crate::sys;
use pecia_core::audit::{audit_findings, AuditEnv, AuditOptions};
use pecia_core::config::{py_splitlines, py_strip, shlex_split};
use pecia_core::dates::parse_day;
use pecia_core::write::require_heads;
use pecia_core::Value;
use std::cell::{OnceCell, RefCell};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

/// How long a declared resolver may run before it counts as unresolved.
const RESOLVE_TIMEOUT: Duration = Duration::from_secs(10);

/// The audit's view of this checkout: resolvers run from the tree root, the
/// test tree is `tests/` and `dev/`, read once.
pub struct Env<'a> {
    pub root: &'a Path,
    tests: OnceCell<Vec<Vec<u8>>>,
    /// Whether each evidence command resolves: a ledger names a handful of
    /// commands many times, and each answer is a PATH search.
    resolvable: RefCell<HashMap<String, bool>>,
}

impl<'a> Env<'a> {
    pub fn new(root: &'a Path) -> Self {
        Env { root, tests: OnceCell::new(), resolvable: RefCell::new(HashMap::new()) }
    }

    fn test_files(&self) -> &[Vec<u8>] {
        self.tests.get_or_init(|| {
            let mut paths = Vec::new();
            for base in ["tests", "dev"] {
                python_files(&self.root.join(base), &mut paths);
            }
            paths.sort();
            paths.iter().filter_map(|p| std::fs::read(p).ok()).collect()
        })
    }
}

/// Every `*.py` beneath `dir`, not descending through a symlinked directory.
fn python_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(kind) = entry.file_type() else { continue };
        if kind.is_dir() {
            python_files(&path, out);
        } else if path.extension().is_some_and(|e| e == "py") {
            out.push(path);
        }
    }
}

fn contains(hay: &[u8], needle: &[u8]) -> bool {
    let Some((&first, rest)) = needle.split_first() else { return true };
    let mut at = 0;
    while let Some(i) = hay[at..].iter().position(|&b| b == first) {
        let start = at + i + 1;
        if hay.get(start..start + rest.len()) == Some(rest) {
            return true;
        }
        at = start;
    }
    false
}

impl AuditEnv for Env<'_> {
    fn resolve(&self, command: &str, target: &str) -> (bool, &'static str, Option<i64>, String) {
        let argv = match shlex_split(command) {
            Ok(argv) => argv,
            Err(_) => return (false, "unparseable-command", None, String::new()),
        };
        let Some(program) = argv.first() else { return (false, "empty-command", None, String::new()) };
        // A relative path names a file in the tree, wherever the audit runs.
        let program = if program.contains('/') { self.root.join(program) } else { PathBuf::from(program) };
        let spawned = Command::new(program)
            .args(&argv[1..])
            .arg(target)
            .current_dir(self.root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn();
        let mut child = match spawned {
            Ok(c) => c,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (false, "not-found", None, String::new()),
            Err(_) => return (false, "os-error", None, String::new()),
        };
        // Stderr is read beside the wait, and within the same deadline: a
        // resolver that leaves something holding the pipe open times out, as
        // the reference's read to end of stream does, rather than hanging.
        let started = std::time::Instant::now();
        let (tx, rx) = std::sync::mpsc::channel();
        let pipe = child.stderr.take();
        std::thread::spawn(move || {
            let mut bytes = Vec::new();
            if let Some(mut pipe) = pipe {
                let _ = std::io::Read::read_to_end(&mut pipe, &mut bytes);
            }
            let _ = tx.send(bytes);
        });
        let status = match sys::wait_timeout(&mut child, RESOLVE_TIMEOUT) {
            Ok(Some(status)) => status,
            Ok(None) => return (false, "timeout", None, String::new()),
            Err(_) => return (false, "os-error", None, String::new()),
        };
        let Ok(bytes) = rx.recv_timeout(RESOLVE_TIMEOUT.saturating_sub(started.elapsed())) else {
            return (false, "timeout", None, String::new());
        };
        use std::os::unix::process::ExitStatusExt;
        let first = || {
            let text = String::from_utf8_lossy(&bytes);
            py_splitlines(&text).into_iter().map(py_strip).find(|l| !l.is_empty()).unwrap_or("").to_string()
        };
        match status.code() {
            Some(0) => (true, "resolved", Some(0), String::new()),
            Some(n) => (false, "nonzero-exit", Some(i64::from(n)), first()),
            None => (false, "nonzero-exit", status.signal().map(|sig| -i64::from(sig)), first()),
        }
    }

    fn cited_in_tests(&self, rid: &str) -> bool {
        self.test_files().iter().any(|body| contains(body, rid.as_bytes()))
    }

    fn command_resolves(&self, token: &str) -> bool {
        if let Some(&known) = self.resolvable.borrow().get(token) {
            return known;
        }
        let found = sys::which(token) || self.root.join(token).exists();
        self.resolvable.borrow_mut().insert(token.to_string(), found);
        found
    }
}

pub fn options(sample: i64, resolve: bool, historical: bool) -> Result<AuditOptions, String> {
    let today = parse_day(&sys::today()?).unwrap_or(0);
    Ok(AuditOptions { today, sample, resolve, historical })
}

pub fn audit(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let (entries, findings) = load_entries(ctx)?;
    let records = pecia_core::check::records_of(&entries);
    let heads = match require_heads(&records, &findings) {
        Ok(h) => h,
        Err(m) => return Ok(ctx.cannot_run(&m)),
    };
    let cfg = config(ctx)?;
    let env = Env::new(&ctx.store.root);
    let opts = options(args.int("sample").unwrap_or(0), true, args.flag("historical"))?;
    let out = match audit_findings(&heads, &records, &cfg, &opts, &env) {
        Ok(out) => out,
        Err(m) => return Ok(ctx.cannot_run(&m)),
    };
    ctx.emit(&obj(vec![
        ("advisory", Value::Bool(true)),
        ("note", s("audit findings never block; they surface frontier-vs-rot")),
        ("findings", Value::Array(out)),
    ]));
    Ok(0)
}
