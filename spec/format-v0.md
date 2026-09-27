# pecia format — v0 draft

**Status:** 📖 pre-scholiast sketch (2026-07-25). Expected to change at M1.
Nothing here is load-bearing yet; disagreements between this file and the M1
conspectus are resolved in favor of evidence.

## Storage

- `.pecia/work.jsonl` — one JSON object per line, UTF-8, LF.
- `.pecia/config.yaml` — repo-local configuration (statuses, types, prefixes).
- Updates **append** a full revised record; readers resolve by *last
  occurrence per id wins* (so `merge=union` across branches degrades to
  duplicate lines, never to conflict markers). `pecia compact` rewrites the
  file keeping resolved heads — run deliberately; git history retains the
  rest. (Open question for M1: snapshot-line-with-dedup vs append-only event
  log; seeds vs grite. v0 picks snapshot-lines for greppability.)
- `.gitattributes`: `.pecia/work.jsonl merge=union`.

## Record (v0 shape)

```json
{
  "id": "pc-a3f8",
  "type": "defect | task | decision | milestone",
  "title": "…",
  "status": "open | ready | in-progress | blocked | done | dropped | superseded",
  "priority": 0,
  "created": "2026-07-25",
  "updated": "2026-07-25",
  "edges": {
    "blocks": ["pc-…"],
    "parent": "pc-…",
    "discovered_from": "pc-…",
    "caused_by": "pc-…",
    "validates": "pc-…",
    "supersedes": "pc-…"
  },
  "disposition": "required iff status is terminal (done | dropped | superseded)",
  "evidence": "optional: claims.yaml id or runnable command that licenses 'done'",
  "owner": "human | agent identity string",
  "labels": [],
  "body": "markdown, optional"
}
```

- **IDs:** content-derived hash with `pc-` prefix, adaptive length (collision
  math per beads). Never sequential (Backlog.md's cross-machine collision
  lesson). No wall-clock inside the hash.
- **Blocking semantics:** only `blocks` and `parent` gate `ready`. The rest
  are custody/lineage annotations.
- **Decisions:** records of `type: decision` carry MADR-style fields in `body`
  frontmatter and use `supersedes` for head-resolution; the checker enforces a
  single active head per decision key.
- **Timestamps are data, not identity.** Dates (not times) by default.

## Invariants (the checker's contract, v0)

1. Every line parses; every record carries the required fields for its type.
2. Edge targets exist (post-dedup); unknown edge names rejected.
3. Blocking subgraph is acyclic.
4. Status transitions are legal per the configured state machine.
5. Terminal status ⇒ non-empty `disposition` ("nothing leaves silently").
6. `type: defect` + `status: done` ⇒ non-empty `evidence`.
7. Frontier-vs-rot: an edge to a nonexistent id is an error **unless** the
   target is declared in the record's `planned` list — intentional frontier is
   declared, never ambient (adr-graph's dispositioning).
8. Exit code 0 clean / 1 any violation; diagnostics as one JSON object per
   finding on stdout with `{severity, code, id, message}` (OpenSpec's
   envelope discipline).

## Queries (deterministic, v0)

- `ready` — open items whose blocking closure is fully terminal.
- `blocked` — open items with at least one non-terminal blocker, each listed.
- `next` — `ready` ordered by (priority, oldest-created, id) — total order,
  no ties, no randomness.
- `graph` / `gantt` — projections (Mermaid), generated only.
