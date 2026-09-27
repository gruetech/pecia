# beads-import — the reference adapter

Converts a [beads](https://github.com/steveyegge/beads) JSONL export into
pecia records. This is the worked example for [ADAPTERS.md](../../ADAPTERS.md)
(decision pc-8a27: adapters live outside the core; beads import is the
reference, not a built-in — its JSONL export is the category's common exit
door). beads is good software with a different center of gravity; this
adapter exists so work can move, in either direction of adoption.

## Use

```bash
# from the root of the repo you are importing INTO:
bd export > imports/beads.jsonl                # or however you obtain it
adapters/beads-import/beads2pecia.py imports/beads.jsonl --out out.jsonl
./pecia_cli.py check --ledger out.jsonl        # loop until exit 0
# then land it through the write path (add/edit/close) — see ADAPTERS.md,
# "Landing an import in a live ledger". Under v2, .pecia/work.jsonl is a
# generated snapshot and is never appended to (E015 catches the fork).
./pecia_cli.py check && ./pecia_cli.py audit
```

Commit `imports/beads.jsonl` and this adapter directory into that repo:
closed records carry **provenance evidence** —
`python3 adapters/beads-import/beads2pecia.py --verify-closed imports/beads.jsonl <id>`
— whose exit 0 licenses "closed in source" and nothing more (rule 1). If
the export leaves the repo, the evidence stops being runnable and `audit`
will say so; that is the designed pressure, not a defect.

## Mapping summary

The complete, binding mapping (including every lossy step and how it is
declared) is documented in [`beads2pecia.py`](beads2pecia.py)'s module
docstring, verified against the beads source (steveyegge/beads @ main,
2026-07-31). Highlights:

- `bug → defect`, `epic → milestone`, `task → task`; `feature`/`chore` →
  `task` + a `beads:type:*` label.
- `blocked` demotes to `open` — the dependency edges carry the truth and
  pecia computes blockedness. `deferred` → `open` at priority 4 (out of
  `next`). `pinned` beads are context, not work: skipped, counted.
- beads stores dependencies on the *dependent*; pecia's `blocks` edge
  lives on the *blocker*. The adapter inverts. `parent-child`,
  `discovered-from`, `caused-by`, `validates`, `supersedes` map to the
  matching edges; `related`/`tracks`/`conditional-blocks`/`waits-for`
  have no exact pecia semantics and are **declared in the body, never
  mapped** — a wrong edge corrupts scheduling; a declared absence does
  not.
- beads `closed` does not distinguish done from dropped: everything
  closed imports as `done` with the source's `close_reason` verbatim in
  the disposition (or an explicit note that none was recorded). If your
  history closes a lot of wontfix, post-edit those records — the import
  will not guess.
- Deterministic throughout: `pc-<sanitized source id>`, dates from the
  source, output sorted — re-running over an unchanged export is
  byte-identical. A repeated `(id, rev)` is an E002 **error** since v2,
  identical or divergent alike (E010 was deleted with the merge algebra),
  so `out.jsonl` is the validation artifact and a changed source lands in
  a live ledger as a label-keyed reconcile through the write path — see
  ADAPTERS.md, "Reconciling a changed source".

## Fixture

`fixtures/sample-export.jsonl` exercises every branch: the `_schema`
header, a memory record, a pinned bead, all four mapped custody types, a
blocks inversion, a dangling target, a scalar-edge collision, an unmapped
`related` link, deferred/hooked/blocked statuses, and closures with and
without a close reason. `tests/test_pecia.py::BeadsAdapter` runs the
adapter over it and holds the output to `check` exit 0, byte-idempotency,
and the changed-source re-import trap, which E002 catches at v2 (a
repeated `(id, rev)` is an error whether identical or divergent; E010
was deleted with the merge algebra).
