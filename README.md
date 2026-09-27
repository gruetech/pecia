# pecia

pecia is a work ledger for a repository: records for defects, tasks, decisions,
and milestones; typed dependencies; a local append-only log; and a snapshot you
can review in Git. It gives a person and an agent the same answer to “what is
next, what blocks it, and what happened to it?”
<!-- claims: reference-implementation, deterministic-queries, v2-storage -->

The Python CLI and Rust CLI are separate implementations. The retained
behavioral suite drives each through its command-line interface. The Rust run
skips Python-specific arms; Rust has its own internal tests. See
[proof and limits](docs/PROOF.md) before treating the two implementations as
independent evidence of correctness.
<!-- claims: reference-implementation, rust-parity -->

## Use it in an existing Git repository

Choose one implementation. Both write the same ledger format. A repository
needs only one executable; development dependencies are not adopter
dependencies.
<!-- claims: stdlib-only, rust-parity -->

### Python: copy one file

Python 3.11 or newer is required. From a clone of this repository, copy
`pecia_cli.py` into the Git repository where you want the ledger:

```sh
cp /path/to/pecia/pecia_cli.py /path/to/your-repo/pecia_cli.py
cd /path/to/your-repo
python3 pecia_cli.py init
python3 pecia_cli.py add --type task --title "First task" --priority 1
python3 pecia_cli.py next
python3 pecia_cli.py snapshot
python3 pecia_cli.py check
```

Commit `pecia_cli.py`, `.pecia/config.yaml`, `.pecia/work.jsonl`, and
`.pecia/snapshot.head`. The log lives under Git's shared directory by default;
`snapshot` writes its reviewable projection. After a new clone, `pecia sync`
can use a published log, or `pecia migrate` can reconstruct one from committed
snapshots. Read the [storage guide](docs/STORAGE.md) before relying on a new
clone for a write.
With `PECIA_LOG_DIR` pinned, the projection sits beside its own log and
nothing from that store appears in the repository snapshot.
<!-- claims: stdlib-only, v2-storage -->

### Rust: install the CLI

Rust and Cargo are needed to build. From a clone of this repository:

```sh
cargo install --path crates/pecia-cli --locked
cd /path/to/your-repo
pecia init
pecia add --type task --title "First task" --priority 1
pecia next
pecia snapshot
pecia check
```

The installed `pecia` binary can replace `python3 pecia_cli.py` in the other
examples. `pecia mcp` exposes the Rust CLI over stdio; it serves the current
repository and does not make the ledger true by returning a green result.
<!-- claims: rust-parity, mcp-server -->

### Turn on the adopter commit gate

The template checks staged ledger content. Install it in each repository that
adopts pecia, then run doctor in each clone to check whether the hook is active:

```sh
mkdir -p dev/hooks
cp /path/to/pecia/templates/pre-commit dev/hooks/pre-commit
chmod +x dev/hooks/pre-commit
git config core.hooksPath dev/hooks
pecia doctor
```

Use `python3 pecia_cli.py doctor` if you chose Python. The hook calls the
available pecia CLI and refuses a staged ledger that fails `check`; `doctor`
reports hook posture, not whether the ledger's claims are true.
<!-- claims: adopter-template, doctor-posture, write-gate -->

## Daily commands

| Command | Purpose |
| --- | --- |
| `pecia add`, `edit`, `close` | Write a record or its disposition. |
| `pecia next`, `ready`, `blocked` | Query the work graph. |
| `pecia show <id>`, `graph`, `board` | Inspect records and their relationships. |
| `pecia snapshot` | Refresh the Git-reviewable projection before commit. |
| `pecia check` | Check structural rules; exit 0 means well-formed, not true. |
| `pecia doctor` | Check this clone's hook and ledger posture. |
| `pecia publish`, `sync`, `migrate` | Carry or reconstruct the local log. |

Commands with machine output support `--json`. Run `pecia <command> --help`
for arguments. The [format specification](spec/format-v2-consolidated.md)
describes the storage and record contract.
<!-- claims: deterministic-queries, query-prose-containment -->

## What the evidence says

The canonical [claims register](claims.yaml) gives each project claim a tier,
date, runnable evidence where available, a red case or stated limit, and
notes. For the **asserted** claims, the register separates observed behavior
from conclusions about the entire format. The v2 format's composed validation is still **asserted** pending a
clean frozen review; the dogfooding history is also **asserted** here because
its underlying observations remain in the separate development archive.
<!-- claims: format-spec, dogfood -->

The public tree contains a curated behavioral suite, Rust internal tests,
format documents, and a short [proof tour](docs/PROOF.md). It begins a new
forward-looking ledger. The old development history and research stay in a
separate archive; they are not prerequisites for installing or using pecia.

## Develop

```sh
python3 dev/test-runner.py -j 4
cargo build --release --locked
PECIA_TEST_CLI="$PWD/target/release/pecia" python3 dev/test-runner.py -j 4
cargo test --release --locked
python3 dev/claims-check.py
```

Development scripts use [uv](https://docs.astral.sh/uv/) for their declared
Python dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test
scope, and the claims workflow. See [architecture](docs/ARCHITECTURE.md) for
the two implementations and their shared format.

## License

Licensed under either [Apache 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at
your option.
