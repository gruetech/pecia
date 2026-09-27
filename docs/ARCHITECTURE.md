# Architecture

pecia has a shared format and two command-line implementations.

- `pecia_cli.py` is the single-file Python reference. It uses the standard
  library for adopter commands.
- `crates/pecia-core` owns the Rust parser, records, graph and storage rules.
  `pecia-commands` implements commands; `pecia-cli` is the executable;
  `pecia-mcp` exposes the command layer through stdio.
- `spec/format-v2-consolidated.md` describes the format. The JSON record
  schema and vocabulary registry are under `spec/`.
- `tests/test_pecia.py` supplies most process-level behavioral assertions.
  `PECIA_TEST_CLI` selects the executable under test; Rust also has
  crate-local tests.

Writes append to a local log under Git's shared directory by default.
`snapshot` projects that log into `.pecia/work.jsonl` and a head witness
for review in a Git commit. A new clone does not receive the local log
automatically; `sync` and `migrate` are the explicit recovery paths.
See [storage](STORAGE.md) for what a clone must do before writing.

The tool can check a record's shape, dependency graph, and transitions. It
cannot establish that a cited test genuinely supports a disposition or that
a human decision was wise. The [proof tour](PROOF.md) names the observable
contract and its limits.
