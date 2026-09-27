//! `pecia mcp` against the CLI it serves, through the built binary.
//!
//! The property design/rust-port.md §5 asks for: one core, two renderers. A
//! tool result must BE the command's reply — its exit code, rule 1, and the
//! exact stdout and stderr the CLI prints for the same arguments — so every
//! test of the CLI's output covers the MCP surface too. Asserted here for
//! every read-only command, and for `show` directly: a record's body reaches
//! the session whole (v3.3).

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE1: &str = "Exit 0 means \"well-formed,\" never \"true.\"";
const PLANTED: &str = "PLANTEDBODYTOKEN";

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_pecia")
}

fn repo(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("pecia-mcp-it-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("dir");
    assert!(Command::new("git").args(["init", "-q"]).current_dir(&dir).status().expect("git").success());
    let dir = dir.canonicalize().expect("dir");
    for argv in [
        vec!["init"],
        vec!["add", "--type", "task", "--title", "first", "--body", PLANTED, "--owner", "t"],
        vec!["add", "--type", "milestone", "--title", "a milestone", "--target", "2099-01-01", "--owner", "t"],
    ] {
        let (_, _, code) = cli(&dir, &argv);
        assert_eq!(code, 0, "{argv:?}");
    }
    dir
}

/// The commands that print JSON on `--json` (v3.5): what a tool result
/// carries is the CLI's JSON form, so the CLI is asked for it.
const JSON_COMMANDS: &[&str] = &["init", "add", "edit", "close", "check", "ready", "blocked", "graph", "show", "next", "audit", "doctor", "publish", "sync", "snapshot", "migrate"];

fn cli(dir: &Path, argv: &[&str]) -> (String, String, i32) {
    let mut argv = argv.to_vec();
    if argv.first().is_some_and(|c| JSON_COMMANDS.contains(c)) && !argv.contains(&"--json") {
        argv.push("--json");
    }
    let o = Command::new(bin()).args(&argv).current_dir(dir).stdin(Stdio::null()).output().expect("run");
    (String::from_utf8_lossy(&o.stdout).into(), String::from_utf8_lossy(&o.stderr).into(), o.status.code().unwrap_or(-1))
}

/// One session: every frame in, every frame out.
fn session(dir: &Path, frames: &[String]) -> Vec<String> {
    let mut child = Command::new(bin()).arg("mcp").current_dir(dir).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().expect("serve");
    let mut stdin = child.stdin.take().expect("stdin");
    for f in frames {
        writeln!(stdin, "{f}").expect("write");
    }
    drop(stdin);
    let out = child.wait_with_output().expect("wait");
    assert!(out.status.success());
    String::from_utf8(out.stdout).expect("utf-8").lines().map(str::to_string).collect()
}

/// The `"text"` of a tool result's first content item, decoded.
fn result_text(frame: &str) -> String {
    let key = "\"text\":\"";
    let start = frame.find(key).expect("text") + key.len();
    let mut out = String::new();
    let mut chars = frame[start..].chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => return out,
            '\\' => match chars.next().expect("escape") {
                'n' => out.push('\n'),
                't' => out.push('\t'),
                'u' => {
                    let hex: String = chars.by_ref().take(4).collect();
                    out.push(char::from_u32(u32::from_str_radix(&hex, 16).expect("hex")).expect("char"));
                }
                other => out.push(other),
            },
            other => out.push(other),
        }
    }
    panic!("unterminated text in {frame}")
}

#[test]
fn every_read_only_tool_result_is_the_clis_json_or_its_view() {
    let dir = repo("same");
    let cases: &[(&str, &str, &[&str])] = &[
        ("check", "{}", &["check"]),
        ("ready", "{}", &["ready"]),
        ("blocked", "{}", &["blocked"]),
        ("next", "{\"limit\":5}", &["next", "--limit", "5"]),
        ("graph", "{}", &["graph"]),
        ("graph", "{\"format\":\"mermaid\"}", &["graph", "--format", "mermaid"]),
        ("gantt", "{\"mermaid\":true}", &["gantt", "--mermaid"]),
        ("gantt", "{\"width\":100,\"no_color\":true}", &["gantt", "--width", "100", "--no-color"]),
        ("audit", "{}", &["audit"]),
        ("board", "{\"no_color\":true}", &["board", "--no-color"]),
        ("doctor", "{}", &["doctor"]),
        ("close", "{\"id\":\"pc-nope\",\"disposition\":\"x\"}", &["close", "pc-nope", "--disposition", "x"]),
    ];
    let mut frames = vec![r#"{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"it","version":"0"}}}"#.to_string()];
    frames.extend(cases.iter().enumerate().map(|(i, (tool, args, _))| {
        format!(r#"{{"jsonrpc":"2.0","id":{},"method":"tools/call","params":{{"name":"{tool}","arguments":{args}}}}}"#, i + 1)
    }));
    let replies = session(&dir, &frames);
    assert_eq!(replies.len(), cases.len() + 1);
    for (i, (tool, _, argv)) in cases.iter().enumerate() {
        let (stdout, stderr, code) = cli(&dir, argv);
        let frame = &replies[i + 1];
        assert!(frame.contains(&format!("\"exit\":{code}")), "{tool}: {frame}");
        assert_eq!(result_text(frame), format!("exit {code} — {RULE1}\n{stdout}{stderr}"), "{tool} {argv:?}");
        assert_eq!(frame.contains("\"isError\":true"), code == 2, "{tool}: only a cannot-run is the tool's error");
    }
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn show_hands_the_session_a_record_s_body() {
    let dir = repo("shown");
    // The control: the body really is in the store the session reads.
    let log = std::fs::read_to_string(dir.join(".git/pecia/log.jsonl")).expect("log");
    assert!(log.contains(PLANTED), "the premise: the body was written");
    let mut frames = vec![
        r#"{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}"#.to_string(),
        r#"{"jsonrpc":"2.0","id":1,"method":"tools/list"}"#.to_string(),
    ];
    let id = log.split("\"id\":\"").nth(1).expect("id").split('"').next().expect("id").to_string();
    frames.push(format!(r#"{{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{{"name":"show","arguments":{{"id":"{id}"}}}}}}"#));
    let replies = session(&dir, &frames);
    assert_eq!(replies.len(), frames.len());
    assert!(replies[2].contains(PLANTED), "show's result carries the body: {}", replies[2]);
    assert!(replies[1].contains("\"name\":\"show\""), "show is a tool");
    let _ = std::fs::remove_dir_all(&dir);
}
