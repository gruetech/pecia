# Contributing

Start with `./pecia_cli.py doctor` in a fresh clone. Enable the local hook
with `git config core.hooksPath dev/hooks` if you intend to commit to this
repository. Check `git branch --show-current` before changing or committing
files; use a separate worktree if another session owns the checkout.

The format and tests are the contract. For a behavior change, update both
implementations or state why one is deliberately different. Run the shared
suite against Python and Rust, then Rust's internal tests:

```sh
python3 dev/test-runner.py -j 4
cargo build --release --locked
PECIA_TEST_CLI="$PWD/target/release/pecia" python3 dev/test-runner.py -j 4
cargo test --release --locked
```

Development scripts with inline dependencies use `uv`; the Python CLI used
by adopters does not. A test fixture created inside this repo must `git init`
its own sandbox or set `PECIA_LOG_DIR`, because timeline discovery walks
upward to the nearest Git directory.

The public `claims.yaml` is canonical for claims about the project. Change
values with `dev/claims-edit.py`, then run `dev/claims-check.py` and the
registered evidence. A new enforcement claim needs a demonstrated bad-input
refusal and a valid control. Recheck README claim bindings with
`dev/prose-check.py`. Exit 0 from a checker means well-formed, not true.

For this repository's own ledger, use `./pecia_cli.py show <id>` and
`./pecia_cli.py next` to read live state. After a write, run
`./pecia_cli.py snapshot` and stage both snapshot files explicitly. Do not
use `git add -A` or `git add .`. Before a commit, write
`.git/EXPECTED_COMMIT` with `branch: <intended-branch>` and each intended
path on its own line. The hook checks the staged set against it.

The [proof tour](docs/PROOF.md) records what the dual implementation suite
observes. The composed v2 format review and the dogfooding report retain
their asserted tier. Please treat those limits as part of the contribution
contract, not as wording to optimize away.
