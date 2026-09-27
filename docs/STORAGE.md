# Storage and a fresh clone

The canonical timeline is a local append-only log, by default under Git's
shared directory. `refs/pecia/log` can publish that chain. The tracked
`.pecia/work.jsonl` is a generated projection, with
`.pecia/snapshot.head` witnessing its log head. It is useful in code review;
it is not the live log. After `add`, `edit`, or `close`, run `snapshot`
before committing those tracked files.

Run `pecia doctor` first in a fresh clone. The hook configuration is local
to each clone; files in the repository cannot make a hook active by
themselves. Install the adopter hook as in the README, or run
`pecia doctor --fix` where that command's suggested changes are wanted.

Before the first write in a new clone, hydrate the timeline. If the remote
publishes `refs/pecia/log`, fetch it and run `pecia sync`. If the published
ref is unavailable, `pecia migrate` can reconstruct a timeline from the
committed snapshots. Read each command's output and run `pecia check`
afterward; an exit-0 check says well-formed, not true. Keep the original
development archive's log distinct from the new public repository's log.

These are the default-store paths. `PECIA_LOG_DIR` explicitly pins an
alternate store; see the format specification for its scope and review
location.
