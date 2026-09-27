# Python and Rust implementation contract

The Rust CLI and the Python reference implement the same on-disk format and
user-facing commands. This document records the boundary a contributor must
preserve. Current test scope and results live in [the proof tour](../docs/PROOF.md).

## 1. Shared behavior

The behavioral suite under `tests/` invokes a subprocess selected by
`PECIA_TEST_CLI`. Assertions inspect command output, exit status, and files.
Run it once against `pecia_cli.py` and once against the compiled Rust
binary. The Rust run skips Python-only introspection arms; crate-local tests
cover Rust internals. An agreement over one suite can still share a wrong
assumption in that suite.

## 2. Format

The current chain uses RFC 8785 canonical JSON over a restricted domain:
integers within the interoperable range, ASCII unique object keys, valid
Unicode, and no lone surrogates. Entry hashes depend on these bytes.
The parser and writer must agree on the domain before a chain is accepted.
U+2028 and U+2029 are data, not line boundaries; log lines are separated
only by LF. See the normative
[format](../spec/format-v2-consolidated.md) and the cross-implementation
corpus tests before changing serialization.

`refs/pecia/log` and committed `.pecia/snapshot.head` name chain
content. A serialization change therefore needs an explicit migration
decision. It cannot be treated as a Rust-only implementation detail.

## 3. Command and storage boundary

The Rust crates separate parsing and graph rules (`pecia-core`), store
access (`pecia-store`), operating-system behavior (`pecia-sys`),
command handling (`pecia-commands`), executable entry
(`pecia-cli`), and MCP transport (`pecia-mcp`). Keep output rendering
at the command boundary: stored record content is data, not a license to
print unbounded prose or terminal control bytes.

Default stores live under Git's shared directory. A pinned
`PECIA_LOG_DIR` is its own store. Both implementations must treat
`snapshot` as a projection step and `sync` or `migrate` as an explicit
fresh-clone hydration step.

## 4. Crates

The installed Rust CLI is one binary at `target/release/pecia` when built
locally. `pecia-core` owns canonical records and diagnostics; no second
format model belongs in a renderer or transport crate. `pecia-commands`
returns structured replies to its callers rather than printing from every
command. `pecia-cli` renders them for terminal use. This keeps storage,
command meaning, and presentation testable separately.

## 5. MCP transport

`pecia mcp` is a stdio server scoped to the repository it runs in. It
exposes command results, including the command exit code and its findings.
The transport is a second renderer of the same command layer, not a
second route to mutate the ledger with different rules. A finding (exit 1)
is a result; a cannot-run (exit 2) is an error. Exit 0 still says
well-formed, never true. The `show` command can return a whole record,
including its prose, so MCP clients must treat record content as untrusted
data.

## 6. Remaining claim boundary

The dual suite is a stronger regression check than either implementation
alone. It does not validate the full composed format specification. That
separate review remains an asserted claim in `claims.yaml`.
