//! `doctor`: is the enforcement ACTIVE? It reports the posture of a clone —
//! hooks configured, the hook present and runnable, the checker it resolves
//! runnable in the environment it will run in — never whether any gate is
//! correct (rule 1).
//!
//! Every relative path reasoned about here resolves against the work-tree
//! root, the way git will when it runs the hook, so the answer does not
//! depend on the directory doctor was invoked from (v2.16).

use crate::args::Parsed;
use crate::cmd::store_cmds::{init, lock_is_ignored};
use crate::ctx::{obj, s, Ctx};
use crate::sys;
use pecia_core::config::{py_splitlines, py_strip};
use pecia_core::text::{is_space, is_word_char, render_str, safe_text, MESSAGE_CAP, MESSAGE_VALUE_CAP};
use pecia_core::Value;
use pecia_store::git;
use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

const HOOK_DIRS: &[&str] = &["dev/hooks", ".githooks", "githooks"];

/// Whether the write gate runs. UNDECIDED is its own answer: a hook that is
/// executable but unreadable runs if it is a self-contained binary and dies
/// "Permission denied" if it is a script, and doctor cannot read it to tell
/// which (pc-937e) — so it is neither reported green nor reported absent.
#[derive(Clone, Copy, PartialEq)]
enum Gate {
    Active,
    Inactive,
    Undecided,
}

/// A path as the reference's `pathlib` spells it: separators collapsed, `.`
/// components and a trailing slash dropped. `..` is kept — that is lexical
/// normalisation, which pathlib does not do either.
fn norm(p: &Path) -> PathBuf {
    p.components().collect()
}

fn shown(p: &Path) -> String {
    norm(p).display().to_string()
}

/// `shlex.quote`.
fn sh_quote(v: &str) -> String {
    if v.is_empty() {
        return "''".into();
    }
    if v.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        return v.to_string();
    }
    format!("'{}'", v.replace('\'', "'\"'\"'"))
}

/// `os.path.realpath`: symlinks resolved component by component, a missing
/// tail kept as written.
fn realpath(p: &Path) -> PathBuf {
    fn walk(out: &mut PathBuf, p: &Path, depth: usize) {
        for comp in p.components() {
            match comp {
                Component::RootDir => *out = PathBuf::from("/"),
                Component::CurDir | Component::Prefix(_) => {}
                Component::ParentDir => {
                    out.pop();
                }
                Component::Normal(name) => {
                    let next = out.join(name);
                    match std::fs::read_link(&next) {
                        Ok(target) if depth < 40 => {
                            if target.is_relative() {
                                walk(out, &target, depth + 1);
                            } else {
                                *out = PathBuf::from("/");
                                walk(out, &target, depth + 1);
                            }
                        }
                        _ => *out = next,
                    }
                }
            }
        }
    }
    let abs = if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(p) };
    let mut out = PathBuf::from("/");
    walk(&mut out, &abs, 0);
    out
}

fn path_is_inside(candidate: &Path, root: &Path) -> bool {
    realpath(candidate).starts_with(realpath(root))
}

/// Opening for reading is refused. Any other failure is not a permission
/// answer, so it is not reported as one.
fn unreadable(p: &Path) -> bool {
    matches!(std::fs::File::open(p), Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied)
}

/// `which`, with every relative PATH entry — the empty one included, which
/// POSIX reads as the current directory — resolved against the work-tree
/// root, where git runs the hook.
fn which_from_root(name: &str, root: &Path) -> Option<PathBuf> {
    which_in(name, root, std::env::var("PATH").ok().as_deref())
}

/// `which_from_root` over a given PATH value (None: unset).
fn which_in(name: &str, root: &Path, path: Option<&str>) -> Option<PathBuf> {
    if name.contains('/') {
        let candidate = Path::new(name);
        let candidate = if candidate.is_absolute() { candidate.to_path_buf() } else { root.join(candidate) };
        return (candidate.is_file() && sys::access_x(&candidate)).then(|| norm(&candidate));
    }
    let path = path.unwrap_or("/bin:/usr/bin");
    let mut seen = BTreeSet::new();
    for entry in path.split(':') {
        let dir = if entry.is_empty() {
            root.to_path_buf()
        } else if Path::new(entry).is_absolute() {
            PathBuf::from(entry)
        } else {
            root.join(entry)
        };
        if !seen.insert(dir.clone()) {
            continue;
        }
        let candidate = dir.join(name);
        if sys::executable(&candidate) {
            return Some(norm(&candidate));
        }
    }
    None
}

