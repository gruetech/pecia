# pecia contributor and agent brief

Read the README for use, CONTRIBUTING.md for development, and the live
pecia ledger (`./pecia_cli.py next`) for current work. This file governs
work in the public product repository.

- Check `git branch --show-current` on arrival. Prefer a dedicated worktree
  when someone else is using a checkout.
- `claims.yaml` is canonical for project claims. Change values through
  `dev/claims-edit.py`; update claim-bearing prose in the same commit.
  A new enforcement claim needs a demonstrated bad-input refusal.
- An exit-0 checker result means well-formed, never true.
- Do not run `git add -A` or `git add .`. Stage named paths. Before
  committing, write `.git/EXPECTED_COMMIT` with the intended
  `branch: <name>` and one intended path or glob per line.
- Run `./pecia_cli.py doctor` first in a fresh clone. Hook enablement is
  local: `git config core.hooksPath dev/hooks`. A repository file cannot
  prove the hook is active.
- After any ledger write, run `./pecia_cli.py snapshot` and stage
  `.pecia/work.jsonl` and `.pecia/snapshot.head`. Read live records with
  `show`, `graph`, or `next`, not the last snapshot.
- Python development scripts use uv. The adopter-facing Python CLI uses
  only Python's standard library. Git fixtures inside this tree must init
  their own repo or set `PECIA_LOG_DIR` explicitly.
- Use a different model vendor lineage for an adversarial review when one
  is available. Record machine-authored ledger owners as stable,
  vendor-qualified strings.

The public repo starts a new ledger for forward-looking work. The complete
development archive remains separate. Its research and review transcripts
are not silently treated as evidence present in this checkout.
