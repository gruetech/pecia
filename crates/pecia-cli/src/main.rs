//! `pecia` — the command-line interface.
//!
//! Exit codes are the format's contract, not a convenience: 0 clean (warnings
//! allowed), 1 findings, 2 cannot-run. RULE 1: exit 0 means "well-formed,"
//! never "true."
//!
//! This file is the CLI's RENDERER and nothing else: the commands return a
//! `Reply` (pecia-commands), and it is written here to stdout and stderr.
//! `pecia mcp` hands the same commands to the MCP server instead.

use pecia_commands::args::{self, Outcome};
use pecia_commands::{human, Out, Reply};
use std::io::Write;
use std::process::ExitCode;

/// Write a reply to the two streams: for people, or as JSON with `--json`
/// (v3.5). A closed pipe (`pecia next | head -1`) ends the writing quietly
/// rather than panicking.
fn render(reply: &Reply, parsed: Option<&args::Parsed>) -> ExitCode {
    let person = parsed.filter(|p| !human::json_mode(p));
    let mut out = std::io::stdout().lock();
    for item in &reply.stdout {
        let line = match (item, person) {
            (Out::Json(v), Some(p)) => human::human(p, v),
            (Out::Json(v), None) => pecia_commands::output::dumps(v),
            (Out::Text(t), _) => t.clone(),
        };
        if writeln!(out, "{line}").is_err() {
            break;
        }
    }
    let _ = out.flush();
    let mut err = std::io::stderr().lock();
    let text = match person {
        Some(_) => reply.stderr.iter().map(|v| human::fatal_line(v) + "\n").collect(),
        None => reply.stderr_text(),
    };
    let _ = err.write_all(text.as_bytes());
    ExitCode::from(reply.exit)
}

fn main() -> ExitCode {
    // args_os, not args: the latter PANICS on an argument that is not UTF-8.
    let raw: Vec<std::ffi::OsString> = std::env::args_os().collect();
    let tainted = raw.iter().any(|a| a.to_str().is_none());
    let argv: Vec<String> = raw.iter().map(|a| a.to_string_lossy().into_owned()).collect();
    let parsed = match args::parse(&argv) {
        Outcome::Run(mut p) => {
            p.tainted = tainted;
            p
        }
        Outcome::Help(text) => {
            print!("{text}");
            return ExitCode::SUCCESS;
        }
        Outcome::Usage(text) => {
            eprint!("{text}");
            return ExitCode::from(2);
        }
    };
    if parsed.command == args::MCP {
        return match pecia_mcp::serve(std::io::stdin().lock(), std::io::stdout().lock()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => render(&Reply::fatal(&format!("the MCP transport failed: {e}")), None),
        };
    }
    render(&pecia_commands::run(&parsed), Some(&parsed))
}