/// What bash's `type -t` calls a name here: function, alias, builtin,
/// keyword, file — or None when bash is absent or says nothing.
fn shell_name_kind(name: &str) -> Option<String> {
    let path = std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin:/usr/sbin:/sbin".into());
    let bash = std::env::split_paths(&path).map(|d| d.join("bash")).find(|p| sys::executable(p))?;
    let mut child = Command::new(bash)
        .args(["-c", "type -t -- \"$1\"", "bash", name])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    sys::wait_timeout(&mut child, Duration::from_secs(10)).ok()??;
    let mut out = String::new();
    std::io::Read::read_to_string(&mut child.stdout.take()?, &mut out).ok()?;
    let kind = out.trim().to_string();
    (!kind.is_empty()).then_some(kind)
}

/// `env -S`'s own lexical rules over the rest of a shebang: whitespace
/// splits, quotes group, backslash escapes, `${NAME}` expands from this
/// environment — and a name this environment does not carry leaves the
/// token COMPUTED, which is undecidable here rather than missing.
fn env_split_string(text: &str, env: &dyn Fn(&str) -> Option<String>) -> (Vec<String>, bool) {
    let chars: Vec<char> = text.chars().collect();
    let (mut tokens, mut current) = (Vec::new(), String::new());
    let (mut started, mut computed) = (false, false);
    let mut quote: Option<char> = None;
    let mut i = 0;
    while i < chars.len() {
        let ch = chars[i];
        if quote.is_none() && " \t\n\r\x0c\x0b".contains(ch) {
            if started {
                tokens.push(std::mem::take(&mut current));
                started = false;
            }
            i += 1;
            continue;
        }
        if quote.is_none() && ch == '#' && !started {
            break; // a comment runs to the end
        }
        started = true;
        if ch == '\\' && i + 1 < chars.len() && (quote != Some('\'') || "\\'".contains(chars[i + 1])) {
            let next = chars[i + 1];
            current.push(match next {
                '_' => ' ',
                't' => '\t',
                'n' => '\n',
                'r' => '\r',
                'f' => '\x0c',
                'v' => '\x0b',
                other => other,
            });
            i += 2;
            continue;
        }
        if quote.is_none() && (ch == '"' || ch == '\'') {
            quote = Some(ch);
            i += 1;
            continue;
        }
        if quote == Some(ch) {
            quote = None;
            i += 1;
            continue;
        }
        if ch == '$' && chars.get(i + 1) == Some(&'{') && quote != Some('\'') {
            if let Some(off) = chars[i + 2..].iter().position(|c| *c == '}') {
                let name: String = chars[i + 2..i + 2 + off].iter().collect();
                match env(&name) {
                    Some(value) => current.push_str(&value),
                    None => {
                        computed = true;
                        current.push_str(&format!("${{{name}}}"));
                    }
                }
                i += off + 3;
                continue;
            }
        }
        current.push(ch);
        i += 1;
    }
    if started {
        tokens.push(current);
    }
    (tokens, computed)
}

/// The programs a file's shebang needs, as (kind, name): `file` for an
/// absolute interpreter, `path` for a name searched on PATH, `computed` for
/// one built from a variable this environment does not carry.
fn shebang_requirements(path: &Path) -> Vec<(&'static str, String)> {
    shebang_requirements_in(path, &|k| std::env::var(k).ok())
}

/// `shebang_requirements` against a given environment.
fn shebang_requirements_in(path: &Path, env: &dyn Fn(&str) -> Option<String>) -> Vec<(&'static str, String)> {
    let Ok(file) = std::fs::File::open(path) else { return Vec::new() };
    let mut buf = Vec::new();
    let _ = std::io::Read::read_to_end(&mut std::io::Read::take(file, 4096), &mut buf);
    if let Some(nl) = buf.iter().position(|b| *b == b'\n') {
        buf.truncate(nl + 1);
    }
    let first = String::from_utf8_lossy(&buf);
    let Some(rest) = first.strip_prefix("#!") else { return Vec::new() };
    let raw = py_strip(rest);
    let mut spans: Vec<(&str, usize, usize)> = Vec::new();
    let mut start: Option<usize> = None;
    for (i, c) in raw.char_indices().chain(std::iter::once((raw.len(), ' '))) {
        match (is_space(c), start) {
            (true, Some(st)) => {
                spans.push((&raw[st..i], st, i));
                start = None;
            }
            (false, None) => start = Some(i),
            _ => {}
        }
    }
    let Some(&(interpreter, _, _)) = spans.first() else { return Vec::new() };
    let mut needs = vec![(if interpreter.starts_with('/') { "file" } else { "path" }, interpreter.to_string())];
    if Path::new(interpreter).file_name().is_none_or(|n| n != "env") {
        return needs;
    }
    let mut computed = false;
    let mut args: Vec<String> = spans[1..].iter().map(|(t, _, _)| t.to_string()).collect();
    for &(token, st, end) in &spans[1..] {
        let split_from = if token == "-S" || token == "--split-string" {
            Some(end)
        } else if token.starts_with("--split-string=") {
            Some(st + "--split-string=".len())
        } else if token.starts_with("-S") && token.chars().count() > 2 {
            Some(st + 2)
        } else {
            None
        };
        if let Some(from) = split_from {
            (args, computed) = env_split_string(&raw[from..], env);
            break;
        }
    }
    let mut skip_value = false;
    for token in args {
        if skip_value {
            skip_value = false;
            continue;
        }
        if token.starts_with('-') {
            skip_value = matches!(token.as_str(), "-u" | "--unset" | "-C" | "--chdir");
            continue;
        }
        if token.contains('=') && !token.starts_with('=') {
            continue;
        }
        if computed && token.contains("${") {
            needs.push(("computed", token));
            return needs;
        }
        needs.push(("path", token));
        return needs;
    }
    needs
}

