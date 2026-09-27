//! pecia's commands. Each is a function from parsed arguments and a store to
//! a `Reply`; nothing here prints. `pecia-cli` renders a reply to stdout and
//! stderr with an exit code, and `pecia-mcp` renders the same reply as an MCP
//! tool result — neither depends on the other (design/rust-port.md §5).
//!
//! RULE 1: exit 0 means "well-formed," never "true."

pub mod args;
pub mod cmd;
pub mod ctx;
pub mod human;
pub mod output;
/// What the process asks of the operating system, from its own crate so that
/// this one can forbid `unsafe` (pc-2426297cff1f).
pub use pecia_sys as sys;

pub use ctx::{Out, Reply, RULE1};

/// Run one parsed command against the store the working directory resolves to.
pub fn run(parsed: &args::Parsed) -> Reply {
    let store = match pecia_store::Store::resolve() {
        Ok(s) => s,
        Err(e) => return Reply::fatal(&e),
    };
    let ctx = ctx::Ctx::new(store);
    // Totality at the entry point (v2.11): every failure is a cannot-run with
    // its reason, never a panic's exit code.
    let exit = cmd::dispatch(&ctx, parsed).unwrap_or_else(|e| ctx.cannot_run(&e));
    ctx.finish(exit)
}
