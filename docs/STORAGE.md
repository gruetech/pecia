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

Before the first write in a new clone, run `pecia sync` to fetch the
published `refs/pecia/log` timeline. A normal Git clone does not fetch that
ref. If it is absent on a remote-backed repository, stop and ask its
maintainer to publish it; `doctor` reports D009 for this case. `migrate`
can reconstruct a local chain from committed snapshots, but using it as an
automatic fallback could diverge from a chain another clone has already
published. After hydration, run `pecia check`; an exit-0 check says
well-formed, not true. Keep the original development archive's log distinct
from the new public repository's log.

These are the default-store paths. `PECIA_LOG_DIR` explicitly pins an
alternate store; see the format specification for its scope and review
location.