/// `\bw1\s+w2\s+w3\b` over a hook's code — the words the resolvers are spelt
/// with, whatever the whitespace between them.
fn words_appear(text: &str, words: &[&str]) -> bool {
    let chars: Vec<char> = text.chars().collect();
    let first: Vec<char> = words[0].chars().collect();
    'start: for i in 0..chars.len() {
        if i > 0 && is_word_char(chars[i - 1]) {
            continue;
        }
        let mut j = i;
        for (n, word) in words.iter().enumerate() {
            if n > 0 {
                let ws = chars[j..].iter().take_while(|c| is_space(**c)).count();
                if ws == 0 {
                    continue 'start;
                }
                j += ws;
            }
            let w: Vec<char> = if n == 0 { first.clone() } else { word.chars().collect() };
            if chars.get(j..j + w.len()) != Some(&w[..]) {
                continue 'start;
            }
            j += w.len();
        }
        if chars.get(j).is_none_or(|c| !is_word_char(*c)) {
            return true;
        }
    }
    false
}

/// A hook's code with its comments removed: from a `#` at the start of a
/// line or after whitespace, to the end of that line.
fn strip_comments(text: &str) -> String {
    py_splitlines(text)
        .into_iter()
        .map(|ln| {
            let mut prev: Option<char> = None;
            for (i, c) in ln.char_indices() {
                if c == '#' && prev.is_none_or(is_space) {
                    return &ln[..i];
                }
                prev = Some(c);
            }
            ln
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn merge_union_declared(ctx: &Ctx) -> bool {
    if let Some(out) = git::out(&ctx.store.root, &["check-attr", "merge", "--", ".pecia/work.jsonl"]) {
        return out.rsplit(':').next().unwrap_or("").trim() == "union";
    }
    let Ok(text) = std::fs::read_to_string(ctx.store.root.join(".gitattributes")) else { return false };
    py_splitlines(&text).into_iter().any(|line| {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            return false;
        }
        let mut parts = line.split_whitespace();
        let pattern = parts.next().unwrap_or("");
        (pattern == ".pecia/work.jsonl" || pattern == "work.jsonl") && parts.any(|a| a == "merge=union")
    })
}

struct Report {
    findings: Vec<Value>,
}

impl Report {
    fn add(&mut self, severity: &str, code: &str, message: &str, fix: Option<&str>) {
        let tail = fix.map_or(String::new(), |f| format!(" Fix: {}", safe_text(f, MESSAGE_VALUE_CAP)));
        let body = safe_text(message, MESSAGE_CAP.saturating_sub(tail.chars().count()));
        self.findings.push(obj(vec![
            ("severity", s(severity)),
            ("code", s(code)),
            ("id", Value::Null),
            ("message", s(safe_text(&(body + &tail), MESSAGE_CAP))),
        ]));
    }

    fn codes(&self) -> BTreeSet<String> {
        self.findings.iter().filter_map(|f| f.get("code").and_then(Value::as_str).map(str::to_string)).collect()
    }

    fn errors(&self) -> usize {
        self.findings.iter().filter(|f| f.get("severity").and_then(Value::as_str) == Some("error")).count()
    }

    fn print(&self, ctx: &Ctx) {
        for f in &self.findings {
            ctx.emit(f);
        }
    }
}

/// D012: the checker the active hook resolves, and every program its
/// interpreter chain needs, must be runnable where the hook runs.
fn check_resolution(r: &mut Report, top: &Path, hook: &Path, hook_code: &str) {
    let env_cli = std::env::var("PECIA_CLI").ok().filter(|v| !v.is_empty());
    let on_path = which_from_root("pecia", top);
    if env_cli.is_none() && words_appear(hook_code, &["command", "-v", "pecia"]) && !words_appear(hook_code, &["type", "-P", "pecia"]) {
        if let Some(kind) = shell_name_kind("pecia").filter(|k| matches!(k.as_str(), "function" | "alias" | "builtin" | "keyword")) {
            r.add("error", "D012", &format!(
                "the active hook resolves its pecia checker with `command -v pecia`, and in this environment `pecia` is a shell {kind} — `command -v` matches it and prints its bare name, which the hook rebases to {}, a path that does not exist, so the gate fails closed and every ordinary commit is refused at 'no pecia CLI found'. A PATH search cannot see a shell {kind}, so the resolution doctor models is not the one this hook performs.",
                shown(&top.join("pecia"))
            ), Some("update the hook to the shipped resolver (`type -P pecia`, v2.17), which searches PATH for the executable file the hook can actually run — or unset the shell function"));
        }
    }
    let resolved = match (&env_cli, on_path) {
        (Some(v), _) => {
            let p = Path::new(v);
            if p.is_absolute() { p.to_path_buf() } else { top.join(p) }
        }
        (None, Some(p)) => p,
        (None, None) => top.join("pecia_cli.py"),
    };
    let shown_resolved = shown(&resolved);
    let is_py = resolved.extension().is_some_and(|e| e == "py");
    let name = resolved.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    if !resolved.is_file() {
        let cli_state = if env_cli.is_some() {
            "set to a missing file (a relative value resolves from the repository root, where git runs hooks)"
        } else {
            "unset"
        };
        r.add("error", "D012", &format!(
            "the active hook resolves no pecia checker — $PECIA_CLI is {cli_state}, `pecia` is not on PATH, and {shown_resolved} does not exist, so the gate fails closed and every ordinary commit is refused at 'no pecia CLI found' while posture read clean."
        ), Some("set PECIA_CLI to the checker, put `pecia` on PATH, or place pecia_cli.py at the repository root"));
    } else if is_py && unreadable(&resolved) {
        r.add("error", "D012", &format!(
            "the active hook resolves {shown_resolved} as its pecia checker, but the file cannot be opened for reading — the hook runs a .py checker through python3, which must read it, so every ordinary commit dies 'Permission denied' while posture read clean."
        ), Some(&format!("chmod +r {}, or point PECIA_CLI at a checker this user can read", sh_quote(&shown_resolved))));
    } else if !is_py && !sys::access_x(&resolved) {
        r.add("error", "D012", &format!(
            "the active hook resolves {shown_resolved} as its pecia checker, but the file is not executable and is not a .py the hook would run via python3 — the hook executes it directly, so every ordinary commit dies 'Permission denied' while posture read clean."
        ), Some("chmod +x the checker, point PECIA_CLI at a .py file, or put an executable `pecia` on PATH"));
    } else if !is_py && unreadable(&resolved) {
        r.add("warning", "D012", &format!(
            "the active hook resolves {shown_resolved} as its pecia checker and executes it directly; it is executable but NOT readable, so doctor cannot tell whether the hook can run it — an interpreted wrapper (a `#!` script) dies 'Permission denied' at every commit, a self-contained binary does not."
        ), Some(&format!("chmod +r {} so the posture is decidable", sh_quote(&shown_resolved))));
    } else {
        let mut needs: Vec<(&str, String, String)> =
            shebang_requirements(hook).into_iter().map(|(k, n)| (k, n, "the hook's own interpreter".to_string())).collect();
        if is_py {
            needs.push(("path", "python3".into(), format!("the hook runs a .py checker as `python3 {name}`")));
        } else {
            needs.extend(shebang_requirements(&resolved).into_iter().map(|(k, n)| (k, n, format!("the hook execs {name} directly and its shebang names this"))));
        }
        for (kind, prog, why) in needs {
            match kind {
                "computed" => r.add("warning", "D012", &format!(
                    "the active hook resolves {shown_resolved} as its pecia checker, and the interpreter it names is built at run time from {prog} — {why}, and doctor's own environment does not carry that variable, so whether the program resolves cannot be decided here."
                ), Some(&format!("set {prog} in the environment the hook inherits, or name the interpreter directly in the shebang"))),
                "path" => {
                    if which_from_root(&prog, top).is_none() {
                        r.add("error", "D012", &format!(
                            "the active hook resolves {shown_resolved} as its pecia checker, and {prog} is on no directory of this PATH — {why}, so every ordinary commit dies '{prog}: command not found' (or `env: {prog}: No such file or directory`) while posture read clean."
                        ), Some(&format!("install {prog} or put it on the PATH the hook inherits, or point PECIA_CLI at a checker whose interpreter is present")));
                    }
                }
                _ => {
                    if !sys::access_x(Path::new(&prog)) {
                        r.add("error", "D012", &format!(
                            "the active hook resolves {shown_resolved} as its pecia checker, and {prog} is not an executable file — {why}, and the kernel execs that path directly, so every ordinary commit dies while posture read clean."
                        ), Some(&format!("install the interpreter at {}, or point the shebang at one that exists", sh_quote(&prog))));
                    }
                }
            }
        }
    }
}

/// Where a refused init put its refusal, read from what it emitted (pc-c810):
/// a fatal on stderr is named with its reason, and findings plus the
/// `initialized: false` note on stdout are named as such.
fn init_refusal_account(out: &[Value], err: &[Value]) -> String {
    let fatal = err.iter().find(|o| o.get("severity").and_then(Value::as_str) == Some("fatal"));
    if let Some(f) = fatal {
        let message = f.get("message").and_then(Value::as_str).unwrap_or("");
        return format!("its reason is the fatal on stderr, printed above: {}", safe_text(message, MESSAGE_VALUE_CAP));
    }
    if out.iter().any(|o| o.get("initialized").is_some()) {
        return "its findings and its `initialized: false` note are on stdout, printed above".into();
    }
    "it printed no reason on either stream".into()
}

pub fn doctor(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    let root = ctx.store.root.clone();
    if git::out(&root, &["rev-parse", "--is-inside-work-tree"]).as_deref() != Some("true") {
        return Ok(ctx.cannot_run("not inside a git work tree — doctor reports the enforcement posture of a git repository"));
    }
    let top = git::out(&root, &["rev-parse", "--show-toplevel"]).filter(|t| !t.is_empty()).map(PathBuf::from).unwrap_or_else(|| root.clone());
    let mut r = Report { findings: Vec::new() };
    let mut fixes: Vec<String> = Vec::new();
    let configured = git::out(&root, &["config", "core.hooksPath"]);
    let hooks_path = configured.clone().filter(|c| !c.is_empty());
    let shipped: Vec<&str> = HOOK_DIRS.iter().copied().filter(|d| top.join(d).join("pre-commit").is_file()).collect();
    let active_dir = hooks_path.as_deref().map(|c| if Path::new(c).is_absolute() { norm(Path::new(c)) } else { norm(&top.join(c)) });
    let ledger_present = top.join(".pecia").join("work.jsonl").exists();
    let gate = match &active_dir {
        Some(dir) if dir.join("pre-commit").is_file() && sys::access_x(&dir.join("pre-commit")) => {
            if unreadable(&dir.join("pre-commit")) { Gate::Undecided } else { Gate::Active }
        }
        _ => Gate::Inactive,
    };
    if ledger_present && gate == Gate::Inactive && shipped.is_empty() {
        r.add("warning", "D010", "this repo has a pecia ledger and no write gate is active in this clone, so nothing checks a record before it is committed. Exit 0 from `check` is something you ran, not something the repo enforces.",
              Some("mkdir -p dev/hooks && cp templates/pre-commit dev/hooks/pre-commit && chmod +x dev/hooks/pre-commit && git config core.hooksPath dev/hooks"));
    }
    if !shipped.is_empty() && hooks_path.is_none() {
        let target = shipped[0];
        r.add("error", "D001", &format!("repo ships {target}/pre-commit but core.hooksPath is unset — this gate has never run in this clone."),
              Some(&format!("pecia doctor --fix (or: git config core.hooksPath {})", sh_quote(target))));
        fixes.push(format!("set core.hooksPath to {target}"));
    } else if let (Some(configured), Some(active_dir)) = (&hooks_path, &active_dir) {
        let hook = active_dir.join("pre-commit");
        if !hook.is_file() {
            let what = if hook.is_dir() {
                "a DIRECTORY sits at that path, which git cannot exec — every commit dies at 'fatal: cannot exec' (pc-aff4)"
            } else {
                "no pre-commit hook is there — commits are running unguarded while configured to be guarded"
            };
            r.add("error", "D002", &format!("core.hooksPath is {} but {what}.", render_str(configured)), None);
        } else if !sys::access_x(&hook) {
            r.add("error", "D003", &format!("{configured}/pre-commit is not executable — git skips it SILENTLY, which reads exactly like a passing gate."),
                  Some(&format!("chmod +x {}", sh_quote(&format!("{configured}/pre-commit")))));
        } else {
            let hook_text = match std::fs::read(&hook) {
                Ok(bytes) => String::from_utf8_lossy(&bytes).into_owned(),
                Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                    r.add("warning", "D003", &format!(
                        "{configured}/pre-commit is executable but NOT readable, so doctor cannot tell whether git can run it: an interpreted hook (a `#!` script) dies 'Permission denied' at every commit, a self-contained binary does not — and this also leaves the checker resolution below unchecked. Until it is readable the gate is UNDECIDED: gate_active is null and ok is false (pc-937e)."
                    ), Some(&format!("chmod +r {}", sh_quote(&format!("{configured}/pre-commit")))));
                    String::new()
                }
                Err(_) => String::new(),
            };
            let hook_code = strip_comments(&hook_text);
            if hook_code.contains("PECIA_CLI") {
                check_resolution(&mut r, &top, &hook, &hook_code);
            }
        }
        for extra in &shipped {
            if realpath(active_dir) == realpath(&top.join(extra)) {
                continue;
            }
            if path_is_inside(active_dir, &top) {
                r.add("warning", "D004", &format!("repo also ships {extra}/pre-commit, which is not the configured hooks path — one of them is dead code."), None);
            } else {
                let scope = if git::out(&root, &["config", "extensions.worktreeConfig"]).as_deref() == Some("true") { "--worktree " } else { "" };
                r.add("warning", "D008", &format!(
                    "core.hooksPath resolves OUTSIDE this working tree ({}) — the hook that runs is that checkout's copy and tracks whatever branch IT has checked out, not this one. This tree's own {extra}/pre-commit never runs.",
                    shown(active_dir)
                ), Some(&format!("git config {scope}core.hooksPath {}", sh_quote(extra))));
            }
        }
    }
    let pecia_dir = root.join(".pecia");
    if pecia_dir.exists() {
        if !lock_is_ignored(ctx) {
            r.add("warning", "D005", ".pecia/.lock is not git-ignored — it becomes permanent untracked noise, and noise trains readers to skim the worktree-activity report that catches swept files.",
                  Some("pecia doctor --fix (or re-run: pecia init)"));
            fixes.push("ignore .pecia/.lock".into());
        }
        if merge_union_declared(ctx) {
            r.add("warning", "D006", "the snapshot still declares merge=union — a v1 attribute. The snapshot is a generated projection under v2 and is never merged; a union here splices two branches' projections into a timeline that never existed.",
                  Some("pecia doctor --fix (or re-run: pecia init)"));
            fixes.push("remove merge=union".into());
        }
        let untracked = |p: &str| git::out(&root, &["ls-files", "--error-unmatch", p]).is_none();
        if pecia_dir.join("work.jsonl").exists() && untracked(".pecia/work.jsonl") {
            r.add("warning", "D007", "the ledger is not tracked by git — an uncommitted ledger is not custody; nobody else can see or review it.", Some("git add .pecia/work.jsonl"));
        }
        if pecia_dir.join("config.yaml").exists() && untracked(".pecia/config.yaml") {
            r.add("warning", "D011", "the config (.pecia/config.yaml) is not tracked by git — commit gates validate the staged ledger against the STAGED config, which substitutes empty, so a record legal under the live vocabulary is refused at commit while the live check stays green.",
                  Some("git add .pecia/config.yaml"));
        }
    }
    if ctx.store.log_path().is_some_and(|l| !l.exists()) && git::out(&root, &["remote"]).is_some_and(|r| !r.is_empty()) {
        r.add("error", "D009", "no local pecia timeline, and this clone has a remote configured — this is the fresh-clone state `pecia sync` exists to hydrate. Do not run `pecia migrate` here: it builds a LOCAL chain from this clone's own history (which a plain `git clone` does not include — refs/pecia/log is never fetched) and can silently diverge from what is already published.",
              Some("pecia sync"));
    }

    if args.flag("fix") {
        let (mut applied, mut refused): (Vec<String>, Vec<String>) = (Vec::new(), Vec::new());
        let mut automated: BTreeSet<String> = BTreeSet::new();
        let codes = r.codes();
        if !fixes.is_empty() {
            if !shipped.is_empty() && hooks_path.is_none() {
                let (code, _, err) = git::run(&root, &["config", "core.hooksPath", shipped[0]], None);
                if code == 0 && git::out(&root, &["config", "core.hooksPath"]).as_deref() == Some(shipped[0]) {
                    applied.push(format!("core.hooksPath={}", shipped[0]));
                    automated.insert("D001".into());
                } else {
                    let why = if err.is_empty() { "git config failed".to_string() } else { err };
                    refused.push(format!("core.hooksPath write refused ({why}) — D001 stands"));
                }
            }
            if pecia_dir.exists() {
                let mark = ctx.mark();
                if init(ctx, args)? == 0 {
                    let mut cleared: Vec<&str> = Vec::new();
                    if lock_is_ignored(ctx) {
                        automated.insert("D005".into());
                        cleared.push("ignore rule in place");
                    } else if codes.contains("D005") {
                        refused.push("init exited 0 and .pecia/.lock is still not ignored — not credited (D005 stands)".into());
                    }
                    if !merge_union_declared(ctx) {
                        automated.insert("D006".into());
                        cleared.push("no v1 merge attribute declared");
                    } else if codes.contains("D006") {
                        refused.push("init exited 0 and the merge=union attribute is still declared — not credited (D006 stands)".into());
                    }
                    applied.push(if cleared.is_empty() { "re-ran init".into() } else { format!("re-ran init ({})", cleared.join("; ")) });
                } else {
                    // Derived from what init emitted, on every branch (pc-c810):
                    // the fresh-clone refusal is a fatal on stderr and emits no
                    // `initialized` object, which one fixed sentence could not say.
                    let (out, err) = ctx.since(mark);
                    refused.push(format!("init refused ({}) — nothing about the ignore rule or merge attribute changed", init_refusal_account(&out, &err)));
                }
            }
        }
        let unfixed: Vec<Value> = codes.difference(&automated).map(|c| s(c.clone())).collect();
        r.print(ctx);
        let strs = |v: &[String]| Value::Array(v.iter().map(|x| s(x.clone())).collect());
        let mut result = vec![
            ("fixed", strs(&applied)),
            ("posture_clean", Value::Bool(unfixed.is_empty() && refused.is_empty())),
            ("unfixed", Value::Array(unfixed)),
            ("rerun", s("pecia doctor")),
            ("note", s("Fixes cover configuration only. `unfixed` names the findings whose remedy needs a human; each Fix: recipe is printed in its finding above. The exit code says whether what this command ATTEMPTED took (v2.16); `posture_clean` says whether anything is left, and is the field to read — a --fix that attempts nothing and repairs nothing exits 0.")),
        ];
        if !refused.is_empty() {
            result.push(("refused", strs(&refused)));
        }
        ctx.emit(&obj(result));
        return Ok(if refused.is_empty() { 0 } else { 1 });
    }
    r.print(ctx);
    let errors = r.errors();
    let gate_ok = gate == Gate::Active || !ledger_present;
    ctx.emit(&obj(vec![
        ("ok", Value::Bool(errors == 0 && gate_ok)),
        ("errors", Value::Int(errors as i64)),
        ("warnings", Value::Int((r.findings.len() - errors) as i64)),
        ("gate_active", match gate { Gate::Active => Value::Bool(true), Gate::Inactive => Value::Bool(false), Gate::Undecided => Value::Null }),
        ("hooks_path", configured.map_or(Value::Null, s)),
        ("ships_hooks", Value::Array(shipped.iter().map(|d| s(*d)).collect())),
        ("note", s("doctor reports whether gates are ACTIVE, never whether they are correct. An active gate can still be wrong; see rule 1. `ok` is that posture answer and NOT the exit code: an unguarded ledger is ok: false at exit 0 (v2.16).")),
    ]));
    Ok(if errors > 0 { 1 } else { 0 })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_env(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn env_split_string_follows_envs_lexical_rules() {
        assert_eq!(env_split_string("uv run --script # c", &no_env).0, ["uv", "run", "--script"]);
        assert_eq!(env_split_string("'a b' \"c\\\"d\" e\\_f", &no_env).0, ["a b", "c\"d", "e f"]);
        let (t, computed) = env_split_string("${PECIA_TEST_INTERP}/py x", &no_env);
        assert!(computed);
        assert_eq!(t, ["${PECIA_TEST_INTERP}/py", "x"]);
    }

    // --- TheShebangIsReadAsEnvReadsIt (pc-fc1e), restated ---------------

    fn requirements(line: &str, env: &dyn Fn(&str) -> Option<String>) -> Vec<(&'static str, String)> {
        // One file per call: two arms reading the same shebang line run in
        // parallel, and a shared name let one delete the other's fixture.
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!("pecia-shebang-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let path = dir.join(format!("checker-{n}"));
        std::fs::write(&path, format!("{line}\nexit 0\n")).expect("write");
        let out = shebang_requirements_in(&path, env);
        let _ = std::fs::remove_file(&path);
        out
    }

    fn want(pairs: &[(&'static str, &str)]) -> Vec<(&'static str, String)> {
        pairs.iter().map(|(k, v)| (*k, v.to_string())).collect()
    }

    #[test]
    fn shebang_kill_a_quoted_program_name_under_dash_s_is_not_a_program_name() {
        assert_eq!(requirements("#!/usr/bin/env -S 'sh'", &no_env), want(&[("file", "/usr/bin/env"), ("path", "sh")]));
        assert_eq!(requirements("#!/usr/bin/env -S \"sh\" -x", &no_env), want(&[("file", "/usr/bin/env"), ("path", "sh")]));
    }

    #[test]
    fn shebang_kill_an_escape_under_dash_s_is_not_part_of_the_name() {
        assert_eq!(requirements("#!/usr/bin/env -S my\\_prog run", &no_env), want(&[("file", "/usr/bin/env"), ("path", "my prog")]));
    }

    #[test]
    fn shebang_a_computed_name_is_reported_as_undecided_not_as_missing() {
        assert_eq!(
            requirements("#!/usr/bin/env -S ${PECIA_TEST_INTERP} run", &no_env),
            want(&[("file", "/usr/bin/env"), ("computed", "${PECIA_TEST_INTERP}")])
        );
    }

    #[test]
    fn shebang_control_a_resolvable_variable_is_resolved() {
        let env = |k: &str| (k == "PECIA_TEST_INTERP").then(|| "python3".to_string());
        assert_eq!(requirements("#!/usr/bin/env -S ${PECIA_TEST_INTERP} run", &env), want(&[("file", "/usr/bin/env"), ("path", "python3")]));
    }

    #[test]
    fn shebang_control_quotes_outside_dash_s_are_part_of_the_name() {
        assert_eq!(requirements("#!/usr/bin/env 'sh'", &no_env), want(&[("file", "/usr/bin/env"), ("path", "'sh'")]));
    }

    #[test]
    fn shebang_control_the_shapes_pc_a2da_derived_are_unchanged() {
        assert_eq!(requirements("#!/usr/bin/env -S uv run --script", &no_env), want(&[("file", "/usr/bin/env"), ("path", "uv")]));
        assert_eq!(requirements("#!/usr/bin/env bash", &no_env), want(&[("file", "/usr/bin/env"), ("path", "bash")]));
        assert_eq!(requirements("#!/bin/sh", &no_env), want(&[("file", "/bin/sh")]));
        assert_eq!(requirements("#!/usr/bin/env -S -u FOO bash", &no_env), want(&[("file", "/usr/bin/env"), ("path", "bash")]));
        assert_eq!(requirements("#!/usr/bin/env -S FOO=1 bash", &no_env), want(&[("file", "/usr/bin/env"), ("path", "bash")]));
    }

    #[test]
    fn shebang_the_repository_s_own_executables_read_cleanly() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        for name in ["pecia_cli.py", "dev/claims-check.py", "dev/prose-check.py", "dev/claims-edit.py", "dev/hooks/pre-commit", "templates/pre-commit", "dev/alloy-gate.sh"] {
            let needs = shebang_requirements_in(&root.join(name), &no_env);
            assert!(!needs.is_empty(), "{name} carries a shebang");
            for (kind, program) in needs {
                assert!(kind == "path" || kind == "file", "{name}: {kind}");
                assert!(!program.contains('\'') && !program.contains('"'), "{name}: {program}");
            }
        }
    }

    // --- DoctorChecksCheckerResolution: the empty PATH entry ---------------

    #[test]
    fn an_empty_path_entry_means_the_root_not_the_cwd() {
        let root = std::env::temp_dir().join(format!("pecia-which-{}", std::process::id()));
        std::fs::create_dir_all(root.join("deep")).expect("dir");
        let tool = root.join("sometool");
        std::fs::write(&tool, "#!/bin/sh\nexit 0\n").expect("write");
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o755)).expect("chmod");
        assert_eq!(which_in("sometool", &root, Some(":/usr/bin:/bin")), Some(tool.clone()));
        assert_eq!(which_in("sometool", &root, Some("/usr/bin:/bin")), None, "the control: without the empty entry the root is not searched");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn resolver_words_match_across_any_whitespace_and_whole_words_only() {
        assert!(words_appear("x=$(command  -v\tpecia)", &["command", "-v", "pecia"]));
        assert!(!words_appear("command -v pecia_cli", &["command", "-v", "pecia"]));
        assert!(!words_appear("mycommand -v pecia", &["command", "-v", "pecia"]));
    }

    #[test]
    fn comments_strip_from_a_hash_at_line_start_or_after_whitespace() {
        assert_eq!(strip_comments("a # c\nb#not\n# all"), "a \nb#not\n");
    }

    #[test]
    fn quoting_matches_shlex() {
        assert_eq!(sh_quote("dev/hooks"), "dev/hooks");
        assert_eq!(sh_quote("a b'c"), "'a b'\"'\"'c'");
        assert_eq!(sh_quote(""), "''");
    }
}
