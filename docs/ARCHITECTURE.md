# Codebase map

This is a navigation guide for the public source tree, checked against the
first public commit. It points to implementation and test loci; it is not a
claim that every seam has received an independent architectural review.

## One format, two paths through it

| Concern | Python reference | Rust implementation | Contract |
| --- | --- | --- | --- |
| CLI entry and arguments | [`pecia_cli.py`](../pecia_cli.py), `build_parser`, `main` | [`pecia-cli/src/main.rs`](../crates/pecia-cli/src/main.rs), [`pecia-commands/src/args.rs`](../crates/pecia-commands/src/args.rs) | The shared process suite observes replies and exit codes. |
| Records and canonical text | `pecia_cli.py`, record and canonical helpers | [`pecia-core/src/record.rs`](../crates/pecia-core/src/record.rs), [`canonical.rs`](../crates/pecia-core/src/canonical.rs), [`parse.rs`](../crates/pecia-core/src/parse.rs) | [`spec/format-v2-consolidated.md`](../spec/format-v2-consolidated.md) and [`record.schema.json`](../spec/record.schema.json). |
| Writing and checking | `cmd_add`, `cmd_edit`, `cmd_close`, `cmd_check` | [`pecia-commands/src/cmd/write.rs`](../crates/pecia-commands/src/cmd/write.rs), [`check.rs`](../crates/pecia-commands/src/cmd/check.rs), [`pecia-core/src/check.rs`](../crates/pecia-core/src/check.rs) | The checker tests shape and relations; a green result cannot establish factual truth. |
| Queries and display | `cmd_next`, `cmd_ready`, `cmd_blocked`, `cmd_graph`, `cmd_board` | [`pecia-core/src/query.rs`](../crates/pecia-core/src/query.rs), [`pecia-commands/src/cmd/query.rs`](../crates/pecia-commands/src/cmd/query.rs), [`board.rs`](../crates/pecia-commands/src/cmd/board.rs) | Compare process output across both CLIs. |
| Log, snapshot, and transport | `cmd_snapshot`, `cmd_publish`, `cmd_sync` | [`pecia-store/src/lib.rs`](../crates/pecia-store/src/lib.rs), [`pecia-commands/src/cmd/store_cmds.rs`](../crates/pecia-commands/src/cmd/store_cmds.rs), [`remote.rs`](../crates/pecia-commands/src/cmd/remote.rs) | See [storage](STORAGE.md) before a fresh clone writes. |
| Agent interface | No Python MCP server | [`pecia-mcp/src/lib.rs`](../crates/pecia-mcp/src/lib.rs) | MCP invokes the Rust command layer over stdio. |

The Rust CLI's request path is `pecia-cli` → `pecia-commands` → `pecia-core`
and `pecia-store`. The Python path stays in one standard-library file so an
adopter can copy it without a package install. Both operate on the same
ledger format; neither reads or calls the other at runtime.

## Follow a change

1. Start with the relevant section of the [format specification](../spec/format-v2-consolidated.md)
   and its [claim](../claims.yaml). A new enforcement assertion needs a
   demonstrated bad-input failure before the claim says “enforced.”
2. Make the behavior observable in [`tests/`](../tests/), especially
   [`test_pecia.py`](../tests/test_pecia.py). `PECIA_TEST_CLI` selects the
   executable. The Rust run skips Python-only probes; those skips are not
   passes. Rust internal tests live beside crates and under
   [`crates/pecia-core/tests/`](../crates/pecia-core/tests/).
3. Change the Python reference and Rust implementation as the contract needs.
   The CLIs must agree on bytes, output, and refusal behavior where the
   shared suite claims parity. Update the specification and claims register
   in the same change.
4. Run both implementations against the behavioral suite, Rust internal
   tests, and `dev/claims-check.py`. [CONTRIBUTING.md](../CONTRIBUTING.md)
   lists the commands and the public test scope.

The public `.pecia/work.jsonl` is this repository's **new** forward-looking
ledger. It is deliberately small. Size-sensitive parser and chain regression
tests generate a fixed public corpus in `crates/pecia-core/tests/corpus.rs`;
they do not borrow the private development timeline. The old research and
review archive is held separately.
