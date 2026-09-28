---
name: pecia
description: Install or use pecia, the Git-backed issue tracker for developers and AI coding agents, in a repository. Applies to pecia setup, issue updates, and fresh-clone recovery.
---

## Installing pecia

Inspect the target Git repository for an existing pecia installation, `.pecia`
files, and a configured commit hook before changing them. Keep the pecia
source checkout separate from the repository that will use it. For a new
installation, use Rust when Cargo is available. Use the Python 3.11+ file if
the user chooses it or Cargo is unavailable; do not replace an existing
installation just to change implementations.

Clone the source, then install one implementation:

```sh
git clone https://github.com/gruetech/pecia /path/to/pecia-source
cargo install --path /path/to/pecia-source/crates/pecia-cli --locked
# Or, for Python:
cp /path/to/pecia-source/pecia_cli.py /path/to/your-repo/pecia_cli.py
```

Run commands from the target repository. Use `pecia` for Rust or
`python3 pecia_cli.py` for Python. Run `init` only if the target has no pecia
history, then run `check`. If an agent supports MCP, configure it to start
`pecia mcp` with the target repository as its working directory.

For a new adoption, integrate the source template at
`templates/pre-commit` into the target repository's hook. Do not overwrite an
existing hook. With no existing hook, use:

```sh
mkdir -p dev/hooks
cp /path/to/pecia-source/templates/pre-commit dev/hooks/pre-commit
chmod +x dev/hooks/pre-commit
git config core.hooksPath dev/hooks
```

Run `doctor` to confirm the hook is active. Commit `.pecia/config.yaml`, `.pecia/work.jsonl`,
`.pecia/snapshot.head`, the hook, and `pecia_cli.py` if copied. Record the
chosen command and fresh-clone procedure in the target repository's
`AGENTS.md` without replacing its other instructions.

In a fresh clone of an existing ledger, run `doctor`, `sync`, and `check`
before writing. If `doctor` finds the hook inactive, integrate and enable it
in that clone. If the published `refs/pecia/log` timeline is missing, ask
the maintainer to publish it. Do not automatically run `init` or `migrate`
over an existing projection; that can create a divergent history.

## Using pecia

Use `next`, `show <id>`, and `graph` to read live work. Use `add`, `edit`, and
`close` to change it. After a write, run `snapshot` and commit both
`.pecia/work.jsonl` and `.pecia/snapshot.head`. When other clones need the
updated timeline, run `publish` to the configured Git remote and check its
reported read-back; a normal Git push does not publish `refs/pecia/log`.

Run `check` to catch structural errors. Exit 0 means well-formed, not that a
record's evidence or conclusion is true. Report the executable used, files
changed, command results, and hook status. Leave missing-history recovery
and other judgment calls visible to the user.
