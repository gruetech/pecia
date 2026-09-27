---
name: pecia
description: Install or use pecia, the Git-backed issue tracker for developers and AI coding agents, in a repository. Applies to pecia setup, issue updates, and fresh-clone recovery.
---

# Use pecia in a repository

Read `README.md` in the pecia source checkout for installation commands and
`docs/STORAGE.md` there before recovery or migration. The repository using
pecia owns its issue history; keep the source checkout separate from it.

- Inspect the target repository for existing `.pecia` files and a configured
  commit hook before changing either. Integrate an existing hook rather than
  replacing it.
- Choose the Python CLI for a Python 3.11+ environment. Choose the Rust CLI
  when Python is not used or a large issue history is expected. Both use the
  same storage format. The Rust CLI also provides `pecia mcp` over stdio when
  the agent supports MCP tools.
- In a fresh clone, run `doctor`, `sync`, and `check` before writing. If the
  published timeline is missing, report it; do not run `init` or `migrate`
  over an existing projection as an automatic recovery step. Initialize only
  a repository that has no pecia history.
- Use `next`, `show`, and `graph` to read current work. After `add`, `edit`, or
  `close`, run `snapshot` and commit both `.pecia/work.jsonl` and
  `.pecia/snapshot.head`. A clean `check` means structurally well-formed, not
  that a record's evidence or conclusion is true.
- Report the executable chosen, files changed, command results, and whether
  `doctor` found the hook active. Leave any publication or recovery decision
  that needs a human judgment visible to the user.
