//! One module per command group, dispatched by name.

use crate::args::Parsed;
use crate::ctx::Ctx;

pub mod audit;
pub mod board;
pub mod check;
pub mod doctor;
pub mod gantt;
pub mod migrate;
pub mod query;
pub mod remote;
pub mod store_cmds;
pub mod write;

pub fn dispatch(ctx: &Ctx, args: &Parsed) -> Result<u8, String> {
    match args.command {
        "check" => check::run(ctx, args),
        "init" => store_cmds::init(ctx, args),
        "snapshot" => store_cmds::snapshot(ctx, args),
        "ready" => query::ready_cmd(ctx, args),
        "blocked" => query::blocked_cmd(ctx, args),
        "next" => query::next_cmd(ctx, args),
        "graph" => query::graph_cmd(ctx, args),
        "show" => query::show_cmd(ctx, args),
        "gantt" => gantt::gantt(ctx, args),
        "audit" => audit::audit(ctx, args),
        "board" => board::board(ctx, args),
        "migrate" => migrate::migrate(ctx, args),
        "doctor" => doctor::doctor(ctx, args),
        "publish" => remote::publish(ctx, args),
        "sync" => remote::sync(ctx, args),
        "add" => write::add(ctx, args),
        "edit" => write::edit(ctx, args),
        "close" => write::close(ctx, args),
        other => Err(format!("`{other}` is not a command this library runs")),
    }
}
