# pecia

pecia is a Git-backed issue tracker for developers and AI coding agents.
<!-- claims: product-category -->

The work ledger stores defects, tasks, decisions, and milestones as records
with typed dependencies. Its local log is append-only; `snapshot` produces a
projection you can review in Git. It gives a person and an agent the same
answer to “what is next, what blocks it, and what happened to it?”
<!-- claims: reference-implementation, deterministic-queries, v2-storage -->

The Python CLI and Rust CLI are separate implementations. The retained
behavioral suite drives each through its command-line interface. The Rust run
skips Python-specific arms; Rust has its own internal tests. See
[proof and limits](docs/PROOF.md) before treating the two implementations as
independent evidence of correctness.
<!-- claims: reference-implementation, rust-parity -->

## Give this to a coding agent

Open the Git repository that should own the ledger, then paste this into your
coding agent:

```text
Set up pecia in the current Git repository using https://github.com/gruetech/pecia. First identify the repository root and branch, and inspect any existing .pecia files and pre-commit hook. Read pecia's README and docs/STORAGE.md. Keep the pecia source checkout outside this repository. Prefer the Python CLI if Python 3.11+ is available; use Rust if I request it. If neither toolchain is available, stop and tell me.

For an existing pecia ledger, do not run init. Run doctor, sync, and check before writing; if the published timeline is missing or verification fails, stop and report the finding. For a new ledger, install one CLI, run init, and install the adopter hook. If another hook is already configured, stop and ask before changing it. Run doctor and check. Record the chosen executable and daily workflow in this repository's AGENTS.md without removing existing instructions. Do not invent a sample task, publish, or migrate merely to make setup look complete. Report the files changed, the commands and results, whether the gate is active, and anything that still needs a decision. A clean check establishes structural consistency, not the truth of any record.
```

The instructions below give the agent the exact installation paths and the
fresh-clone recovery boundary. You can also follow them yourself.

## Use it in an existing Git repository

Choose one implementation. Both write the same ledger format. A repository
needs only one executable; development dependencies are not adopter
dependencies.
<!-- claims: stdlib-only, rust-parity -->

Clone pecia outside the repository that will use it. In the commands below,
replace `/path/to/pecia-source` and `/path/to/your-repo` with separate absolute
paths:

```sh
git clone https://github.com/gruetech/pecia /path/to/pecia-source
```

### Python: copy one file

Python 3.11 or newer is required. Copy `pecia_cli.py` into the Git repository
where you want the ledger:

```sh
cp /path/to/pecia-source/pecia_cli.py /path/to/your-repo/pecia_cli.py
cd /path/to/your-repo
python3 pecia_cli.py init
python3 pecia_cli.py check
```

Commit `pecia_cli.py`, `.pecia/config.yaml`, `.pecia/work.jsonl`, and
`.pecia/snapshot.head`. The log lives under Git's shared directory by default;
`snapshot` writes its reviewable projection. After a new clone, run `pecia
sync` to fetch the published log before writing. If no log has been published,
stop and ask the repository maintainer to publish it; a local reconstruction
can diverge from a chain held elsewhere. Read the [storage guide](docs/STORAGE.md)
before relying on a new clone for a write.
With `PECIA_LOG_DIR` pinned, the projection sits beside its own log and
nothing from that store appears in the repository snapshot.
<!-- claims: stdlib-only, v2-storage -->

### Rust: install the CLI

Rust and Cargo are needed to build:

```sh
cargo install --path /path/to/pecia-source/crates/pecia-cli --locked
cd /path/to/your-repo
pecia init
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
cp /path/to/pecia-source/templates/pre-commit dev/hooks/pre-commit
chmod +x dev/hooks/pre-commit
git config core.hooksPath dev/hooks
pecia doctor
```

Use `python3 pecia_cli.py doctor` if you chose Python. The hook calls the
available pecia CLI and refuses a staged ledger that fails `check`; `doctor`
reports hook posture, not whether the ledger's claims are true. Do not replace
an existing repository hook without integrating its checks. Once setup is
verified, add a real task with `add`, run `snapshot` before committing, and
use `next` to see what is ready. An agent should report its changes and
findings rather than manufacture a first record. Commit `dev/hooks/pre-commit`
with the ledger files; `git config core.hooksPath` still has to be set in each
clone.
<!-- claims: adopter-template, doctor-posture, write-gate -->

### Leave instructions for the next agent

Add a short pecia section to the adopting repository's `AGENTS.md`, preserving
its other instructions. Record which executable you installed. This is the
workflow that section should convey:

```md
From the repository root, run pecia with `python3 pecia_cli.py` (or `pecia`
if the Rust CLI is installed). In each fresh clone, run `doctor`, `sync`, and
`check` before writing. If the published timeline is missing, stop and ask;
do not use `init` or `migrate` to reconstruct it automatically. Read live
work with `next` and `show <id>`. After `add`, `edit`, or `close`, run
`snapshot` and commit both `.pecia/work.jsonl` and `.pecia/snapshot.head`.
`check` reports structural consistency; it does not establish that a record's
claim is true.
```

The installed command and hook are local to each clone. Keep this note with
the adopter repository so future agents have the same starting point.

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
uv run --python 3.12 --script dev/test-runner.py -j 4
cargo build --release --locked
PECIA_TEST_CLI="$PWD/target/release/pecia" uv run --python 3.12 --script dev/test-runner.py -j 4
cargo test --release --locked
uv run --python 3.12 --script dev/claims-check.py
```

Development scripts use [uv](https://docs.astral.sh/uv/) for their declared
Python dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test
scope, and the claims workflow. See [architecture](docs/ARCHITECTURE.md) for
the two implementations and their shared format.

## License

Licensed under either [Apache 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT), at
your option.
